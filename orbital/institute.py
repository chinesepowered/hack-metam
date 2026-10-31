"""Run one cycle of the institute: plan → labs measure → audit + review → director rules → bulletin.

    uv run python -m orbital.institute "briefing text"
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from orbital import llm, sops
from orbital.agents import director, fire_lab, reviewer, water_lab
from orbital.agents.audit import audit_finding
from orbital.agents.events import EventLog
from orbital.agents.runtime import Agent
from orbital.config import RUNS_DIR
from orbital.targets import WATER_TARGETS

DEFAULT_BRIEFING = ("Two questions this cycle. (1) Lake Mead is in the news again: how much has its surface "
                    "changed from 2018 to today, and do our satellite measurements agree with the official gauge? "
                    "(2) Map the burn scar of this summer's Tartar Fire in Idaho and check it against the official "
                    "perimeter.")


def roles() -> dict[str, str]:
    """Muse Spark Open (open weights) for every role. ORBITAL_META_ROLES=1 moves the Director and Reviewer to
    Muse Wonder / Muse Spark 1.3 on the Meta Model API (needs MODEL_API_KEY)."""
    meta = os.environ.get("ORBITAL_META_ROLES") == "1"
    return {
        "lab": llm.pick("spark-open"),
        "reviewer": llm.pick("spark", "spark-open") if meta else llm.pick("spark-open"),
        "director": llm.pick("wonder", "spark", "spark-open") if meta else llm.pick("spark-open"),
    }


def lab_task(study: dict, revision: dict | None) -> str:
    t = WATER_TARGETS[study["target"]]
    text = (f"Study {study['id']}: {study['question']}\n"
            f"Target: {study['target']} ({t['name']}); period {study['start']} to {study['end']}; "
            f"current SOP: {sops.current('water').version}.\n"
            "Workflow: 1) measure_water_series  2) compare_with_ground_truth (same start/end)  3) view_evidence "
            "for the latest date, the minimum, and any outliers or points with warnings  4) submit_finding.")
    return text + _revision_note(revision)


def _revision_note(revision: dict | None) -> str:
    if not revision:
        return ""
    return ("\n\nThis is a REVISION. Director's instructions: " + revision["instructions"]
            + "\nReviewer's issues with the previous version:\n- " + "\n- ".join(revision.get("issues", [])))


def run_lab(study: dict, log: EventLog, provider: str, revision: dict | None) -> tuple[dict, list[dict]]:
    if study["lab"] == "water":
        system, tools, task = water_lab.SYSTEM, water_lab.make_tools(), lab_task(study, revision)
    else:
        system, tools, task = fire_lab.SYSTEM, fire_lab.make_tools(), fire_lab.task(study) + _revision_note(revision)
    agent = Agent(f"{study['lab']}-lab", system, tools, log, provider, tag=study["id"])
    log.emit(agent.name, "start", f"{study['id']}: {study['question']}", study=study, tag=study["id"])
    try:
        finding = agent.run(task)
    except Exception as e:  # one failing agent must not take the cycle down
        finding = {"error": f"{type(e).__name__}: {e}"}
    if "error" not in finding:
        log.emit(agent.name, "finding", finding.get("headline", ""), study_id=study["id"],
                 finding={k: v for k, v in finding.items() if not k.startswith("_")})
    return finding, agent.results


def run_review(study: dict, finding: dict, audit: list[dict], log: EventLog, provider: str) -> dict:
    agent = reviewer.make_agent(log, provider, study["lab"], tag=study["id"])
    log.emit("reviewer", "start", f"reviewing {study['id']}", tag=study["id"])
    try:
        review = agent.run(reviewer.task_for(study, finding, audit))
    except Exception as e:
        review = {"error": f"{type(e).__name__}: {e}"}
    if "error" in review:  # an unfinished review can never count as approval
        review = {"verdict": "revise", "claim_checks": [], "issues": [f"review did not complete: {review['error']}"]}
    log.emit("reviewer", "review", f"{study['id']}: {review.get('verdict', 'error')}", study_id=study["id"],
             review={k: v for k, v in review.items() if not k.startswith("_")})
    return review


def ledger_entries(study: dict, rnd: int, audit: list[dict], review: dict) -> list[dict]:
    out = [{"study": study["id"], "round": rnd, "caught_by": "number audit", "what": f"untraced value {a['value']}: {a['statement']}"}
           for a in audit if a["status"] == "untraced"]
    out += [{"study": study["id"], "round": rnd, "caught_by": "reviewer", "what": f"{c['status']}: {c['statement']} — {c.get('note', '')}"}
            for c in review.get("claim_checks", []) if c.get("status") in ("incorrect", "unsupported")]
    out += [{"study": study["id"], "round": rnd, "caught_by": "reviewer", "what": issue}
            for issue in review.get("issues", [])] if review.get("verdict") == "revise" else []
    return out


def run(briefing: str = DEFAULT_BRIEFING, max_rounds: int = 3, run_id: str | None = None) -> Path:
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / "sessions" / run_id
    log = EventLog(run_dir / "events.jsonl")
    sops.reset()
    who = roles()
    log.emit("institute", "start", briefing, roles={k: llm.model_name(v) for k, v in who.items()}, run_id=run_id)

    studies = director.plan(log, who["director"], briefing)
    state = {"run_id": run_id, "briefing": briefing, "studies": {s["id"]: s for s in studies},
             "rounds": [], "published": [], "ledger": []}
    pending = {s["id"]: None for s in studies}  # study id -> revision instructions (None on first pass)

    for rnd in range(1, max_rounds + 1):
        if not pending:
            break
        log.emit("institute", "round", f"round {rnd}: {len(pending)} studies", round=rnd)
        batch = [state["studies"][sid] for sid in pending]
        with ThreadPoolExecutor(max_workers=4) as pool:
            labs = list(pool.map(lambda s: run_lab(s, log, who["lab"], pending[s["id"]]), batch))
        packets = []
        for study, (finding, results) in zip(batch, labs):
            if "error" in finding:
                log.emit("institute", "error", f"{study['id']} lab did not finish: {finding['error']}")
                continue
            audit = audit_finding(finding, results)
            log.emit("auditor", "audit", f"{study['id']}: {sum(a['status'] == 'traced' for a in audit)}/{len(audit)} "
                     "claimed values traced to tool outputs", study_id=study["id"], audit=audit)
            packets.append({"study": study, "finding": finding, "audit": audit})
        with ThreadPoolExecutor(max_workers=4) as pool:
            reviews = list(pool.map(lambda p: run_review(p["study"], p["finding"], p["audit"], log, who["reviewer"]), packets))
        for p, r in zip(packets, reviews):
            p["review"] = r
            state["ledger"] += ledger_entries(p["study"], rnd, p["audit"], r)

        decision = director.decide(log, who["director"], packets)
        state["rounds"].append({"round": rnd, "packets": packets, "decision": decision})
        pending = {}
        for p in packets:
            sid = p["study"]["id"]
            if sid in decision["publish"]:
                state["published"].append({"study": p["study"], "finding": {k: v for k, v in p["finding"].items()
                                                                            if not k.startswith("_")},
                                           "review": p["review"], "note": decision["publish"][sid]})
            elif sid in decision["send_back"] and rnd < max_rounds:
                pending[sid] = {"instructions": decision["send_back"][sid], "issues": p["review"].get("issues", [])}
        (run_dir / "state.json").write_text(json.dumps(state, indent=1, default=str))

    state["bulletin"] = director.bulletin(log, who["director"], state["published"], state["ledger"])
    state["sop_history"] = sops.history()
    (run_dir / "state.json").write_text(json.dumps(state, indent=1, default=str))
    log.emit("institute", "done", f"{len(state['published'])} finding(s) published, "
             f"{len(state['ledger'])} problem(s) caught", run_id=run_id)
    return run_dir


if __name__ == "__main__":
    print(run(*sys.argv[1:2]))
