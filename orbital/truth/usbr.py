"""U.S. Bureau of Reclamation records — ground truth the Water Lab is graded against."""
from __future__ import annotations

import calendar
import json
import re
from datetime import date

import requests

from orbital.config import CACHE_DIR

MEAD_URL = "https://www.usbr.gov/lc/region/g4000/hourly/mead-elv.html"


def lake_mead_monthly(refresh: bool = False) -> list[tuple[date, float]]:
    """End-of-month elevation of Lake Mead at Hoover Dam (feet above sea level), 1935–present."""
    cache = CACHE_DIR / "usbr_mead_eom.json"
    if cache.exists() and not refresh:
        return [(date.fromisoformat(d), v) for d, v in json.loads(cache.read_text())]
    html = requests.get(MEAD_URL, timeout=60).text
    series = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        cells = re.sub(r"<[^>]+>", " ", row).split()
        if not cells or not re.fullmatch(r"\d{4}", cells[0]):
            continue
        year = int(cells[0])
        for month, val in enumerate(cells[1:13], start=1):
            try:
                series.append((date(year, month, calendar.monthrange(year, month)[1]), float(val)))
            except ValueError:  # "---" placeholders
                pass
    series.sort()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps([(d.isoformat(), v) for d, v in series]))
    return series


def interpolate(series: list[tuple[date, float]], when: date) -> float | None:
    """Linear interpolation between the month-ends bracketing `when`; within 31 days of the
    newest record (USBR publishes month-ends with a lag) the latest value is used."""
    for (d0, v0), (d1, v1) in zip(series, series[1:]):
        if d0 <= when <= d1:
            f = (when - d0).days / max(1, (d1 - d0).days)
            return v0 + f * (v1 - v0)
    if series and 0 < (when - series[-1][0]).days <= 31:
        return series[-1][1]
    return None
