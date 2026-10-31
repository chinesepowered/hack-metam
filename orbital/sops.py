"""The institute's standard operating procedures: one current version per lab, with history.

When the Director issues a new SOP, every lab's next measurement follows it, and results are stored
per SOP version so before/after comparisons stay possible.
"""
from __future__ import annotations

import dataclasses
import threading

from orbital.instruments.water import SOP_FIELDS, WaterSOP

_lock = threading.Lock()
_current = {"water": WaterSOP()}
_history: list[dict] = [{"lab": "water", "version": WaterSOP().version, "changes": {}, "reason": "initial SOP"}]

FIELDS = {"water": SOP_FIELDS}


def current(lab: str = "water") -> WaterSOP:
    with _lock:
        return _current[lab]


def issue(lab: str, changes: dict, reason: str) -> dict:
    """New version with `changes` applied; unknown fields are rejected."""
    with _lock:
        old = _current[lab]
        bad = [k for k in changes if k not in FIELDS[lab]]
        if bad:
            raise ValueError(f"unknown SOP fields {bad}; allowed: {list(FIELDS[lab])}")
        n = int(old.version.rsplit("-", 1)[1]) + 1
        typed = {k: type(getattr(old, k))(v) if not isinstance(getattr(old, k), bool)
                 else str(v).lower() in ("true", "1", "yes") for k, v in changes.items()}
        new = dataclasses.replace(old, version=f"{lab}-sop-{n}", **typed)
        _current[lab] = new
        entry = {"lab": lab, "version": new.version, "previous": old.version, "changes": typed, "reason": reason}
        _history.append(entry)
        return entry


def history() -> list[dict]:
    with _lock:
        return list(_history)


def reset() -> None:
    with _lock:
        _current["water"] = WaterSOP()
        del _history[1:]
