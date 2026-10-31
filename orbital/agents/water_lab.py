"""Water Lab: measures surface-water extent from orbit and grades itself against gauge records."""
from __future__ import annotations

import base64
import json
import re
import subprocess
import time
from datetime import date

import numpy as np

from orbital import sops
from orbital.agents.runtime import Tool, schema
from orbital.imagery import render
from orbital.imagery.load import load_day
from orbital.imagery.stac import search_scenes
from orbital.instruments import sam
from orbital.instruments.water import iou, measure_water
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


WATCH_PROMPT = """This video is a time-lapse of monthly Sentinel-2 images of {name}, about two seconds per month.
Each frame's caption shows its date and the water area the instrument measured; cyan outline/fill marks the water it
counted. Watch every frame and report the frames where the measurement looks wrong:
- missed_water: lake water that looks black or dark but is NOT covered in cyan;
- false_water: cyan over things that are not lake water (land, cloud, snow, shadow);
- other: anything else that makes the frame unreliable (haze, missing coverage).
Think briefly: do not enumerate frames that look fine. Answer with only a JSON array, one object per problem frame:
[{{"date": "YYYY-MM-DD as printed in the caption", "area_km2": number from the caption, "problem": "missed_water|false_water|other", "note": "what you see"}}]
Return [] if every frame looks right."""


CHUNK = 24  # months per clip: shorter clips get more of the model's attention per frame


def _review_video(out_dir, points, name: str):
    """Slow time-lapse for review (2 s per month, 800 px): the model samples ~1 frame/s, so each month is seen."""
    from orbital.imagery.render import frames_to_mp4
    video = out_dir / "review" / f"{name}.mp4"
    if not video.exists():
        video.parent.mkdir(parents=True, exist_ok=True)
        raw = frames_to_mp4([out_dir / p["evidence"] for p in points], video.with_suffix(".raw.mp4"), fps=0.5)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-vf", "scale=800:-2,fps=1",
                        "-c:v", "libx264", "-crf", "28", "-pix_fmt", "yuv420p", str(video)], check=True)
        raw.unlink()
    return video


def _watch_clip(video, name: str) -> tuple[list, float]:
    from orbital import llm
    data_url = "data:video/mp4;base64," + base64.b64encode(video.read_bytes()).decode()
    reply = llm.chat([{"role": "user", "content": [
        {"type": "text", "text": WATCH_PROMPT.format(name=name)},
        {"type": "video_url", "video_url": {"url": data_url}}]}], provider="spark-open", max_tokens=16000)
    text = reply.content
    try:
        return json.loads(text[text.index("["): text.rindex("]") + 1]), reply.seconds
    except ValueError:
        return [{"date": d, "problem": "other", "note": "unparsed"} for d in re.findall(r"\d{4}-\d{2}-\d{2}", text)], reply.seconds


def watch_timelapse(target: str, start: str | None = None, end: str | None = None) -> dict:
    """Skim the measured series as time-lapse video clips with Muse Spark Open (in parallel), then map what it
    flags to real data points."""
    from concurrent.futures import ThreadPoolExecutor
    good = usable_points(_points(target, start, end))
    if not good:
        return {"error": "no measured months to watch"}
    out_dir = series_dir(data_dir(target), sops.current("water"))
    chunks = [good[i:i + CHUNK] for i in range(0, len(good), CHUNK)]
    clips = [_review_video(out_dir, c, f"{c[0]['date']}_{c[-1]['date']}") for c in chunks]
    with ThreadPoolExecutor(max_workers=len(clips)) as pool:
        results = list(pool.map(lambda v: _watch_clip(v, WATER_TARGETS[target]["name"]), clips))
    flagged = [f for found, _ in results for f in found]
    seconds = max(s for _, s in results)
    by_date = {p["date"]: p for p in good}
    out, unmatched = [], []
    for f in flagged:
        try:
            seen = date.fromisoformat(str(f.get("date", ""))[:10])
        except ValueError:
            unmatched.append(f)
            continue
        # Captions are small: allow a misread day or digit, and use the printed area to pick the right month.
        near = [p for p in good if abs((date.fromisoformat(p["date"]) - seen).days) <= 20]
        area = f.get("area_km2")
        if isinstance(area, (int, float)) and near:
            near.sort(key=lambda p: abs(p["area_km2"] - area))
        if not near:
            unmatched.append(f)
            continue
        p = near[0]
        out.append({"matched_date": p["date"], "measured_km2": round(p["area_km2"], 1), "problem": f.get("problem"),
                    "caption_read_as": f.get("date"), "note": str(f.get("note", ""))[:300]})
    seen_dates, unique = set(), []
    for f in out:  # the same month can be flagged twice (once per problem); keep one row per month and problem
        if (f["matched_date"], f["problem"]) not in seen_dates:
            seen_dates.add((f["matched_date"], f["problem"]))
            unique.append(f)
    worst = sorted(unique, key=lambda f: f["measured_km2"])[:3]  # largest undercounts first
    return {"frames_watched": len(good), "clips": len(clips), "seconds": round(seconds, 1),
            "flagged_count": len(unique), "flagged": unique,
            "unmatched": unmatched, "shown": [f["matched_date"] for f in worst],
            "how_to_use": "the model skims the video; confirm each flagged month with view_evidence before relying on it",
            "known_dates": len(by_date), "_images": [out_dir / by_date[f["matched_date"]]["evidence"] for f in worst]}


