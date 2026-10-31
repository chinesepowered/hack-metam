"""Water Lab instrument: surface-water extent from Sentinel-2, measured by independent methods.

Primary: MNDWI > fixed threshold (physics; a fixed cut keeps a time series consistent).
Cross-checks: ESA's scene classification (SCL) water class, and a locally fitted Otsu threshold.
SAM 3.1 joins as a vision-based method when the Meta Model API key is set.
The spread between methods is reported as the measurement's uncertainty band.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

import numpy as np
import xarray as xr
from scipy import ndimage
from skimage.filters import threshold_otsu

from orbital.imagery import indices
from orbital.imagery.load import SCL_CLOUD, SCL_NODATA, SCL_WATER


@dataclass(frozen=True)
class WaterSOP:
    """Standard operating procedure. Versioned so the director can change it and every lab follows."""
    version: str = "water-sop-1"
    resolution_m: float = 40.0
    mndwi_threshold: float = 0.0
    min_body_km2: float = 1.0
    max_cloud_near_water: float = 0.01
    max_nodata: float = 0.005
    # Low-sun scenes can be over-corrected to near-zero reflectance over water, where MNDWI turns to noise.
    # When enabled, pixels dark in every band and connected to the water body count as water.
    dark_water_rule: bool = False
    dark_reflectance: float = 0.015
    # Scene quality gates (off by default): reject a scene when too much of the lake is near-black, or when the
    # independent methods disagree by more than this share of the area; the month then falls back to its next date.
    max_dark_share: float = 1.0
    max_method_spread: float = 10.0

    def key(self) -> str:
        """Version plus a hash of the parameters: caches only match when the procedure is identical.
        Gates left at their defaults are excluded, so adding a gate never invalidates older results."""
        params = {k: v for k, v in asdict(self).items()
                  if k != "version" and not (k in OPTIONAL_GATES and v == OPTIONAL_GATES[k])}
        return f"{self.version}-{hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:6]}"

    def measure_params(self) -> dict:
        """Fields that change what a scene measures (not which scenes are accepted)."""
        return {k: v for k, v in asdict(self).items() if k not in GATE_FIELDS and k != "version"}

    def passes(self, point: dict) -> bool:
        """Would a measurement made under another SOP with the same measure_params pass this SOP's gates?"""
        spread = (point["band_km2"][1] - point["band_km2"][0]) / max(point["area_km2"], 1e-6)
        return (point.get("cloud_near_water", 1.0) <= self.max_cloud_near_water
                and point.get("nodata_fraction", 1.0) <= self.max_nodata
                and point.get("dark_share", 0.0) <= self.max_dark_share
                and spread <= self.max_method_spread)


OPTIONAL_GATES = {"max_dark_share": 1.0, "max_method_spread": 10.0}
GATE_FIELDS = {"max_cloud_near_water", "max_nodata", *OPTIONAL_GATES}


SOP_FIELDS = {
    "mndwi_threshold": "MNDWI cut-off above which a pixel is water (default 0.0)",
    "min_body_km2": "smallest separate water body kept, km² (default 1.0)",
    "max_cloud_near_water": "max cloud/shadow share within 1 km of the shore before a scene is rejected (default 0.01)",
    "max_nodata": "max share of the area with no data before a scene is rejected (default 0.005)",
    "dark_water_rule": "count pixels that are near-black in all bands and connected to the lake as water (default false)",
    "dark_reflectance": "reflectance under which a pixel counts as near-black for the dark-water rule (default 0.015)",
    "max_dark_share": "reject a scene when more than this share of ESA-classified water is near-black; the month "
                      "falls back to its next clear date (default 1.0 = off)",
    "max_method_spread": "reject a scene when the methods' range exceeds this share of the measured area; the month "
                         "falls back to its next clear date (default 10.0 = off)",
}


@dataclass
class WaterMeasurement:
    date: str
    sop: str
    area_km2: float
    methods_km2: dict[str, float]
    agreement_iou: dict[str, float]
    cloud_near_water: float
    nodata_fraction: float
    dark_share: float
    usable: bool
    issues: list[str]
    warnings: list[str]
    pixel_area_m2: float
    mask: np.ndarray = field(repr=False)
    masks: dict[str, np.ndarray] = field(repr=False)

    @property
    def band_km2(self) -> tuple[float, float]:
        vals = list(self.methods_km2.values())
        return min(vals), max(vals)

    def summary(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if k not in ("mask", "masks")}
        d["band_km2"] = [round(x, 2) for x in self.band_km2]
        return _round(d)


