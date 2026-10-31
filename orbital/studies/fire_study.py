"""Measure one wildfire's burn scar: pick clear pre/post scenes, compute dNBR, render evidence."""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from pyproj import Transformer
from rasterio.features import rasterize
from shapely.ops import transform as shp_transform
from skimage.segmentation import find_boundaries

from orbital.config import RUNS_DIR
from orbital.imagery import render
from orbital.imagery.load import load_day
from orbital.imagery.stac import group_by_day, search_scenes, tile_cloud_cover
from orbital.instruments.burn import FireSOP, measure_burn
from orbital.truth.fires import perimeter

MAX_TRIES = 4


def fire_dir(name: str, state: str, sop: FireSOP) -> Path:
    return RUNS_DIR / "fires" / f"{name.lower().replace(' ', '-')}-{state.lower()}" / sop.key()


def _aoi(bbox, buffer_km):
    lat = (bbox[1] + bbox[3]) / 2
    dlat, dlon = buffer_km / 111.0, buffer_km / (111.0 * math.cos(math.radians(lat)))
    return [bbox[0] - dlon, bbox[1] - dlat, bbox[2] + dlon, bbox[3] + dlat]


def _candidates(aoi, start: date, end: date):
    days = group_by_day(search_scenes(aoi, str(start), str(end), max_cloud=30))
    return sorted(days.items(), key=lambda kv: (-len(kv[1]), tile_cloud_cover(kv[1])))


def _perimeter_mask(ds, geom) -> np.ndarray:
    """Rasterize the official perimeter (lon/lat) onto the scene's UTM grid."""
    epsg = ds.odc.crs.epsg
    to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform
    g = shp_transform(to_utm, geom)
    gb = ds.odc.geobox
    return rasterize([(g, 1)], out_shape=gb.shape, transform=gb.affine, fill=0, dtype="uint8").astype(bool)


def severity_rgb(dnbr: np.ndarray) -> np.ndarray:
    """dNBR → USGS-style severity colours (unburned dark green … high severity dark red)."""
    stops = [(-0.1, (34, 70, 40)), (0.1, (60, 110, 60)), (0.27, (250, 220, 80)), (0.44, (245, 140, 40)),
             (0.66, (220, 40, 30)), (1.0, (110, 10, 20))]
    d = np.nan_to_num(dnbr, nan=-0.1)
    out = np.zeros(d.shape + (3,), np.float32)
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        m = (d >= a) & (d < b)
        f = ((d - a) / (b - a))[m][:, None]
        out[m] = np.array(ca) * (1 - f) + np.array(cb) * f
    out[d >= 1.0] = stops[-1][1]
    out[d < -0.1] = stops[0][1]
    return out.astype(np.uint8)


def run_fire(name: str, state: str, sop: FireSOP = FireSOP(), use_cache: bool = True) -> dict:
    out = fire_dir(name, state, sop)
    cache = out / "result.json"
    if use_cache and cache.exists():
        return json.loads(cache.read_text())
    if not use_cache:  # independent recompute: same procedure, separate outputs, never overwrites the lab's
        out = out / "recompute"
        out.mkdir(parents=True, exist_ok=True)
        cache = out / "result.json"
    fire = perimeter(name, state)
    aoi = _aoi(fire["bbox"], sop.buffer_km)
    discovered = date.fromisoformat(fire["discovered"])
    post_start = date.fromisoformat(fire["contained"]) if fire["contained"] else date.today() - timedelta(days=sop.post_window_days)
    pre_c = _candidates(aoi, discovered - timedelta(days=sop.pre_window_days), discovered - timedelta(days=1))
    post_c = _candidates(aoi, post_start, min(date.today(), post_start + timedelta(days=sop.post_window_days)))

    rejected, chosen = [], None
    for pre_day, pre_items in pre_c[:MAX_TRIES]:
        pre = load_day(pre_items, aoi, resolution=sop.resolution_m)
        mask = _perimeter_mask(pre, fire["geometry"])
        for post_day, post_items in post_c[:MAX_TRIES]:
            post = load_day(post_items, aoi, resolution=sop.resolution_m)
            m = measure_burn(pre, post, mask, fire["official_km2"], sop)
            if m.usable:
                chosen = (pre_day, post_day, pre, post, mask, m)
                break
            rejected.append({"pre": str(pre_day), "post": str(post_day), "issues": m.issues})
        if chosen:
            break
    if not chosen:
        return {"fire": {k: v for k, v in fire.items() if k != "geometry"}, "usable": False, "rejected": rejected}

    pre_day, post_day, pre, post, mask, m = chosen
    edge = find_boundaries(mask, mode="outer")
    post_rgb = render.true_color(post)
    overlay = render.overlay(post_rgb, m.burned, color=(255, 90, 40), alpha=0.35, outline=False)
    overlay[edge] = (255, 255, 255)
    sev = severity_rgb(m.dnbr)
    sev[edge] = (255, 255, 255)
    pre_rgb = render.true_color(pre)
    pre_rgb[edge] = (255, 255, 255)
    title = f"{fire['name']} Fire ({fire['state']}) · {post_day}"
    files = {
        "overlay": render.save_png(render.caption(render.fit(overlay, 1100), title,
                                                  f"orange = burned (dNBR > {sop.dnbr_threshold}) · white = official perimeter"), out / "overlay.png"),
        "severity": render.save_png(render.caption(render.fit(sev, 1100), f"{fire['name']} Fire · burn severity (dNBR)",
                                                   "green unburned → yellow low → orange moderate → red high"), out / "severity.png"),
        "pre": render.save_png(render.caption(render.fit(pre_rgb, 1100), f"{fire['name']} · before · {pre_day}"), out / "pre.png"),
        "post": render.save_png(render.caption(render.fit(post_rgb, 1100), f"{fire['name']} · after · {post_day}"), out / "post.png"),
    }
    result = {
        "fire": {k: v for k, v in fire.items() if k != "geometry"},
        "sop": sop.version,
        "usable": True,
        "pre_date": str(pre_day), "post_date": str(post_day),
        "measurement": m.summary() | {"burned_vs_official_pct": round(m.burned_km2 / fire["official_km2"] * 100, 1)
                                      if fire["official_km2"] else None},
        "rejected_before": rejected,
        "evidence": {k: str(p.relative_to(RUNS_DIR)) for k, p in files.items()},
    }
    cache.write_text(json.dumps(result, indent=1))
    return result
