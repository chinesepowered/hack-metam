"""Director: plans the institute's work, rules on reviews, owns the SOPs, publishes the bulletin."""
from __future__ import annotations

import json

from orbital import sops
from orbital.agents import water_lab
from orbital.agents.runtime import Agent, Tool, schema
from orbital.targets import WATER_TARGETS

SYSTEM = """You are the Director of Orbital, a one-person Earth-science institute run entirely by AI labs.
You decide what to study, assign studies to labs, rule on every review, keep the labs' standard operating
procedures (SOPs) consistent, and publish only findings that passed review. You never measure anything yourself.
When a review shows a systematic measurement problem, fix the procedure (issue a new SOP) based on evidence,
then send the study back so the lab re-measures under it. Be decisive and brief."""

LABS = {"water": "Water Lab — lake and reservoir extent from Sentinel-2, graded against gauge records"}


def _agent(log, provider, tools) -> Agent:
    return Agent("director", SYSTEM, tools, log, provider)


def plan(log, provider: str, briefing: str) -> list[dict]:
    studies: list[dict] = []

    def assign(a):
        if a["lab"] not in LABS:
            return {"error": f"unknown lab; labs: {list(LABS)}"}
        if a["lab"] == "water" and a["target"] not in WATER_TARGETS:
            return {"error": f"unknown target; water targets: {list(WATER_TARGETS)}"}
        study = {"id": f"{a['lab']}-{len(studies) + 1}", **{k: a[k] for k in ("lab", "target", "question", "start", "end")}}
        studies.append(study)
        log.emit("director", "assign", f"{study['id']} → {a['lab']} lab: {a['question']}", study=study)
        return {"assigned": study["id"]}

    tools = [
        Tool("assign_study", "Assign a study to a lab.",
             schema(lab={"type": "string", "enum": list(LABS)}, target={"type": "string"},
                    question={"type": "string"}, start={"type": "string", "description": "YYYY-MM-DD"},
                    end={"type": "string", "description": "YYYY-MM-DD"}), assign),
        Tool("finish_planning", "Done assigning studies.", schema(note={"type": "string"}),
             lambda a: {"ok": True}, terminal=True),
    ]
    task = (f"Briefing: {briefing}\n\nLabs:\n" + "\n".join(f"- {k}: {v}" for k, v in LABS.items())
            + f"\n\nWater targets: {json.dumps(WATER_TARGETS, indent=1)}\n\nAssign the studies, then finish_planning.")
    _agent(log, provider, tools).run(task)
    return studies


def decide(log, provider: str, packets: list[dict]) -> dict:
    """packets: [{study, finding, audit, review}] → {publish: {id: note}, send_back: {id: instructions}, sop: [...]}"""
    out = {"publish": {}, "send_back": {}, "sop": []}
    ids = {p["study"]["id"] for p in packets}

    def check(sid):
        if sid not in ids:
            raise ValueError(f"unknown study id; ids: {sorted(ids)}")

    def publish(a):
        check(a["study_id"])
        out["publish"][a["study_id"]] = a.get("note", "")
        log.emit("director", "publish", f"{a['study_id']} approved for publication", note=a.get("note", ""))
        return {"ok": True}

    def send_back(a):
        check(a["study_id"])
        out["send_back"][a["study_id"]] = a["instructions"]
        log.emit("director", "send_back", f"{a['study_id']} sent back: {a['instructions']}")
        return {"ok": True}

    def issue_sop(a):
        entry = sops.issue(a["lab"], a["changes"], a["reason"])
        out["sop"].append(entry)
        log.emit("director", "sop_update", f"{entry['version']} issued: {a['reason']}", sop=entry)
        return entry

    tools = [
        Tool("methods_study", "Compare water-detection methods against the gauge; shows which SOP change each needs.",
             schema(target={"type": "string"}), lambda a: water_lab.methods_study(a["target"])),
        Tool("current_sop", "The lab's current SOP and every field you may change.",
             schema(lab={"type": "string"}),
             lambda a: {"current": sops.current(a["lab"]).__dict__, "fields": sops.FIELDS[a["lab"]],
                        "history": sops.history()}),
        Tool("issue_sop", "Issue a new SOP version for a lab; every later measurement by that lab follows it.",
             schema(lab={"type": "string", "enum": list(LABS)}, changes={"type": "object"}, reason={"type": "string"}),
             issue_sop),
        Tool("send_back", "Send a study back to its lab with instructions (e.g. re-measure under the new SOP).",
             schema(study_id={"type": "string"}, instructions={"type": "string"}), send_back),
        Tool("publish", "Approve a reviewed study for publication.",
             schema(study_id={"type": "string"}, note={"type": "string"}), publish),
        Tool("finish_decisions", "Done ruling on this round.", schema(summary={"type": "string"}),
             lambda a: {"ok": True}, terminal=True),
    ]
    brief = [{"study": p["study"], "finding": {k: v for k, v in p["finding"].items() if not k.startswith("_")},
              "number_audit": p["audit"], "review": {k: v for k, v in p["review"].items() if not k.startswith("_")}}
             for p in packets]
    task = ("Rule on each study below: publish it, or send it back. Publish only if the reviewer accepted it.\n"
            "If a review points to a systematic measurement problem, use methods_study and current_sop, issue an "
            "evidence-based SOP change, then send the study back to be re-measured under it.\n\n"
            + json.dumps(brief, indent=1, default=str))
    _agent(log, provider, tools).run(task)
    return out


def bulletin(log, provider: str, published: list[dict], ledger: list[dict]) -> dict:
    result = {}

    def write(a):
        result.update(a)
        log.emit("director", "bulletin", a["title"], markdown=a["markdown"])
        return {"ok": True}

    tools = [Tool("publish_bulletin", "Publish the institute bulletin (markdown).",
                  schema(title={"type": "string"}, markdown={"type": "string"}), write, terminal=True)]
    task = ("Write this cycle's institute bulletin for the public: one short section per published finding "
            "(headline, what was measured, key numbers exactly as in the finding, how it was verified), then a short "
            "'How we caught our own mistakes' section from the error ledger and SOP history. Plain, warm, precise. "
            "Do not add numbers that are not in the inputs.\n\n"
            + json.dumps({"published": published, "error_ledger": ledger, "sop_history": sops.history()},
                         indent=1, default=str))
    _agent(log, provider, tools).run(task)
    return result
