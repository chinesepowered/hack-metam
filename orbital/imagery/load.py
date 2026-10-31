"""Load an area of interest from one day's Sentinel-2 tiles onto a single UTM grid, in reflectance."""
from __future__ import annotations

import numpy as np
import odc.stac
import pystac
import xarray as xr

BANDS = ("blue", "green", "red", "nir", "swir16", "swir22", "scl")
SCL_NODATA, SCL_CLOUD_SHADOW, SCL_WATER, SCL_SNOW = 0, 3, 6, 11
SCL_CLOUD = (3, 8, 9, 10)  # shadow, medium/high probability cloud, thin cirrus

odc.stac.configure_rio(cloud_defaults=True)


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def load_day(items: list[pystac.Item], bbox, resolution: float = 20, bands=BANDS) -> xr.Dataset:
    """Mosaic a day's tiles over `bbox`; reflectance bands become float32, SCL stays categorical."""
    lon, lat = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    ds = odc.stac.load(
        items,
        bands=list(bands),
        bbox=bbox,
        crs=f"EPSG:{utm_epsg(lon, lat)}",
        resolution=resolution,
        groupby="solar_day",
        resampling={"scl": "nearest", "*": "bilinear"},
        pool=8,
    ).isel(time=0)
    out = {}
    for band in bands:
        raw = ds[band]
        if band == "scl":
            out[band] = raw.astype(np.uint8)
            continue
        scale, offset, nodata = _scaling(items[0], band)
        refl = raw.astype(np.float32) * scale + offset
        out[band] = refl.where(raw != nodata).clip(min=0.0)
    res = xr.Dataset(out, attrs={"date": str(items[0].datetime.date()), "resolution_m": resolution,
                                 "items": [it.id for it in items]})
    res.attrs["pixel_area_m2"] = float(resolution * resolution)
    return res


def _scaling(item: pystac.Item, band: str) -> tuple[float, float, int]:
    """Reflectance = DN·scale + offset.

    ESA baseline ≥ 04.00 adds +1000 to DNs. Earth Search strips it on ingest
    (`earthsearch:boa_offset_applied`) yet still lists offset −0.1 in raster:bands;
    water pixels (SWIR DN ≈ 90) confirm no offset remains, so only subtract it when not yet applied.
    """
    rb = (item.assets[band].extra_fields.get("raster:bands") or [{}])[0]
    scale, nodata = float(rb.get("scale", 1e-4)), int(rb.get("nodata", 0))
    baseline = float(item.properties.get("s2:processing_baseline", "0") or 0)
    applied = bool(item.properties.get("earthsearch:boa_offset_applied", False))
    offset = -0.1 if baseline >= 4.0 and not applied else 0.0
    return scale, offset, nodata


def cloud_fraction(ds: xr.Dataset, region: np.ndarray | None = None) -> float:
    """Share of valid pixels flagged cloud/shadow/cirrus by ESA's scene classification, optionally within a region."""
    scl = ds.scl.values
    valid = scl != SCL_NODATA
    if region is not None:
        valid &= region
    if not valid.any():
        return 1.0
    return float(np.isin(scl[valid], SCL_CLOUD).mean())


def nodata_fraction(ds: xr.Dataset) -> float:
    return float((ds.scl.values == SCL_NODATA).mean())


def lonlat_to_pixel(ds: xr.Dataset, lon: float, lat: float) -> tuple[int, int]:
    from pyproj import Transformer

    crs = ds.odc.crs if hasattr(ds, "odc") else None
    epsg = crs.epsg if crs is not None and crs.epsg else utm_epsg(lon, lat)
    x, y = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform(lon, lat)
    col = int(np.abs(ds.x.values - x).argmin())
    row = int(np.abs(ds.y.values - y).argmin())
    return row, col
