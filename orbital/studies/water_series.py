"""Measure a water body month by month; every data point keeps the image it came from."""
from __future__ import annotations

import fcntl
import json
import shutil
import warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image
from skimage.transform import resize

from orbital.imagery import render
from orbital.imagery.load import load_day
from orbital.imagery.stac import group_by_day, search_scenes, tile_cloud_cover
from orbital.instruments.water import GATE_FIELDS, WaterSOP, measure_water

warnings.filterwarnings("ignore", module="rasterio")
warnings.filterwarnings("ignore", category=DeprecationWarning)

MAX_TRIES_PER_MONTH = 3


def monthly_candidates(bbox, start: str, end: str, max_tile_cloud: float = 10.0) -> dict[tuple[int, int], list]:
    """For each month, that month's days ordered clearest-first (scene-level cloud cover)."""
    days = group_by_day(search_scenes(bbox, start, end, max_cloud=max_tile_cloud))
    months: dict[tuple[int, int], list] = defaultdict(list)
    for d, items in days.items():
        months[(d.year, d.month)].append((d, items))
    # Most tiles first (edge-of-swath days cover only part of the area; the SOP's no-data gate
    # rejects those anyway), then clearest.
    return {k: sorted(v, key=lambda kv: (-len(kv[1]), tile_cloud_cover(kv[1]))) for k, v in sorted(months.items())}


def measure_month(bbox, candidates, sop: WaterSOP, out_dir: Path, label: str, skip=frozenset()) -> dict | None:
    """Try the clearest days until one passes the SOP's quality gates; record every rejection."""
    rejected = []
    for d, items in [c for c in candidates if str(c[0]) not in skip][:MAX_TRIES_PER_MONTH]:
        ds = load_day(items, bbox, resolution=sop.resolution_m)
        m = measure_water(ds, sop)
        if not m.usable:
            rejected.append({"date": str(d), "issues": m.issues})
            continue
        rgb = render.true_color(ds)
        chip = render.save_png(render.fit(rgb, 1100), out_dir / "chips" / f"{d}.png")
        lo, hi = m.band_km2
        ev = render.caption(render.fit(render.overlay(rgb, m.mask), 1100), f"{label} · {d}",
                            f"{m.area_km2:.1f} km² water (range {lo:.0f}–{hi:.0f} across methods) · {sop.version}")
        evidence = render.save_png(ev, out_dir / "evidence" / f"{d}.png")
        (out_dir / "masks").mkdir(parents=True, exist_ok=True)
        np.save(out_dir / "masks" / f"{d}.npy", np.packbits(m.mask))
        return m.summary() | {"chip": str(chip.relative_to(out_dir)), "evidence": str(evidence.relative_to(out_dir)),
                              "mask_shape": list(m.mask.shape), "rejected_before": rejected, "items": ds.attrs["items"]}
    return {"date": None, "usable": False, "sop": sop.version, "rejected_before": rejected} if rejected else None


def _parent(out_dir: Path, sop: WaterSOP):
    """The newest earlier series measured the same way (only its gates differ), whose months can be reused."""
    best = None
    for d in out_dir.parent.glob("water-sop-*"):
        if d == out_dir or not (d / "sop.json").exists() or not (d / "series.json").exists():
            continue
        old = WaterSOP(**json.loads((d / "sop.json").read_text()))
        if old.measure_params() == sop.measure_params():
            n = int(old.version.rsplit("-", 1)[1])
            if best is None or n > best[0]:
                best = (n, d, old)
    return best[1:] if best else None


