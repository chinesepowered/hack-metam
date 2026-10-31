"""Lay each scene's narration at the moment its scene started on screen, then mux with the recording.

    uv run python scripts/demo/assemble.py   # → runs/demo/orbital-demo.mp4
"""
from __future__ import annotations

import json
import subprocess
import wave

from orbital.config import RUNS_DIR

DEMO = RUNS_DIR / "demo"
SHORT = ("hook", "reject", "publish", "close")  # scenes kept in the X cut


def main():
    timeline = {s["id"]: s for s in json.loads((DEMO / "timeline.json").read_text())}
    rec = json.loads((DEMO / "video" / "marks.json").read_text())
    out_wav = DEMO / "narration.wav"
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
    out = DEMO / "orbital-demo.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", rec["video"], "-i", str(out_wav),
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)], check=True)
    print(f"{out} ({duration(out):.1f}s)")
    short_cut(out, rec["marks"], timeline)


def duration(path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(path)], capture_output=True, text=True).stdout.strip())


def short_cut(full, marks, timeline, keep=SHORT):
    """A ~45 s cut for X: whole scenes only, so every sentence of narration stays intact."""
    ends = {m["id"]: (marks[i + 1]["start"] if i + 1 < len(marks) else duration(full)) for i, m in enumerate(marks)}
    parts = []
    for m in marks:
        if m["id"] in keep:
            part = DEMO / f"part-{m['id']}.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{m['start']:.2f}", "-to", f"{ends[m['id']]:.2f}",
                            "-i", str(full), "-c:v", "libx264", "-crf", "20", "-c:a", "aac", str(part)], check=True)
            parts.append(part)
    listing = DEMO / "parts.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    out = DEMO / "orbital-x.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c", "copy", "-movflags", "+faststart", str(out)], check=True)
    for p in parts:
        p.unlink()
    listing.unlink()
    print(f"{out} ({duration(out):.1f}s)")


if __name__ == "__main__":
    main()
