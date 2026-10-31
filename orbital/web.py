"""Mission control: a small API over the institute's runs, plus the static front end.

    uv run uvicorn orbital.web:app --port 8000
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from orbital import sops
from orbital.config import ROOT, RUNS_DIR
from orbital.targets import WATER_TARGETS, data_dir
from orbital.truth.usbr import lake_mead_monthly

WEB = ROOT / "web"
SESSIONS = RUNS_DIR / "sessions"
app = FastAPI(title="Orbital mission control")


def _session_dir(sid: str) -> Path:
    d = (SESSIONS / sid).resolve()
    if SESSIONS.resolve() not in d.parents or not d.is_dir():
        raise HTTPException(404, "no such session")
    return d


@app.get("/api/sessions")
def sessions():
    out = []
    for d in sorted(SESSIONS.glob("*"), reverse=True) if SESSIONS.exists() else []:
        ev = d / "events.jsonl"
        if not ev.exists():
            continue
        first = json.loads(ev.open().readline() or "{}")
        state = d / "state.json"
        out.append({"id": d.name, "briefing": first.get("text", ""), "started": first.get("ts"),
                    "done": state.exists() and "bulletin" in json.loads(state.read_text())})
    return out


@app.get("/api/sessions/{sid}/events")
def events(sid: str, after: int = 0):
    lines = (_session_dir(sid) / "events.jsonl").read_text().splitlines()
    return {"events": [json.loads(x) for x in lines[after:] if x.strip()], "next": len(lines)}


@app.get("/api/sessions/{sid}/state")
def state(sid: str):
    path = _session_dir(sid) / "state.json"
    return json.loads(path.read_text()) if path.exists() else {}


@app.get("/api/study/{target}")
def study(target: str):
    """Every SOP version measured for this target: points, grade, media."""
    if target not in WATER_TARGETS:
        raise HTTPException(404, "unknown target")
    versions = []
    for d in sorted(data_dir(target).glob("water-sop-*")):
        series, grade = d / "series.json", d / "grade.json"
        if not series.exists():
            continue
        pts = [p for p in json.loads(series.read_text()) if p.get("usable") and p.get("date")]
        g = json.loads(grade.read_text()) if grade.exists() else {}
        rel = d.relative_to(RUNS_DIR)
        versions.append({
            "key": d.name, "version": d.name.rsplit("-", 1)[0],
            "points": [{"date": p["date"], "area": round(p["area_km2"], 2), "lo": p["band_km2"][0],
                        "hi": p["band_km2"][1], "evidence": f"/runs/{rel}/{p['evidence']}",
                        "warnings": p.get("warnings", [])} for p in pts],
            "grade": {k: g.get(k) for k in ("n", "pearson_r", "rmse_km2", "slope_km2_per_ft", "outliers")},
            "timelapse": f"/runs/{rel}/timelapse.mp4" if (d / "timelapse.mp4").exists() else None,
        })
    truth = [{"date": str(d), "elev": v} for d, v in lake_mead_monthly() if d.year >= 2017] if target == "lake-mead" else []
    methods = data_dir(target) / "methods_eval.json"
    return {"target": target, **WATER_TARGETS[target], "versions": versions, "truth": truth,
            "methods": json.loads(methods.read_text())["variants"] if methods.exists() else None}


@app.get("/api/fires")
def fires():
    """Every fire the Fire Lab has measured, with each SOP's result."""
    out = {}
    for result in sorted((RUNS_DIR / "fires").glob("*/*/result.json")):
        r = json.loads(result.read_text())
        if not r.get("usable"):
            continue
        slug = result.parent.parent.name
        entry = out.setdefault(slug, {"fire": r["fire"], "versions": []})
        entry["versions"].append({
            "key": result.parent.name, "version": r["sop"], "pre": r["pre_date"], "post": r["post_date"],
            "measurement": r["measurement"],
            "evidence": {k: f"/runs/{v}" for k, v in r["evidence"].items()},
        })
    return out


class RunRequest(BaseModel):
    briefing: str | None = None


_running: dict[str, threading.Thread] = {}


@app.post("/api/sessions")
def start(req: RunRequest):
    from orbital import institute

    if any(t.is_alive() for t in _running.values()):
        raise HTTPException(409, "a cycle is already running")
    sid = time.strftime("%Y%m%d-%H%M%S")
    t = threading.Thread(target=institute.run, kwargs={"briefing": req.briefing or institute.DEFAULT_BRIEFING,
                                                       "run_id": sid}, daemon=True)
    t.start()
    _running[sid] = t
    return {"id": sid}


@app.get("/api/sop")
def sop():
    return {"current": sops.current("water").__dict__, "history": sops.history()}


RUNS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/runs", StaticFiles(directory=RUNS_DIR), name="runs")
app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/{name}")
def static_file(name: str):
    path = (WEB / name).resolve()
    if path.parent != WEB.resolve() or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)