def _reuse(point: dict, src: Path, out_dir: Path, sop: WaterSOP, label: str) -> dict:
    """Carry a month over from the parent series and re-caption its evidence under the new SOP."""
    d = point["date"]
    for sub, ext in (("chips", ".png"), ("masks", ".npy")):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / sub / f"{d}{ext}", out_dir / sub / f"{d}{ext}")
    rgb = np.asarray(Image.open(out_dir / "chips" / f"{d}.png").convert("RGB"))
    h, w = point["mask_shape"]
    mask = np.unpackbits(np.load(out_dir / "masks" / f"{d}.npy"))[: h * w].reshape(h, w).astype(bool)
    mask = resize(mask.astype(np.float32), rgb.shape[:2], order=0) > 0.5
    lo, hi = point["band_km2"]
    ev = render.caption(render.overlay(rgb, mask), f"{label} · {d}",
                        f"{point['area_km2']:.1f} km² water (range {lo:.0f}–{hi:.0f} across methods) · {sop.version}")
    evidence = render.save_png(ev, out_dir / "evidence" / f"{d}.png")
    return point | {"sop": sop.version, "reused_from": src.name, "evidence": str(evidence.relative_to(out_dir)),
                    "chip": f"chips/{d}.png"}


def series_dir(base_dir: Path, sop: WaterSOP) -> Path:
    """Results live per SOP (version + parameter hash), so a new SOP never mixes with old measurements."""
    return base_dir / sop.key()


def run_series(label: str, bbox, start: str, end: str, base_dir: Path, sop: WaterSOP = WaterSOP(),
               workers: int = 3, on_point: Callable[[dict], None] | None = None) -> list[dict]:
    out_dir = series_dir(base_dir, sop)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Labs run in parallel and may hit the same series: one writer at a time, across threads and processes.
    with (out_dir / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _run_series_locked(label, bbox, start, end, out_dir, sop, workers, on_point)


def _run_series_locked(label, bbox, start, end, out_dir: Path, sop: WaterSOP, workers, on_point) -> list[dict]:
    cache = out_dir / "series.json"
    (out_dir / "sop.json").write_text(json.dumps(asdict(sop)))
    done = {p["month"]: p for p in json.loads(cache.read_text())} if cache.exists() else {}
    months = monthly_candidates(bbox, start, end)
    todo = {k: v for k, v in months.items() if f"{k[0]}-{k[1]:02d}" not in done}

    # A new SOP that only changes quality gates re-measures incrementally: a month whose earlier scene
    # still passes is reused; otherwise that scene (and, if every gate is at least as strict, the scenes
    # rejected before) is skipped and the next clear date is measured.
    skips: dict[tuple[int, int], set[str]] = {}
    parent = _parent(out_dir, sop)
    if parent:
        src, old = parent
        stricter = all(getattr(sop, g) <= getattr(old, g) for g in GATE_FIELDS)
        prior = {p["month"]: p for p in json.loads((src / "series.json").read_text())}
        for key in list(todo):
            pp = prior.get(f"{key[0]}-{key[1]:02d}")
            if pp is None:
                continue
            earlier = {r["date"] for r in pp.get("rejected_before", [])} if stricter else set()
            if pp.get("usable") and sop.passes(pp):
                done[pp["month"]] = _reuse(pp, src, out_dir, sop, label)
                del todo[key]
            elif not pp.get("usable") and stricter:
                done[pp["month"]] = pp | {"sop": sop.version, "reused_from": src.name}
                del todo[key]
            else:
                skips[key] = earlier | ({pp["date"]} if pp.get("date") else set())
        cache.write_text(json.dumps(sorted(done.values(), key=lambda p: p["month"]), indent=1))

    def work(key):
        res = measure_month(bbox, todo[key], sop, out_dir, label, skip=skips.get(key, frozenset()))
        return key, res

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, k) for k in todo]
        for f in as_completed(futures):
            (y, mo), res = f.result()
            if res is None:
                continue
            point = {"month": f"{y}-{mo:02d}"} | res
            done[point["month"]] = point
            cache.write_text(json.dumps(sorted(done.values(), key=lambda p: p["month"]), indent=1))
            if on_point:
                on_point(point)
    return sorted(done.values(), key=lambda p: p["month"])


def to_date(s: str) -> date:
    return date.fromisoformat(s)
