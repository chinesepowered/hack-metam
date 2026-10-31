"""Lay each scene's narration at the moment its scene started on screen, then mux with the recording.

    uv run python scripts/demo/assemble.py     # → runs/demo/orbital-demo.mp4
    uv run python scripts/demo/assemble.py x   # → runs/demo/orbital-x.mp4 (from runs/demo-x)
"""
from __future__ import annotations

import json
import subprocess
import sys
import wave

from orbital.config import RUNS_DIR

DEMO = RUNS_DIR / "demo"


def main(cut: str = "full"):
    src = DEMO if cut == "full" else RUNS_DIR / "demo-x"  # the X cut has its own script, audio and recording
    timeline = {s["id"]: s for s in json.loads((src / "timeline.json").read_text())}
    rec = json.loads((src / "video" / "marks.json").read_text())
    out_wav = src / "narration.wav"
    rate = None
    frames = bytearray()
    for mark in rec["marks"]:
        with wave.open(timeline[mark["id"]]["audio"]) as w:
            rate = rate or w.getframerate()
            assert w.getframerate() == rate and w.getsampwidth() == 2 and w.getnchannels() == 1
            pcm = w.readframes(w.getnframes())
        start = int((mark["start"] + 0.25) * rate) * 2  # small lead-in so the voice follows the cut
        if len(frames) < start:
            frames.extend(b"\x00" * (start - len(frames)))
        frames[start:start + len(pcm)] = pcm
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    out = DEMO / ("orbital-demo.mp4" if cut == "full" else "orbital-x.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", rec["video"], "-i", str(out_wav),
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)], check=True)
    print(f"{out} ({duration(out):.1f}s)")


def duration(path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(path)], capture_output=True, text=True).stdout.strip())


if __name__ == "__main__":
    main(*sys.argv[1:2])
