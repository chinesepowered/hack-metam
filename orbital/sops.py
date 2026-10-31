"""The institute's standard operating procedures: one current version per lab, with history.

When the Director issues a new SOP, every lab's next measurement follows it, and results are stored
per SOP (version + parameter hash) so before/after comparisons stay possible.
"""
from __future__ import annotations

import dataclasses
import threading

from orbital.instruments import burn, water

_lock = threading.Lock()
DEFAULTS = {"water": water.WaterSOP, "fire": burn.FireSOP}
FIELDS = {"water": water.SOP_FIELDS, "fire": burn.SOP_FIELDS}
_current = {lab: cls() for lab, cls in DEFAULTS.items()}
_history: list[dict] = []


def current(lab: str):
    with _lock:
        return _current[lab]


def issue(lab: str, changes: dict, reason: str) -> dict:
    """New version with `changes` applied; unknown fields are rejected."""
    with _lock:
        if lab not in _current:
            raise ValueError(f"unknown lab {lab!r}; labs: {list(_current)}")
        old = _current[lab]
        bad = [k for k in changes if k not in FIELDS[lab]]
        if bad:
            raise ValueError(f"unknown SOP fields {bad}; allowed: {list(FIELDS[lab])}")
        typed = {}
        for k, v in changes.items():
            cur = getattr(old, k)
            typed[k] = (str(v).lower() in ("true", "1", "yes")) if isinstance(cur, bool) else type(cur)(v)
        n = int(old.version.rsplit("-", 1)[1]) + 1
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
        for lab, cls in DEFAULTS.items():
            _current[lab] = cls()
        _history.clear()
