"""Deterministic number audit: a claimed value must appear in the lab's own tool outputs."""
from __future__ import annotations

import json
import re

NUM = re.compile(r"-?\d+(?:\.\d+)?")


def numbers_in(obj) -> list[float]:
    return [float(x) for x in NUM.findall(json.dumps(obj, default=str))]


def supported(value: float, pool: list[float], rel: float = 0.006, abs_tol: float = 0.051) -> bool:
    """Match within rounding: 252.0 is supported by 251.9816; 2.3 by 2.31."""
    return any(abs(value - x) <= max(abs_tol, rel * abs(x)) for x in pool)


def audit_finding(finding: dict, tool_results: list[dict]) -> list[dict]:
    pool = numbers_in(tool_results)
    checks = []
    for claim in finding.get("claims", []):
        value = claim.get("value")
        if not isinstance(value, (int, float)):
            checks.append({"statement": claim.get("statement", ""), "status": "no_value"})
            continue
        ok = supported(float(value), pool)
        checks.append({"statement": claim.get("statement", ""), "value": value,
                       "status": "traced" if ok else "untraced",
                       "note": "" if ok else "value not found in any tool output this lab received"})
    return checks
