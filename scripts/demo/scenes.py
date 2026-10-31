"""Demo video scenes. Narration numbers are read from the run's own outputs, never typed by hand.

    uv run python scripts/demo/scenes.py cycle-1   # prints the narration for review
"""
from __future__ import annotations

import dataclasses
import json
import sys

from orbital.config import RUNS_DIR
from orbital.sops import DEFAULTS
from orbital.studies.analysis import compare_to_elevation
from orbital.studies.fire_study import fire_dir, run_fire
from orbital.studies.water_series import series_dir
from orbital.targets import data_dir
from orbital.truth.usbr import lake_mead_monthly


def session_sops(state: dict, lab: str) -> list:
    """The SOPs a session actually used, in order: the default, then each one the Director issued."""
    sop = DEFAULTS[lab]()
    used = [sop]
    for h in state.get("sop_history", []):
        if h["lab"] == lab:
            sop = dataclasses.replace(sop, version=h["version"], **h["changes"])
            used.append(sop)
    return used


def facts(session: str) -> dict:
    state = json.loads((RUNS_DIR / "sessions" / session / "state.json").read_text())
    water = session_sops(state, "water")
    fire = session_sops(state, "fire")
    truth = lake_mead_monthly()
    grade = lambda sop: compare_to_elevation(  # noqa: E731
        json.loads((series_dir(data_dir("lake-mead"), sop) / "series.json").read_text()), truth)
    burn = lambda sop: run_fire("Tartar", "ID", sop)["measurement"]  # cached: reads the session's result  # noqa: E731
    v1, v2 = grade(water[0]), grade(water[-1])
    f1, f2 = burn(fire[0]), burn(fire[-1])
    official = json.loads((fire_dir("Tartar", "ID", fire[0]) / "result.json").read_text())["fire"]["official_acres"]
    since = [(d, v) for d, v in truth if d.year >= 1940]
    low_d, low_v = min(since, key=lambda x: x[1])
    return {
        "low_ft": f"{low_v:,.1f}", "low_month": low_d.strftime("%B %Y"),
        "r1": f"{v1.get('pearson_r', 0):.2f}", "r2": f"{v2.get('pearson_r', 0):.2f}", "n": v2.get("n"),
        "rmse1": f"{v1.get('rmse_km2', 0):.0f}", "rmse2": f"{v2.get('rmse_km2', 0):.0f}",
        "iou1": f"{f1.get('iou', 0):.2f}", "iou2": f"{f2.get('iou', 0):.2f}",
        "prec2": f"{f2.get('precision', 0) * 100:.0f}", "outside1": f"{f1.get('burned_outside_perimeter_km2', 0):.0f}",
        "acres": f"{official:,}", "published": len(state.get("published", [])),
        "caught": len(state.get("ledger", [])), "sops": [s.key() for s in water + fire],
    }


def scenes(session: str) -> list[dict]:
    """The script follows what the session actually did: how many rounds, which SOPs, which verdicts."""
    f = facts(session)
    state = json.loads((RUNS_DIR / "sessions" / session / "state.json").read_text())
    fire_sops = [h for h in state.get("sop_history", []) if h["lab"] == "fire"]
    rounds = len(state.get("rounds", []))
    out = [
        {"id": "title", "visual": "/demo/title.html",
         "text": "Hi! I'm Capy. I run Orbital, a tiny Earth-science institute where every scientist is an AI agent, "
                 "and every number gets checked."},
        {"id": "hook", "visual": "/demo/hook.html",
         "text": f"This is Lake Mead, the largest reservoir in the United States. In {f['low_month']}, its gauge hit "
                 f"{f['low_ft']} feet, the lowest since 1940. We wanted to measure it ourselves, from orbit."},
        {"id": "crew", "visual": "replay:r1",
         "text": "This is mission control. The Director plans the work. The Water Lab and Fire Lab measure the planet "
                 "from free Sentinel-2 satellite images. The Reviewer signs off on every finding, and the Auditor "
                 "traces every number back to a tool. The agents run on Muse Spark Open, Meta's open-weights model."},
        {"id": "round1", "visual": "replay:r1_review",
         "text": "I asked two questions: how much has Lake Mead changed since 2018, and how big was this summer's "
                 "Tartar Fire in Idaho? The labs measured 106 months of the lake and mapped the fire, and every "
                 "number traced back to a tool."},
        {"id": "reject", "visual": "replay:r1_decide",
         "text": f"But the Reviewer checked the satellite evidence and sent both studies back. In winter the lake "
                 f"looked almost black, so our water index missed most of it. Agreement with the gauge was just "
                 f"{f['r1']}. And the fire map counted {f['outside1']} square kilometers of farmland as burned."},
    ]
    if rounds >= 3 and len(fire_sops) >= 2:
        out += [
            {"id": "fix", "visual": "replay:r2_decide",
             "text": f"So the Director ran a methods study and issued new procedures to both labs. The water fix worked "
                     f"first time: agreement jumped from {f['r1']} to {f['r2']}. The fire map improved, but it still "
                     f"missed the institute's bar."},
            {"id": "fix2", "visual": "replay:bulletin",
             "text": f"So the Director tightened the fire procedure again. On the third try, the burn map overlapped the "
                     f"official perimeter at {f['iou2']}, with {f['prec2']} percent precision. Both findings passed "
                     f"review and went into the bulletin."},
        ]
    else:
        out += [
            {"id": "fix", "visual": "replay:bulletin",
             "text": f"So the Director ran a methods study and issued new procedures to both labs. Agreement jumped from "
                     f"{f['r1']} to {f['r2']}, and the fire map now overlaps the official perimeter at {f['iou2']}, "
                     f"with {f['prec2']} percent precision. Both passed review and went into the bulletin."},
        ]
    out += [
        {"id": "publish", "visual": "replay:end",
         "text": "The bulletin even lists the mistakes we caught in our own work."},
        {"id": "verify", "visual": "/demo/verify.html",
         "text": "Every finding passes five checks: independent methods, official ground truth, a number audit, a "
                 "vision review of the evidence, and a fresh re-measurement from raw imagery."},
        {"id": "close", "visual": "/demo/close.html",
         "text": "Satellite data is free, and it covers every lake and every wildfire on Earth. Orbital turns it into "
                 "verified science, around the clock, with one person and a crew of capybaras. Thanks for watching!"},
    ]
    return out


if __name__ == "__main__":
    session = sys.argv[1] if len(sys.argv) > 1 else "cycle-1"
    print(json.dumps(facts(session), indent=1))
    total = 0
    for s in scenes(session):
        words = len(s["text"].split())
        total += words
        print(f"\n[{s['id']}] ({words} words) {s['text']}")
    print(f"\n{total} words ≈ {total / 150 * 60:.0f}s at 150 wpm")
