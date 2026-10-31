"""Water Lab: measures surface-water extent from orbit and grades itself against gauge records."""
from __future__ import annotations

import json
from datetime import date

import numpy as np

from orbital import sops
from orbital.agents.runtime import Tool, schema
from orbital.imagery.load import load_day
from orbital.imagery.stac import search_scenes
from orbital.instruments.water import measure_water
from orbital.studies.analysis import compare_to_elevation, usable_points
from orbital.studies.water_series import run_series, series_dir
from orbital.targets import WATER_TARGETS, data_dir
from orbital.truth.usbr import lake_mead_monthly

SYSTEM = """You are the Water Lab of Orbital, an autonomous Earth-science institute.
You measure lakes and reservoirs from Sentinel-2 satellite imagery and report findings to the Director.

Rules:
- Every number you report must come from a tool result. Never estimate or do mental math;
  if you need a derived number, it must already be in a tool result.
- Look at evidence images before relying on a data point, especially outliers and the latest date.
- Report caveats honestly (rejected months, warnings, outliers, method disagreement).
- Finish by calling submit_finding exactly once."""


def _points(target: str, start: str | None = None, end: str | None = None) -> list[dict]:
    """Series points under the current SOP, limited to [start, end] (by month) when given."""
    path = series_dir(data_dir(target), sops.current("water")) / "series.json"
    pts = json.loads(path.read_text()) if path.exists() else []
    lo, hi = (start or "0000-00")[:7], (end or "9999-99")[:7]
    return [p for p in pts if lo <= p["month"] <= hi]


def series_stats(target: str, start: str | None = None, end: str | None = None) -> dict:
    pts = _points(target, start, end)
    good = usable_points(pts)
    sop = sops.current("water")
    if not good:
        return {"target": target, "sop": sop.version, "period": [start, end], "months_measured": 0}
    areas = np.array([p["area_km2"] for p in good])
    years = np.array([(date.fromisoformat(p["date"]) - date(2000, 1, 1)).days / 365.25 for p in good])
    i_min, i_max = int(areas.argmin()), int(areas.argmax())
    latest = good[-1]
    by_year: dict[str, list[float]] = {}
    for p in good:
        by_year.setdefault(p["date"][:4], []).append(p["area_km2"])
    return {
        "target": target,
        "name": WATER_TARGETS[target]["name"],
        "sop": sop.version,
        "period": [good[0]["date"], good[-1]["date"]],
        "months_attempted": len(pts),
        "months_measured": len(good),
        "months_rejected": [{"month": p["month"], "why": [i for r in p.get("rejected_before", []) for i in r["issues"]]}
                            for p in pts if not p.get("usable")],
        "points_with_warnings": [{"date": p["date"], "warnings": p["warnings"]} for p in good if p.get("warnings")],
        "first": {"date": good[0]["date"], "area_km2": round(good[0]["area_km2"], 1)},
        "latest": {"date": latest["date"], "area_km2": round(latest["area_km2"], 1),
                   "range_across_methods_km2": latest["band_km2"]},
        "minimum": {"date": good[i_min]["date"], "area_km2": round(float(areas[i_min]), 1)},
        "maximum": {"date": good[i_max]["date"], "area_km2": round(float(areas[i_max]), 1)},
        "change_first_to_latest_km2": round(float(areas[-1] - areas[0]), 1),
        "change_first_to_latest_pct": round(float((areas[-1] - areas[0]) / areas[0] * 100), 1),
        "change_max_to_latest_pct": round(float((areas[-1] - areas[i_max]) / areas[i_max] * 100), 1),
        "linear_trend_km2_per_year": round(float(np.polyfit(years, areas, 1)[0]), 2),
        "annual_mean_km2": {y: round(float(np.mean(v)), 1) for y, v in by_year.items()},
        "points": [[p["date"], round(p["area_km2"], 1), *p["band_km2"]] for p in good],
        "points_format": "[date, area_km2 (MNDWI), method_range_low, method_range_high]",
    }


