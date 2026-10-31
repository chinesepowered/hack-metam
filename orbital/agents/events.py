"""Append-only event log: the institute's lab notebook, streamed live to mission control."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class EventLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, actor: str, kind: str, text: str = "", **data) -> dict:
        event = {"ts": round(time.time(), 3), "actor": actor, "kind": kind, "text": text} | data
        line = json.dumps(event, default=str)
        with self._lock, self.path.open("a") as f:
            f.write(line + "\n")
        return event

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]
