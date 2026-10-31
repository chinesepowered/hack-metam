"""Fire Lab: maps burn scars from orbit and grades itself against official fire perimeters."""
from __future__ import annotations

from orbital import sops
from orbital.agents.runtime import Tool, schema
from orbital.config import RUNS_DIR
from orbital.studies.fire_study import run_fire
from orbital.truth.fires import large_fires

SYSTEM = """You are the Fire Lab of Orbital, an autonomous Earth-science institute.
You map wildfire burn scars from Sentinel-2 imagery (change in Normalized Burn Ratio between a pre-fire and a
post-fire scene) and grade each map against the official NIFC fire perimeter.

Rules:
- Every number you report must come from a tool result. Never estimate or do mental math.
- Look at the evidence images (burn overlay and severity map) before relying on the numbers.
- Report caveats honestly: burned area outside the perimeter, cloud, scene timing, unburned islands.
- Finish by calling submit_finding exactly once."""


def _fire_args(a: dict) -> tuple[str, str]:
    return a["fire"], a["state"]


def measure(name: str, state: str) -> dict:
    sop = sops.current("fire")
    r = run_fire(name, state, sop)
    if not r.get("usable"):
        return {"error": "no clear pre/post scene pair passed quality gates", "rejected": r.get("rejected", [])}
    m = r["measurement"]
    return {
        "fire": r["fire"], "sop": sop.version, "pre_fire_scene": r["pre_date"], "post_fire_scene": r["post_date"],
        "burned_km2": round(m["burned_km2"], 1), "burned_acres": round(m["burned_acres"]),
        "burned_inside_perimeter_km2": round(m["burned_inside_perimeter_km2"], 1),
        "burned_outside_perimeter_km2": round(m["burned_outside_perimeter_km2"], 1),
        "official_perimeter_km2": m["official_km2"],
        "burned_vs_official_pct": m["burned_vs_official_pct"],
        "agreement_with_perimeter": {"iou": round(m["iou"], 3), "precision": round(m["precision"], 3),
                                     "recall": round(m["recall"], 3)},
        "severity_km2": m["severity_km2"],
        "phenology_offset_applied": round(m["offset_applied"], 4),
        "quality": {"cloud_pre": m["cloud_pre"], "cloud_post": m["cloud_post"], "nodata": m["nodata"]},
    }


def evidence(name: str, state: str, kinds: list[str]):
    r = run_fire(name, state, sops.current("fire"))
    ev = r.get("evidence", {})
    return [RUNS_DIR / ev[k] for k in kinds if k in ev][:3]


def view_tool() -> Tool:
    def view(a):
        kinds = a.get("kinds") or ["overlay", "severity"]
        paths = evidence(a["fire"], a["state"], kinds)
        return {"shown": [p.stem for p in paths], "_images": paths,
                "legend": "overlay: orange = mapped as burned, white line = official perimeter; "
                          "severity: green unburned → yellow low → orange moderate → red high"}
    return Tool("view_evidence", "Look at the fire's evidence images: overlay, severity, pre, post (up to 3).",
                schema(required=["fire", "state"], fire={"type": "string"}, state={"type": "string"},
                       kinds={"type": "array", "items": {"type": "string", "enum": ["overlay", "severity", "pre", "post"]}}),
                view)


def data_tools() -> list[Tool]:
    return [
        Tool("list_fires", "This year's largest US wildfires with official perimeters.",
             schema(min_acres={"type": "number"}), lambda a: {"fires": large_fires(a.get("min_acres", 50000))}),
        Tool("measure_burn_scar",
             "Map a fire's burn scar (dNBR between clear pre- and post-fire scenes, quality-gated by the current SOP) "
             "and grade it against the official perimeter. Cached per SOP.",
             schema(fire={"type": "string"}, state={"type": "string", "description": "two-letter state"}),
             lambda a: measure(*_fire_args(a))),
        view_tool(),
    ]


def make_tools() -> list[Tool]:
    return data_tools() + [
        Tool("submit_finding", "Submit the study's finding to the Director. Ends your turn.",
             schema(required=["headline", "summary", "claims", "caveats", "confidence"],
                    headline={"type": "string"},
                    summary={"type": "string", "description": "2-4 sentences for a general audience"},
                    claims={"type": "array", "items": schema(statement={"type": "string"}, value={"type": "number"},
                                                             unit={"type": "string"}, source_tool={"type": "string"})},
                    caveats={"type": "array", "items": {"type": "string"}},
                    confidence={"type": "string", "enum": ["low", "medium", "high"]}),
             lambda a: {"received": True}, terminal=True),
    ]


def task(study: dict) -> str:
    return (f"Study {study['id']}: {study['question']}\nFire: {study['target']}; current SOP: "
            f"{sops.current('fire').version}.\nWorkflow: 1) measure_burn_scar  2) view_evidence (overlay and severity)  "
            "3) submit_finding.")