def ground_truth(target: str, start: str | None = None, end: str | None = None) -> dict:
    if target != "lake-mead":
        return {"error": f"no ground truth source wired for {target}"}
    truth = lake_mead_monthly()
    grade = compare_to_elevation(_points(target, start, end), truth)
    since_1940 = [(d, v) for d, v in truth if d.year >= 1940]
    low_d, low_v = min(since_1940, key=lambda x: x[1])
    prev_d, prev_v = min([(d, v) for d, v in since_1940 if d.year < low_d.year], key=lambda x: x[1])
    return {
        "source": WATER_TARGETS[target]["truth"],
        "sop": sops.current("water").version,
        "period": [start, end],
        "agreement": {k: grade[k] for k in ("n", "pearson_r", "rmse_km2", "relative_rmse", "slope_km2_per_ft") if k in grade},
        "outliers_vs_gauge": grade.get("outliers", []),
        "gauge_latest": {"date": str(truth[-1][0]), "elevation_ft": truth[-1][1]},
        "gauge_lowest_since_1940": {"date": str(low_d), "elevation_ft": low_v},
        "previous_record_low": {"date": str(prev_d), "elevation_ft": prev_v},
    }


def remeasure(target: str, day: str) -> dict:
    """Re-run the instrument from raw imagery for one date — the reviewer's independent check."""
    bbox = WATER_TARGETS[target]["bbox"]
    items = search_scenes(bbox, day, day, max_cloud=100)
    if not items:
        return {"error": f"no scenes on {day}"}
    sop = sops.current("water")
    return measure_water(load_day(items, bbox, resolution=sop.resolution_m), sop).summary()


# Variants from scripts/water_methods_eval.py, and the SOP change that reproduces each (None: not expressible).
METHOD_TO_SOP = {
    "mndwi>0": {"dark_water_rule": False},
    "mndwi>0|dark.015": {"dark_water_rule": True, "dark_reflectance": 0.015},
    "mndwi>0|dark.025": {"dark_water_rule": True, "dark_reflectance": 0.025},
    "mndwi>0|nir<.02": None,
    "awei_nsh>0": None,
    "esa_scl": None,
}


def methods_study(target: str) -> dict:
    """Which water-detection variant tracks the gauge best (precomputed by scripts/water_methods_eval.py),
    plus what each scene quality gate would cost in months and gain in agreement."""
    path = data_dir(target) / "methods_eval.json"
    if not path.exists():
        return {"error": "no methods study on file; run scripts/water_methods_eval.py"}
    ev = json.loads(path.read_text())
    ranked = sorted(ev["variants"].items(), key=lambda kv: -kv[1]["pearson_r"])
    return {
        "dates_compared": ev["n"],
        "score": "Pearson r between measured area and USBR gauge elevation (higher is better); "
                 "scatter = RMSE around a quadratic area–elevation fit",
        "variants": [{"method": k, **v, "sop_changes": METHOD_TO_SOP.get(k)} for k, v in ranked],
        "quality_gates": gate_options(target),
        "note": "sop_changes = the SOP field values that reproduce the method; null means the SOP cannot express it",
    }


