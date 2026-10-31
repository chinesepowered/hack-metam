"""Find Sentinel-2 L2A scenes on Earth Search (AWS open data, no key needed)."""
from __future__ import annotations

import random
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pystac
from pystac_client import Client
from pystac_client.exceptions import APIError

from orbital.config import S2_COLLECTION, STAC_URL


def search_scenes(bbox, start: str, end: str, max_cloud: float = 20.0) -> list[pystac.Item]:
    """All scenes intersecting `bbox` ([minlon, minlat, maxlon, maxlat]) between ISO dates.

    Multi-year ranges are split per year and searched in parallel: a single paged search over
    ~5,000 items is slow, and a capped one silently drops the oldest years.
    """
    y0, y1 = int(start[:4]), int(end[:4])
    spans = [(start if y == y0 else f"{y}-01-01", end if y == y1 else f"{y}-12-31") for y in range(y0, y1 + 1)]

    def one(span):
        for attempt in range(4):
            try:
                search = Client.open(STAC_URL).search(
                    collections=[S2_COLLECTION], bbox=bbox, datetime=f"{span[0]}/{span[1]}",
                    query={"eo:cloud_cover": {"lt": max_cloud}}, max_items=None, limit=100,
                )
                return list(search.items())
            except APIError:  # Earth Search answers occasional 500s under load
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt + random.random())

    with ThreadPoolExecutor(max_workers=min(4, len(spans))) as pool:
        items = [it for chunk in pool.map(one, spans) for it in chunk]
    return _latest_processing(items)


def _latest_processing(items: list[pystac.Item]) -> list[pystac.Item]:
    """Earth Search can hold several processings of one tile-day (…_0_L2A, …_1_L2A); keep the newest."""
    best: dict[tuple, pystac.Item] = {}
    for it in items:
        key = (it.properties.get("grid:code") or it.properties.get("s2:mgrs_tile"), it.datetime.date())
        seq = int(it.properties.get("s2:sequence", 0) or 0)
        if key not in best or seq > int(best[key].properties.get("s2:sequence", 0) or 0):
            best[key] = it
    return list(best.values())


def group_by_day(items: list[pystac.Item]) -> dict[date, list[pystac.Item]]:
    groups: dict[date, list[pystac.Item]] = defaultdict(list)
    for it in items:
        groups[it.datetime.date()].append(it)
    return dict(sorted(groups.items()))


def tile_cloud_cover(items: list[pystac.Item]) -> float:
    """Mean scene-level cloud cover (%) across a day's tiles — a cheap prefilter before loading pixels."""
    vals = [float(it.properties.get("eo:cloud_cover", 100.0)) for it in items]
    return sum(vals) / len(vals) if vals else 100.0
