"""ESA WorldCover 2021 (10 m land cover, public on AWS) resampled onto a scene's grid."""
from __future__ import annotations

import math

import numpy as np
import rasterio
import xarray as xr
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import from_bounds

URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
CROPLAND, WATER, BUILT = 40, 80, 50


def _tiles(lon0, lat0, lon1, lat1):
    """3°×3° tiles are named by their south-west corner, e.g. N42W120 covers 42–45°N, 120–117°W."""
    for lat in range(math.floor(lat0 / 3) * 3, math.floor(lat1 / 3) * 3 + 1, 3):
        for lon in range(math.floor(lon0 / 3) * 3, math.floor(lon1 / 3) * 3 + 1, 3):
            yield f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


def classes_on(ds: xr.Dataset) -> np.ndarray:
    """WorldCover class per pixel of `ds` (0 where no tile covers it)."""
    gb = ds.odc.geobox
    dst = np.zeros(gb.shape, np.uint8)
    lon0, lat0, lon1, lat1 = transform_bounds(f"EPSG:{gb.crs.epsg}", "EPSG:4326", *gb.extent.boundingbox)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        for tile in _tiles(lon0, lat0, lon1, lat1):
            try:
                src = rasterio.open(URL.format(tile=tile))
            except rasterio.errors.RasterioIOError:
                continue  # ocean tiles do not exist
            with src:
                win = from_bounds(lon0, lat0, lon1, lat1, src.transform).round_offsets().round_lengths()
                data = src.read(1, window=win, boundless=True, fill_value=0)
                part = np.zeros_like(dst)
                reproject(data, part, src_transform=src.window_transform(win), src_crs=src.crs,
                          dst_transform=gb.affine, dst_crs=f"EPSG:{gb.crs.epsg}",
                          resampling=Resampling.nearest, src_nodata=0, dst_nodata=0)
                dst = np.where(part > 0, part, dst)
    return dst
