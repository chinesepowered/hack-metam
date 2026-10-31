"""Reviewer: audits every finding before the Director may publish it."""
from __future__ import annotations

import json

from orbital import sops
from orbital.agents import fire_lab, water_lab
from orbital.agents.runtime import Agent, Tool, schema
from orbital.studies.fire_study import run_fire

SYSTEM = """You are the Reviewer at Orbital, an autonomous Earth-science institute.
No finding is published without your sign-off. You are strict but fair.

For each finding:
1. Check every claim's number against the data tools. A claim is "verified" only if a tool output supports it;
   "incorrect" if a tool output contradicts it; "unsupported" if nothing supports it.
2. Look at the evidence images the finding relies on, and at outliers. Look for clouds, shadows, haze, missing
   coverage, or things mapped that are not what the lab claims (e.g. farmland or a neighbouring fire counted as burned).
3. Independently re-check at least one measurement with the recompute tool and compare.
4. Judge whether the caveats are honest and the confidence is justified.

Publication standards — verdict "revise" if ANY holds, even when the finding discloses the problem honestly
(the institute fixes its methods instead of publishing caveated numbers):
- a claim is incorrect or unsupported;
- Water: agreement with the gauge is weak (Pearson r < 0.8), or evidence shows systematic missed water;
- Fire: agreement with the official perimeter is weak (IoU < 0.75), or a large burned area is mapped outside the
  perimeter (> 10% of the perimeter's area), or evidence shows systematic false burns.
Put concrete, procedure-level fixes in sop_suggestions. Finish by calling submit_review exactly once."""


def _water_tools() -> list[Tool]:
    return [
        Tool("get_series_stats", "The lab's measured series for a period: statistics and all data points.",
             schema(target={"type": "string"}, start={"type": "string"}, end={"type": "string"}),
             lambda a: water_lab.series_stats(a["target"], a.get("start"), a.get("end"))),
        Tool("get_ground_truth", "Official gauge records and the period's agreement with them.",
             schema(target={"type": "string"}, start={"type": "string"}, end={"type": "string"}),
             lambda a: water_lab.ground_truth(a["target"], a.get("start"), a.get("end"))),
        water_lab.view_tool(),
        Tool("recompute_point", "Independently re-measure one date from raw satellite imagery.",
             schema(target={"type": "string"}, date={"type": "string"}),
             lambda a: water_lab.remeasure(a["target"], a["date"])),
    ]


def _fire_tools() -> list[Tool]:
    def recompute(a):
        r = run_fire(a["fire"], a["state"], sops.current("fire"), use_cache=False)
        return r.get("measurement", {"error": "no usable scene pair"}) | {"pre": r.get("pre_date"), "post": r.get("post_date")}
    return [
        Tool("get_burn_result", "The lab's burn-scar measurement and its agreement with the official perimeter.",
             schema(fire={"type": "string"}, state={"type": "string"}), lambda a: fire_lab.measure(a["fire"], a["state"])),
        fire_lab.view_tool(),
        Tool("recompute_burn", "Independently recompute the burn map from raw imagery (ignores the lab's cache).",
             schema(fire={"type": "string"}, state={"type": "string"}), recompute),
    ]


SUBMIT = Tool(
    "submit_review", "Submit your review. Ends your turn.",
    schema(required=["verdict", "claim_checks", "issues"],
           verdict={"type": "string", "enum": ["accept", "revise"]},
           claim_checks={"type": "array", "items": schema(
               statement={"type": "string"},
               status={"type": "string", "enum": ["verified", "incorrect", "unsupported"]},
               note={"type": "string"})},
           issues={"type": "array", "items": {"type": "string"}},
           sop_suggestions={"type": "array", "items": {"type": "string"}}),
    lambda a: {"received": True}, terminal=True)


def make_agent(log, provider: str, lab: str, tag: str | None = None) -> Agent:
    tools = (_water_tools() if lab == "water" else _fire_tools()) + [SUBMIT]
    return Agent("reviewer", SYSTEM, tools, log, provider, max_steps=24, tag=tag)


def task_for(study: dict, finding: dict, audit: list[dict]) -> str:
    clean = {k: v for k, v in finding.items() if not k.startswith("_")}
    if study["lab"] == "water":
        scope = f"Use target={study['target']}, start={study['start']}, end={study['end']} with the data tools."
    else:
        name, state = [s.strip() for s in study["target"].split(",")]
        scope = f"Use fire={name}, state={state} with the data tools."
    return (f"Review study {study['id']} by the {study['lab']} lab.\nBrief: {study['question']}\n{scope}\n\n"
            f"Finding:\n{json.dumps(clean, indent=1)}\n\n"
            f"Automatic number audit (does each claimed value appear in the lab's tool outputs?):\n"
            f"{json.dumps(audit, indent=1)}")
