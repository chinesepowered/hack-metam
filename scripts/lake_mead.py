"""Water Lab baseline: Lake Mead month by month under a given SOP, graded against USBR records.

    uv run python scripts/lake_mead.py [sop: 1|2] [start] [end]
"""
import dataclasses
import json
import sys

from orbital.imagery import render
from orbital.instruments.water import WaterSOP
from orbital.studies.analysis import compare_to_elevation, plot_series, usable_points
from orbital.studies.water_series import run_series, series_dir
from orbital.targets import WATER_TARGETS, data_dir
from orbital.truth.usbr import lake_mead_monthly

SOPS = {
    "1": WaterSOP(),
    # What the methods study recommends: count near-black over-corrected water (r 0.92 vs gauge).
    "2": dataclasses.replace(WaterSOP(), version="water-sop-2", dark_water_rule=True, dark_reflectance=0.025),
}


def main(sop="1", start="2018-01-01", end="2026-10-31"):
    sop = SOPS[sop]
    t = WATER_TARGETS["lake-mead"]
    pts = run_series(t["name"], t["bbox"], start, end, data_dir("lake-mead"), sop=sop, workers=4,
                     on_point=lambda p: print(p["month"], p.get("date"), p.get("area_km2"), flush=True))
    out = series_dir(data_dir("lake-mead"), sop)
    truth = lake_mead_monthly()
    grade = compare_to_elevation(pts, truth)
    (out / "grade.json").write_text(json.dumps(grade, indent=1))
    plot_series(pts, truth, grade, f"Lake Mead from orbit vs USBR gauge · {sop.version}", out / "chart.png")
    render.frames_to_mp4([out / p["evidence"] for p in usable_points(pts)], out / "timelapse.mp4", fps=6)
    print(json.dumps({k: v for k, v in grade.items() if k not in ("pairs", "fit_coefficients")}, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