def watch_tool() -> Tool:
    return Tool("watch_timelapse",
                "Watch the whole measured series as one time-lapse video (Muse Spark Open video understanding) and get "
                "the months where the water outline looks wrong. Confirm flagged months with view_evidence.",
                schema(required=["target"], target={"type": "string"}, start={"type": "string"}, end={"type": "string"}),
                lambda a: watch_timelapse(a["target"], a.get("start"), a.get("end")))


def sam_check(target: str, start: str | None = None, end: str | None = None) -> dict:
    """A second, independent instrument: Meta's SAM 2.1 outlines the lake in every measured month's image."""
    if not sam.available():
        return {"error": "SAM 2.1 is not installed on this machine (uv sync --extra sam)"}
    good = usable_points(_points(target, start, end))
    if not good:
        return {"error": "no measured months under the current SOP; measure the series first"}
    base = series_dir(data_dir(target), sops.current("water"))
    rows, masks, t0 = [], {}, time.time()
    for p in good:
        h, w = p["mask_shape"]
        o = sam.outline_month(target, p["date"], base / p["chip"], (h, w), p["pixel_area_m2"] ** 0.5)
        mine = np.unpackbits(np.load(base / "masks" / f"{p['date']}.npy"))[: h * w].reshape(h, w).astype(bool)
        masks[p["date"]] = (mine, o["mask"])
        rows.append({"date": p["date"], "usable": True, "instrument_km2": round(p["area_km2"], 1),
                     "area_km2": o["area_km2"], "overlap_iou": round(iou(mine, o["mask"]), 3)})
    disagree = sorted([r for r in rows if r["overlap_iou"] < 0.5], key=lambda r: r["overlap_iou"])
    shown = (disagree or sorted(rows, key=lambda r: r["overlap_iou"]))[:3]
    images = []
    for r in shown:
        p = next(x for x in good if x["date"] == r["date"])
        img = sam.compare_image(base / p["chip"], *masks[r["date"]],
                                f"{WATER_TARGETS[target]['name']} · {r['date']}",
                                f"cyan = water index {r['instrument_km2']:.0f} km² · magenta = SAM 2.1 outline "
                                f"{r['area_km2']:.0f} km² · overlap IoU {r['overlap_iou']:.2f}")
        images.append(render.save_png(img, base / "sam" / f"{r['date']}.png"))
    out = {
        "instrument": f"SAM 2.1 ({sam.MODEL_ID}, Meta open weights) running locally. It is prompted only with points "
                      "that ESA WorldCover maps as permanent water and never sees the water index.",
        "months_checked": len(rows),
        "seconds": round(time.time() - t0, 1),
        "median_overlap_iou": round(float(np.median([r["overlap_iou"] for r in rows])), 3),
        "median_sam_to_instrument_area": round(float(np.median([r["area_km2"] / max(r["instrument_km2"], 0.1)
                                                                for r in rows])), 2),
        "disagreement_count": len(disagree),
        "disagreements": [{"date": r["date"], "instrument_km2": r["instrument_km2"], "sam_km2": r["area_km2"],
                           "overlap_iou": r["overlap_iou"]} for r in disagree],
        "disagreement_rule": "overlap_iou < 0.5 between the two outlines",
        "shown": [r["date"] for r in shown],
        "how_to_read": "The two instruments fail in different ways, so a month where they disagree needs a look "
                       "(view_evidence). SAM draws the shoreline a little wider; compare trends, not exact areas.",
        "_images": images,
    }
    if target == "lake-mead":
        truth = lake_mead_monthly()
        for name, pts in (("sam_vs_gauge", rows), ("instrument_vs_gauge", good)):
            g = compare_to_elevation(pts, truth)
            out[name] = {k: g[k] for k in ("n", "pearson_r", "rmse_km2") if k in g}
    return out


def sam_tool() -> Tool:
    return Tool("sam_check",
                "Cross-check every measured month with a second, independent instrument: Meta's SAM 2.1 segmentation "
                "model outlines the lake in the satellite image itself. Returns where the two disagree, each one's "
                "agreement with the gauge, and images of the worst months (cyan = water index, magenta = SAM).",
                schema(required=["target"], target={"type": "string"}, start={"type": "string"}, end={"type": "string"}),
                lambda a: sam_check(a["target"], a.get("start"), a.get("end")))


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
        watch_tool(),
        sam_tool(),
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