def _round(x):
    if isinstance(x, float):
        return round(x, 4)
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_round(v) for v in x]
    return x


def iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 1.0


def keep_water_bodies(water: np.ndarray, pixel_area_m2: float, min_km2: float) -> np.ndarray:
    """Drop ponds, pools and noise: keep the largest body plus any body ≥ min_km2 (disconnected arms)."""
    labels, n = ndimage.label(water)
    if n == 0:
        return water
    sizes = ndimage.sum(water, labels, index=np.arange(1, n + 1)) * pixel_area_m2 / 1e6
    keep = {int(np.argmax(sizes)) + 1} | {i + 1 for i, s in enumerate(sizes) if s >= min_km2}
    return np.isin(labels, list(keep))


def measure_water(ds: xr.Dataset, sop: WaterSOP = WaterSOP()) -> WaterMeasurement:
    px = float(ds.attrs["pixel_area_m2"])
    km2 = px / 1e6
    scl = ds.scl.values
    valid = scl != SCL_NODATA
    m = indices.mndwi(ds)
    finite = valid & np.isfinite(m)

    dr = sop.dark_reflectance
    dark = valid & (ds.green.values < dr) & (ds.nir.values < dr) & (ds.swir16.values < 2 * dr)
    primary_raw = (m > sop.mndwi_threshold) & finite
    if sop.dark_water_rule:
        primary_raw |= dark
    masks = {
        "mndwi": keep_water_bodies(primary_raw, px, sop.min_body_km2),
        "esa_scl": keep_water_bodies((scl == SCL_WATER) & valid, px, sop.min_body_km2),
    }
    # Otsu fitted only around the shoreline, where water/land are both present in good proportion.
    shore = ndimage.binary_dilation(masks["mndwi"] | masks["esa_scl"], iterations=max(1, int(1000 / np.sqrt(px))))
    region = shore & finite
    if region.sum() > 100:
        t = float(threshold_otsu(m[region]))
        masks["otsu_local"] = keep_water_bodies((m > t) & finite, px, sop.min_body_km2)

    primary = masks["mndwi"]
    near_valid = shore & valid
    cloud = float(np.isin(scl[near_valid], SCL_CLOUD).mean()) if near_valid.any() else 1.0
    nodata = float((~valid).mean())

    issues, warnings = [], []
    if nodata > sop.max_nodata:
        issues.append(f"partial coverage: {nodata:.1%} of the area has no data")
    if cloud > sop.max_cloud_near_water:
        issues.append(f"clouds near the shoreline: {cloud:.1%}")
    esa_water = masks["esa_scl"]
    dark_share = float(dark[esa_water].mean()) if esa_water.any() else 0.0
    if dark_share > 0.05 and not sop.dark_water_rule:
        warnings.append(f"{dark_share:.0%} of ESA-classified water is near-black in every band "
                        "(possible atmospheric over-correction); MNDWI is unreliable there")
    if dark_share > sop.max_dark_share:
        issues.append(f"{dark_share:.0%} of the lake is near-black (gate {sop.max_dark_share:.0%})")
    areas = [float(v.sum() * km2) for v in masks.values()]
    primary_km2 = float(primary.sum() * km2)
    spread = (max(areas) - min(areas)) / max(primary_km2, 1e-6)
    if spread > sop.max_method_spread:
        issues.append(f"methods disagree by {spread:.0%} of the area (gate {sop.max_method_spread:.0%})")

    return WaterMeasurement(
        date=ds.attrs["date"],
        sop=sop.version,
        area_km2=float(primary.sum() * km2),
        methods_km2={k: float(v.sum() * km2) for k, v in masks.items()},
        agreement_iou={k: iou(primary, v) for k, v in masks.items() if k != "mndwi"},
        cloud_near_water=cloud,
        nodata_fraction=nodata,
        dark_share=dark_share,
        usable=not issues,
        issues=issues,
        warnings=warnings,
        pixel_area_m2=px,
        mask=primary,
        masks=masks,
    )
