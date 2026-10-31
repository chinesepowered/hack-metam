"""Export a recorded session as a static replay site (no keys, no server).

    uv run python scripts/build_static.py cycle-5 ../hack-metam-static
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

from orbital import web
from orbital.config import ROOT, RUNS_DIR

BANNER = ("This page replays a real research cycle recorded on {date}: every event, image and number below came from "
          "that run. To run a live cycle, start mission control from the repository.")

SHIM = """// Static replay: serve the recorded session from files instead of the live API.
(function () {
  const files = [
    [/^\\/api\\/sessions$/, "/data/sessions.json"],
    [/^\\/api\\/sessions\\/[^/]+\\/events$/, "/data/events.json"],
    [/^\\/api\\/study\\//, "/data/study.json"],
    [/^\\/api\\/fires$/, "/data/fires.json"],
  ];
  const real = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if ((init.method || "GET") !== "GET" && url.pathname.startsWith("/api/")) {
      return Promise.resolve(new Response(JSON.stringify({ detail: "this page is a replay" }), { status: 409 }));
    }
    const hit = files.find(([re]) => re.test(url.pathname));
    return real(hit ? hit[1] : input, init);
  };
  const p = new URLSearchParams(location.search);
  if (!p.get("replay")) {
    p.set("session", "%SESSION%");
    p.set("replay", "1");
    p.set("speed", "12");
    history.replaceState(null, "", "?" + p.toString());
  }
  document.addEventListener("DOMContentLoaded", () => {
    const run = document.getElementById("run");
    run.disabled = true;
    run.title = "This page is a replay; live cycles run from the repository.";
    run.textContent = "Replay only";
    const bar = document.createElement("div");
    bar.className = "replay-note";
    bar.innerHTML = `%BANNER% <a href="?session=%SESSION%&replay=1&speed=12">Replay again</a>`;
    document.body.prepend(bar);
  });
})();
"""

NOTE_CSS = """
.replay-note { background: #3b2a06; color: #fde68a; border-bottom: 1px solid #8a6a1f; padding: 8px 22px; font-size: 13px; }
.replay-note a { color: #fff; margin-left: 8px; }
.grid { height: calc(100vh - 120px); }
"""


def jpg(src: Path, dst: Path, width: int = 900) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    im = Image.open(src).convert("RGB")
    im.thumbnail((width, width))
    im.save(dst, "JPEG", quality=74, optimize=True)


def small_mp4(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", "scale=800:-2", "-c:v", "libx264",
                    "-crf", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)], check=True)


def main(session: str = "cycle-5", out: str = "../hack-metam-static"):
    out = Path(out).resolve()
    for sub in ("data", "runs", "assets"):
        shutil.rmtree(out / sub, ignore_errors=True)
    (out / "data").mkdir(parents=True)

    events = web.events(session)["events"]
    study = web.study("lake-mead", session=session)
    fires = web.fires(session=session)
    sessions = [s for s in web.sessions() if s["id"] == session]
    started = sessions[0]["started"] if sessions else None
    date = __import__("time").strftime("%B %-d, %Y", __import__("time").gmtime(started)) if started else "recently"

    copied: set[str] = set()

    def static_path(run_rel: str) -> str:
        """runs/<rel>.png → /runs/<rel>.jpg in the site (converted once)."""
        rel = run_rel.split("/runs/")[-1].lstrip("/")
        src = RUNS_DIR / rel
        if src.suffix == ".png":
            dst_rel = rel[:-4] + ".jpg"
            if dst_rel not in copied and src.exists():
                jpg(src, out / "runs" / dst_rel)
                copied.add(dst_rel)
            return "/runs/" + dst_rel
        if src.suffix == ".mp4":
            if rel not in copied and src.exists():
                small_mp4(src, out / "runs" / rel)
                copied.add(rel)
            return "/runs/" + rel
        return "/runs/" + rel

    for e in events:
        if e.get("images"):
            e["images"] = [static_path(p) for p in e["images"]]
    for v in study["versions"]:
        for p in v["points"]:
            p["evidence"] = static_path(p["evidence"])
        if v.get("timelapse"):
            v["timelapse"] = static_path(v["timelapse"])
    for f in fires.values():
        for v in f["versions"]:
            v["evidence"] = {k: static_path(p) for k, p in v["evidence"].items()}

    (out / "data" / "events.json").write_text(json.dumps({"events": events, "next": len(events)}))
    (out / "data" / "study.json").write_text(json.dumps(study))
    (out / "data" / "fires.json").write_text(json.dumps(fires))
    (out / "data" / "sessions.json").write_text(json.dumps([s | {"done": True} for s in sessions]))

    shutil.copytree(ROOT / "web" / "assets", out / "assets")
    index = (ROOT / "web" / "index.html").read_text()
    index = index.replace('<script src="/app.js"></script>', '<script src="/static-shim.js"></script>\n  <script src="/app.js"></script>')
    (out / "index.html").write_text(index)
    (out / "app.js").write_text((ROOT / "web" / "app.js").read_text())
    (out / "style.css").write_text((ROOT / "web" / "style.css").read_text() + NOTE_CSS)
    (out / "static-shim.js").write_text(SHIM.replace("%SESSION%", session).replace("%BANNER%", BANNER.format(date=date)))
    (out / "vercel.json").write_text(json.dumps({"cleanUrls": True, "trailingSlash": False}, indent=1))
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file() and ".git" not in p.parts)
    print(f"{out}: {len(events)} events, {len(copied)} media files, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(*sys.argv[1:])
