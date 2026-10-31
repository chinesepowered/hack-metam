"""Fire Lab instrument: burn scars from the change in Normalized Burn Ratio (dNBR) between a
pre-fire and a post-fire Sentinel-2 scene, graded against the official perimeter."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

import numpy as np
import xarray as xr
from scipy import ndimage

from orbital.imagery import indices
from orbital.imagery.load import SCL_CLOUD, SCL_NODATA, SCL_WATER
from orbital.instruments.water import iou
from orbital.truth.landcover import CROPLAND

ACRES_PER_KM2 = 247.105
# USGS FIREMON dNBR severity classes (Key & Benson 2006)
SEVERITY = [("low", 0.10, 0.27), ("moderate-low", 0.27, 0.44), ("moderate-high", 0.44, 0.66), ("high", 0.66, 9.0)]


@dataclass(frozen=True)
class FireSOP:
    version: str = "fire-sop-1"
    resolution_m: float = 20.0
    dnbr_threshold: float = 0.10
    min_patch_km2: float = 0.05
    buffer_km: float = 3.0
    max_cloud: float = 0.03
    max_nodata: float = 0.01
    pre_window_days: int = 60
    post_window_days: int = 45
    # Off in v1. Domain: count burned pixels only within this distance of the official perimeter
    # (excludes neighbouring fires and farmland). Offset: subtract the median dNBR of an unburned ring
    # 2–5 km outside the perimeter, removing seasonal drying between the two scenes (Key 2006).
    analysis_buffer_km: float = 0.0
    phenology_offset: bool = False
    # Off by default: drop ESA WorldCover cropland from the burn map and from the perimeter's burnable area
    # (harvest between the two scenes looks like a burn in dNBR).
    exclude_cropland: bool = False

    def key(self) -> str:
        """Optional fields left at their defaults are excluded, so adding one never invalidates older results."""
        params = {k: v for k, v in asdict(self).items()
                  if k != "version" and not (k in OPTIONAL_FIELDS and v == OPTIONAL_FIELDS[k])}
        return f"{self.version}-{hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:6]}"


OPTIONAL_FIELDS = {"exclude_cropland": False}


SOP_FIELDS = {
    "dnbr_threshold": "dNBR above which a pixel counts as burned (default 0.10, USGS low-severity bound)",
    "analysis_buffer_km": "count burned pixels only within this distance of the official perimeter; 0 = whole scene (default 0)",
    "phenology_offset": "subtract the median dNBR of an unburned ring 2–5 km outside the perimeter (default false)",
    "max_cloud": "max cloud share over the fire area before a scene pair is rejected (default 0.03)",
    "exclude_cropland": "drop ESA WorldCover 2021 cropland from the burn map and from the perimeter's burnable area "
                        "(default false)",
}


@dataclass
class BurnMeasurement:
    burned_km2: float
    burned_acres: float
    burned_inside_perimeter_km2: float
    official_km2: float
    iou: float
    precision: float
    recall: float
    severity_km2: dict[str, float]
    cloud_pre: float
    cloud_post: float
    nodata: float
    offset_applied: float
    burned_outside_perimeter_km2: float
    usable: bool
    issues: list[str]
    burned: np.ndarray = field(repr=False)
    dnbr: np.ndarray = field(repr=False)

    def summary(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()
                if k not in ("burned", "dnbr")} | {"severity_km2": {k: round(v, 2) for k, v in self.severity_km2.items()}}


def measure_burn(pre: xr.Dataset, post: xr.Dataset, perimeter: np.ndarray, official_km2: float,
                 sop: FireSOP = FireSOP(), landcover: np.ndarray | None = None) -> BurnMeasurement:
    px_km2 = pre.attrs["pixel_area_m2"] / 1e6
    cropland = None
    if sop.exclude_cropland:
        if landcover is None:
            raise ValueError("exclude_cropland needs the land-cover map")
        cropland = landcover == CROPLAND
        perimeter = perimeter & ~cropland  # score against the burnable part of the official perimeter
    def bad(ds):
        s = ds.scl.values
        return np.isin(s, SCL_CLOUD) | (s == SCL_NODATA)
    nodata = (pre.scl.values == SCL_NODATA) | (post.scl.values == SCL_NODATA)
    water = (pre.scl.values == SCL_WATER) | (post.scl.values == SCL_WATER)
    dnbr = indices.nbr(pre) - indices.nbr(post)
    valid = ~bad(pre) & ~bad(post) & np.isfinite(dnbr) & ~water
    px_m = float(np.sqrt(pre.attrs["pixel_area_m2"]))
    dist_m = ndimage.distance_transform_edt(~perimeter) * px_m  # metres from the official perimeter

    offset = 0.0
    if sop.phenology_offset:
        ring = (dist_m > 2000) & (dist_m <= 5000) & valid
        if ring.sum() > 1000:
            offset = float(np.median(dnbr[ring]))
            dnbr = dnbr - offset
    domain = valid
    if sop.analysis_buffer_km > 0:
        domain = valid & (dist_m <= sop.analysis_buffer_km * 1000)

    if cropland is not None:
        domain = domain & ~cropland
    burned = (dnbr > sop.dnbr_threshold) & domain
    burned = ndimage.binary_opening(burned, iterations=1)
    labels, n = ndimage.label(burned)
    if n:
        sizes = ndimage.sum(burned, labels, index=np.arange(1, n + 1)) * px_km2
        burned = np.isin(labels, [i + 1 for i, s in enumerate(sizes) if s >= sop.min_patch_km2])

    inside = burned & perimeter
    cloud_pre = float(np.isin(pre.scl.values, SCL_CLOUD)[perimeter].mean()) if perimeter.any() else 1.0
    cloud_post = float(np.isin(post.scl.values, SCL_CLOUD)[perimeter].mean()) if perimeter.any() else 1.0
    nd = float(nodata[perimeter].mean()) if perimeter.any() else 1.0
    issues = []
    if max(cloud_pre, cloud_post) > sop.max_cloud:
        issues.append(f"clouds over the fire area (pre {cloud_pre:.1%}, post {cloud_post:.1%})")
    if nd > sop.max_nodata:
        issues.append(f"partial coverage: {nd:.1%} of the fire area has no data")
    severity = {name: float(((dnbr >= lo) & (dnbr < hi) & burned).sum() * px_km2) for name, lo, hi in SEVERITY}
    burned_km2 = float(burned.sum() * px_km2)
    return BurnMeasurement(
        burned_km2=burned_km2,
        burned_acres=burned_km2 * ACRES_PER_KM2,
        burned_inside_perimeter_km2=float(inside.sum() * px_km2),
        official_km2=official_km2,
        iou=iou(burned, perimeter),
        precision=float(inside.sum() / burned.sum()) if burned.any() else 0.0,
        recall=float(inside.sum() / perimeter.sum()) if perimeter.any() else 0.0,
        severity_km2=severity,
        cloud_pre=cloud_pre,
        cloud_post=cloud_post,
        nodata=nd,
        offset_applied=offset,
        burned_outside_perimeter_km2=float((burned & ~perimeter).sum() * px_km2),
        usable=not issues,
        issues=issues,
        burned=burned,
        dnbr=dnbr,
    )
