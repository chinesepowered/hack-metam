"""Official wildfire perimeters (NIFC WFIGS) — ground truth the Fire Lab is graded against."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import requests
from shapely.geometry import shape

from orbital.config import CACHE_DIR

WFIGS = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
         "WFIGS_Interagency_Perimeters_YearToDate/FeatureServer/0/query")
FIELDS = "poly_IncidentName,poly_GISAcres,attr_POOState,attr_FireDiscoveryDateTime,attr_ContainmentDateTime,attr_UniqueFireIdentifier"
ACRES_PER_KM2 = 247.105


def _day(ms):
    return str(datetime.fromtimestamp(ms / 1000, timezone.utc).date()) if ms else None


def _row(attrs: dict) -> dict:
    return {
        "name": attrs["poly_IncidentName"],
        "id": attrs.get("attr_UniqueFireIdentifier"),
        "state": (attrs.get("attr_POOState") or "").replace("US-", ""),
        "official_acres": round(attrs["poly_GISAcres"] or 0),
        "official_km2": round((attrs["poly_GISAcres"] or 0) / ACRES_PER_KM2, 1),
        "discovered": _day(attrs.get("attr_FireDiscoveryDateTime")),
        "contained": _day(attrs.get("attr_ContainmentDateTime")),
    }


def large_fires(min_acres: float = 50000, limit: int = 15) -> list[dict]:
    """This year's largest fires with mapped perimeters."""
    r = requests.get(WFIGS, params={"where": f"poly_GISAcres>{min_acres}", "outFields": FIELDS,
                                    "returnGeometry": "false", "orderByFields": "poly_GISAcres DESC",
                                    "resultRecordCount": limit, "f": "json"}, timeout=90).json()
    return [_row(f["attributes"]) for f in r.get("features", [])]


def split_name(name: str, state: str | None = None) -> tuple[str, str | None]:
    """Accept 'Tartar, ID' as well as ('Tartar', 'ID')."""
    if "," in name:
        name, state_in_name = (s.strip() for s in name.split(",", 1))
        state = state or state_in_name
    return name.strip(), (state or "").strip().upper() or None


def perimeter(name: str, state: str | None = None) -> dict:
    """The largest perimeter matching `name` (and state), with geometry in lon/lat."""
    name, state = split_name(name, state)
    key = f"{name}-{state}".lower().replace(" ", "_")
    cache = CACHE_DIR / "fires" / f"{key}.json"
    if cache.exists():
        data = json.loads(cache.read_text())
    else:
        where = f"upper(poly_IncidentName)='{name.upper().replace(chr(39), '')}'"
        if state:
            where += f" AND attr_POOState='US-{state.upper()}'"
        r = requests.get(WFIGS, params={"where": where, "outFields": FIELDS, "returnGeometry": "true", "outSR": 4326,
                                        "orderByFields": "poly_GISAcres DESC", "resultRecordCount": 1,
                                        "f": "geojson"}, timeout=120).json()
        feats = r.get("features", [])
        if not feats:
            raise ValueError(f"no perimeter found for {name!r}")
        data = feats[0]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
    props = {k.replace("poly_", "poly_"): v for k, v in data["properties"].items()}
    geom = shape(data["geometry"])
    return _row(props) | {"geometry": geom, "bbox": list(geom.bounds)}
