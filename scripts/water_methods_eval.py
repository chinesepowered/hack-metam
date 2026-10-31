"""Water Lab methods study: which water-detection variant best tracks the official gauge?

Loads each date once, measures it with every variant, and scores each variant by how well
area follows USBR elevation (Pearson r and scatter around a quadratic area–elevation fit).
"""
import json
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import numpy as np

from orbital.config import RUNS_DIR
from orbital.imagery import indices
from orbital.imagery.load import SCL_WATER, load_day
from orbital.imagery.stac import search_scenes
from orbital.instruments.water import keep_water_bodies
from orbital.targets import WATER_TARGETS
from orbital.truth.usbr import interpolate, lake_mead_monthly

warnings.filterwarnings("ignore")
BBOX = WATER_TARGETS["lake-mead"]["bbox"]


def variants(ds):
    px = ds.attrs["pixel_area_m2"]
    valid = ds.scl.values != 0
    g, n, s1, s2, b = (ds[k].values for k in ("green", "nir", "swir16", "swir22", "blue"))
    m = indices.mndwi(ds)
    awei = 4 * (g - s1) - (0.25 * n + 2.75 * s2)
    out = {}
    for name, raw in {
        "mndwi>0": m > 0,
        "mndwi>0|dark.015": (m > 0) | ((g < .015) & (n < .015) & (s1 < .03)),
        "mndwi>0|dark.025": (m > 0) | ((g < .025) & (n < .025) & (s1 < .05)),
        "mndwi>0|nir<.02": (m > 0) | (n < 0.02) & (s1 < 0.03),
        "awei_nsh>0": awei > 0,
        "esa_scl": ds.scl.values == SCL_WATER,
    }.items():
        out[name] = float(keep_water_bodies(np.nan_to_num(raw).astype(bool) & valid, px, 1.0).sum() * px / 1e6)
    return out


def main(series_path):
    pts = [p for p in json.loads(open(series_path).read()) if p.get("usable") and p.get("date")]
    truth = lake_mead_monthly()

    def work(p):
        items = search_scenes(BBOX, p["date"], p["date"], max_cloud=100)
        return p["date"], variants(load_day(items, BBOX, resolution=40))

    with ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(work, pts))
    names = list(rows[0][1])
    h = np.array([interpolate(truth, date.fromisoformat(d)) for d, _ in rows], dtype=float)
    ok = np.isfinite(h)
    report = {}
    for name in names:
        a = np.array([v[name] for _, v in rows])[ok]
        coef = np.polyfit(h[ok], a, 2)
        rmse = float(np.sqrt(np.mean((a - np.polyval(coef, h[ok])) ** 2)))
        report[name] = {"pearson_r": round(float(np.corrcoef(h[ok], a)[0, 1]), 4), "rmse_km2": round(rmse, 1),
                        "mean_km2": round(float(a.mean()), 1)}
    out = RUNS_DIR / "lake-mead" / "methods_eval.json"
    out.write_text(json.dumps({"n": int(ok.sum()), "variants": report, "rows": rows}, indent=1))
    for k, v in sorted(report.items(), key=lambda kv: -kv[1]["pearson_r"]):
        print(f"{k:22s} r={v['pearson_r']:.3f}  scatter=±{v['rmse_km2']:5.1f} km²  mean={v['mean_km2']}")


if __name__ == "__main__":
    main(sys.argv[1])
