"""Spectral indices: physics-based instruments that cross-check what SAM sees."""
from __future__ import annotations

import numpy as np
import xarray as xr


def nd(a, b):
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    return (a - b) / np.maximum(a + b, 1e-6)


def mndwi(ds: xr.Dataset) -> np.ndarray:
    """Modified NDWI (Xu 2006): green vs SWIR — water is bright in green, near-black in SWIR."""
    return nd(ds.green.values, ds.swir16.values)


def ndwi(ds: xr.Dataset) -> np.ndarray:
    """McFeeters NDWI: green vs NIR."""
    return nd(ds.green.values, ds.nir.values)


def ndvi(ds: xr.Dataset) -> np.ndarray:
    return nd(ds.nir.values, ds.red.values)


def nbr(ds: xr.Dataset) -> np.ndarray:
    """Normalized Burn Ratio: NIR vs SWIR2. dNBR = NBR_pre − NBR_post maps burn severity."""
    return nd(ds.nir.values, ds.swir22.values)
