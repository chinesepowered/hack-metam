"""Synthesize the demo narration with AssemblyAI text-to-speech, one WAV per scene.

    ASSEMBLYAI_API_KEY=... uv run python scripts/demo/tts.py cycle-1 [voice_id]
"""
from __future__ import annotations

import json
import os
import sys
import wave
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from scenes import scenes  # noqa: E402

from orbital.config import RUNS_DIR  # noqa: E402

URL = "https://tts.us.assemblyai.com/v1/speech"
VOICE = "aai-aaliyah"  # young woman, fairly high-pitched; swap for a catalog voice_id if preferred


def synth(text: str, voice: str, out: Path) -> float:
    r = requests.post(URL, headers={"Authorization": os.environ["ASSEMBLYAI_API_KEY"]}, timeout=120,
                      json={"text": text, "voice_id": voice, "container": "wav", "stream": False})
    r.raise_for_status()
    out.write_bytes(r.content)
    with wave.open(str(out)) as w:
        return w.getnframes() / w.getframerate()


def main(session: str = "cycle-1", voice: str = VOICE):
    out = RUNS_DIR / "demo" / "audio"
    out.mkdir(parents=True, exist_ok=True)
    timeline = []
    for s in scenes(session):
        seconds = synth(s["text"], voice, out / f"{s['id']}.wav")
        timeline.append(s | {"audio": str(out / f"{s['id']}.wav"), "seconds": round(seconds, 2)})
        print(f"{s['id']:8s} {seconds:5.1f}s")
    (out.parent / "timeline.json").write_text(json.dumps(timeline, indent=1))
    print(f"total {sum(t['seconds'] for t in timeline):.1f}s")


if __name__ == "__main__":
    main(*sys.argv[1:])
