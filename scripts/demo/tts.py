"""Synthesize the demo narration with AssemblyAI text-to-speech, one WAV per scene.

    ASSEMBLYAI_API_KEY=... uv run python scripts/demo/tts.py cycle-1 [full|x] [voice_id]
"""
from __future__ import annotations

import json
import os
import sys
import wave
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from scenes import scenes, x_scenes  # noqa: E402

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


def main(session: str = "cycle-1", cut: str = "full", voice: str = VOICE):
    out = RUNS_DIR / ("demo" if cut == "full" else "demo-x") / "audio"
    out.mkdir(parents=True, exist_ok=True)
    timeline = []
    for s in (scenes if cut == "full" else x_scenes)(session):
        wav, said = out / f"{s['id']}.wav", out / f"{s['id']}.txt"
        if wav.exists() and said.exists() and said.read_text() == f"{voice}\n{s['text']}":
            with wave.open(str(wav)) as w:  # unchanged line: keep the audio already synthesized
                seconds = w.getnframes() / w.getframerate()
        else:
            seconds = synth(s["text"], voice, wav)
            said.write_text(f"{voice}\n{s['text']}")
        timeline.append(s | {"audio": str(out / f"{s['id']}.wav"), "seconds": round(seconds, 2)})
        print(f"{s['id']:8s} {seconds:5.1f}s")
    (out.parent / "timeline.json").write_text(json.dumps(timeline, indent=1))
    print(f"total {sum(t['seconds'] for t in timeline):.1f}s")


if __name__ == "__main__":
    main(*sys.argv[1:])
