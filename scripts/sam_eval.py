"""Run the SAM 2.1 vision instrument over every month of a measured water series and grade it against the gauge.

    uv run python scripts/sam_eval.py runs/lake-mead/water-sop-1-764ec8

Writes sam.json next to the series: per-month SAM area and its overlap with the instrument's outline.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

from orbital.instruments import sam
from orbital.instruments.water import iou
from orbital.studies.analysis import compare_to_elevation, usable_points
from orbital.truth.usbr import lake_mead_monthly


def main(series_path: Path, target: str = "lake-mead"):
    points = usable_points(json.loads((series_path / "series.json").read_text()))
    rows, t0 = [], time.time()
    for p in points:
        h, w = p["mask_shape"]
        mine = np.unpackbits(np.load(series_path / "masks" / f"{p['date']}.npy"))[: h * w].reshape(h, w).astype(bool)
        out = sam.outline_month(target, p["date"], series_path / p["chip"], (h, w), p["pixel_area_m2"] ** 0.5)
        rows.append({"date": p["date"], "usable": True, "area_km2": out["area_km2"], "instrument_km2": round(p["area_km2"], 2),
                     "iou_with_instrument": round(iou(out["mask"], mine), 3), "sam_score": out["sam_score"]})
        print(rows[-1], flush=True)
    truth = lake_mead_monthly()
    result = {"model": sam.MODEL_ID, "prompts": sam.prompts(target)["source"], "months": len(rows),
              "seconds_per_month": round((time.time() - t0) / max(len(rows), 1), 2),
              "sam_vs_gauge": compare_to_elevation(rows, truth),
              "instrument_vs_gauge": compare_to_elevation(points, truth), "rows": rows}
    for k in ("sam_vs_gauge", "instrument_vs_gauge"):
        result[k].pop("pairs", None)
    (series_path / "sam.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
