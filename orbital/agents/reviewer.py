"""Reviewer: audits every finding before the Director may publish it."""
from __future__ import annotations

import json

from orbital.agents import water_lab
from orbital.agents.runtime import Agent, Tool, schema

SYSTEM = """You are the Reviewer at Orbital, an autonomous Earth-science institute.
No finding is published without your sign-off. You are strict but fair.

For each finding:
1. Check every claim's number against tool outputs (get_series_stats, get_ground_truth). A claim is
   "verified" only if a tool output supports it; "incorrect" if a tool output contradicts it;
   "unsupported" if nothing supports it.
2. Look at the evidence images for the dates the finding relies on and for any outliers. Check that
   the water outline follows the real shoreline, and look for clouds, shadows, haze or missing coverage.
3. Independently re-measure at least one key date from raw imagery with recompute_point and compare.
4. Judge whether the caveats are honest and the confidence is justified.
Finish by calling submit_review exactly once. Verdict "revise" if any claim is incorrect or unsupported,
or if evidence shows a measurement problem."""


def make_agent(log, provider: str) -> Agent:
    tools = [
        Tool("get_series_stats", "The lab's measured series: statistics and all data points.",
             schema(target={"type": "string"}), lambda a: water_lab.series_stats(a["target"])),
        Tool("get_ground_truth", "Official gauge records and the series' agreement with them.",
             schema(target={"type": "string"}), lambda a: water_lab.ground_truth(a["target"])),
        water_lab.view_tool(),
        Tool("recompute_point", "Independently re-measure one date from raw satellite imagery.",
             schema(target={"type": "string"}, date={"type": "string"}),
             lambda a: water_lab.remeasure(a["target"], a["date"])),
        Tool("submit_review", "Submit your review. Ends your turn.",
             schema(required=["verdict", "claim_checks", "issues"],
                    verdict={"type": "string", "enum": ["accept", "revise"]},
                    claim_checks={"type": "array", "items": schema(
                        statement={"type": "string"},
                        status={"type": "string", "enum": ["verified", "incorrect", "unsupported"]},
                        note={"type": "string"})},
                    issues={"type": "array", "items": {"type": "string"}},
                    sop_suggestions={"type": "array", "items": {"type": "string"}}),
             lambda a: {"received": True}, terminal=True),
    ]
    return Agent("reviewer", SYSTEM, tools, log, provider)


def task_for(study: dict, finding: dict, audit: list[dict]) -> str:
    clean = {k: v for k, v in finding.items() if not k.startswith("_")}
    return (f"Review study {study['id']} by the {study['lab']} lab.\n"
            f"Brief: {study['question']} (target: {study['target']}, {study['start']} to {study['end']})\n\n"
            f"Finding:\n{json.dumps(clean, indent=1)}\n\n"
            f"Automatic number audit (does each claimed value appear in the lab's tool outputs?):\n"
            f"{json.dumps(audit, indent=1)}")