def gate_options(target: str) -> dict | list:
    """Replay quality gates over the cached dark-water series: months kept vs agreement with the gauge.
    (A real re-run falls back to other dates in rejected months, so it keeps at least this many.)"""
    base = {"dark_water_rule": True, "dark_reflectance": 0.025}
    series = None
    for d in sorted(data_dir(target).glob("water-sop-*")):
        sop = json.loads((d / "sop.json").read_text()) if (d / "sop.json").exists() else {}
        if all(sop.get(k) == v for k, v in base.items()) and sop.get("max_dark_share", 1.0) == 1.0 \
                and sop.get("min_method_iou", 0.0) == 0.0 and (d / "series.json").exists():
            series = usable_points(json.loads((d / "series.json").read_text()))
    if not series or target != "lake-mead":
        return {"note": "no cached dark-water series to replay gates on"}
    truth = lake_mead_monthly()
    rows = []
    for max_dark in (1.0, 0.5, 0.3):
        for min_iou in (0.0, 0.75, 0.8):
            keep = [p for p in series if p.get("dark_share", 0) <= max_dark
                    and p.get("agreement_iou", {}).get("esa_scl", 1) >= min_iou]
            g = compare_to_elevation(keep, truth)
            if "pearson_r" in g:
                rows.append({"sop_changes": base | {"max_dark_share": max_dark, "min_method_iou": min_iou},
                             "months_kept": g["n"], "pearson_r": g["pearson_r"], "rmse_km2": g["rmse_km2"]})
    return sorted(rows, key=lambda r: (-r["pearson_r"], -r["months_kept"]))


def evidence_paths(target: str, dates: list[str]):
    pts = {p.get("date"): p for p in _points(target)}
    base = series_dir(data_dir(target), sops.current("water"))
    out, missing = [], []
    for d in dates[:3]:
        if d in pts and pts[d].get("evidence"):
            out.append(base / pts[d]["evidence"])
        else:
            missing.append(d)
    return out, missing


def view_tool() -> Tool:
    def view(args):
        paths, missing = evidence_paths(args["target"], args["dates"])
        return {"shown": [p.stem for p in paths], "missing": missing, "_images": paths,
                "legend": "cyan outline/fill = water as measured by the MNDWI instrument; "
                          "unoutlined dark-blue or black areas inside the lake were NOT counted"}
    return Tool("view_evidence", "Look at the satellite evidence image(s) for up to 3 measured dates.",
                schema(target={"type": "string"}, dates={"type": "array", "items": {"type": "string"}}), view)


def make_tools() -> list[Tool]:
    def measure(args):
        target = args["target"]
        if target not in WATER_TARGETS:
            return {"error": f"unknown target; choose from {list(WATER_TARGETS)}"}
        t = WATER_TARGETS[target]
        run_series(t["name"], t["bbox"], args["start"], args["end"], data_dir(target), sop=sops.current("water"))
        return series_stats(target, args["start"], args["end"])

    return [
        Tool("list_targets", "Water bodies this lab can study, with areas of interest and ground-truth sources.",
             schema(), lambda a: WATER_TARGETS),
        Tool("measure_water_series",
             "Measure water surface area month by month from Sentinel-2 (clearest scene per month, quality-gated "
             "by the current SOP). Cached per SOP version; returns statistics and every data point.",
             schema(target={"type": "string"}, start={"type": "string", "description": "YYYY-MM-DD"},
                    end={"type": "string", "description": "YYYY-MM-DD"}), measure),
        Tool("compare_with_ground_truth",
             "Grade the measured series for a period against official gauge records: correlation, scatter, "
             "outliers, record levels.",
             schema(target={"type": "string"}, start={"type": "string"}, end={"type": "string"}),
             lambda a: ground_truth(a["target"], a.get("start"), a.get("end"))),
        view_tool(),
        Tool("submit_finding", "Submit the study's finding to the Director. Ends your turn.",
             schema(required=["headline", "summary", "claims", "evidence_dates", "caveats", "confidence"],
                    headline={"type": "string"},
                    summary={"type": "string", "description": "2-4 sentences for a general audience"},
                    claims={"type": "array", "items": schema(statement={"type": "string"}, value={"type": "number"},
                                                             unit={"type": "string"}, source_tool={"type": "string"})},
                    evidence_dates={"type": "array", "items": {"type": "string"}},
                    caveats={"type": "array", "items": {"type": "string"}},
                    confidence={"type": "string", "enum": ["low", "medium", "high"]}),
             lambda a: {"received": True}, terminal=True),
    ]
