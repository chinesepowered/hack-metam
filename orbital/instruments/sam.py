"""Vision instrument: Meta's Segment Anything 2.1 (open weights, Apache-2.0, run locally on CPU) outlines the lake
in each month's true-colour image. Its only prompts are a few points that ESA WorldCover maps as permanent water,
so it never sees a spectral index, and it fails in different ways than the water index does. That makes it an
independent check.

Needs the optional extra: uv sync --extra sam
"""
from __future__ import annotations

import json
import os
import threading
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from skimage.segmentation import find_boundaries

from orbital.config import CACHE_DIR
from orbital.imagery import render
from orbital.imagery.load import grid
from orbital.targets import WATER_TARGETS, data_dir
from orbital.truth.landcover import WATER, classes_on

MODEL_ID = "facebook/sam2.1-hiera-small"
DRY = 110  # a prompt point brighter than this (0–255 grey) shows land or cloud, not lake
_lock = threading.Lock()  # one image at a time: the model already uses every core


def available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache(maxsize=1)
def _model():
    os.environ.setdefault("HF_HOME", str(CACHE_DIR / "hf"))  # weights download once, next to the other caches
    from transformers import Sam2Model, Sam2Processor
    from transformers.utils import logging
    logging.set_verbosity_error()  # the checkpoint is the video variant; its image weights load as Sam2Model
    return Sam2Model.from_pretrained(MODEL_ID).eval(), Sam2Processor.from_pretrained(MODEL_ID)


def deep_points(water: np.ndarray, pixel_m: float, n: int = 10, min_sep_m: float = 4800,
                min_depth_m: float = 400) -> list[tuple[int, int]]:
    """Up to n points deep inside the largest water body, spread across its basins (row, col)."""
    labels, k = ndimage.label(water)
    if not k:
        return []
    lake = labels == 1 + int(np.argmax(ndimage.sum(water, labels, range(1, k + 1))))
    depth = ndimage.distance_transform_edt(lake) * pixel_m
    sep = (min_sep_m / pixel_m) ** 2
    chosen: list[tuple[int, int]] = []
    for idx in np.argsort(depth.ravel())[::-1]:
        r, c = divmod(int(idx), lake.shape[1])
        if depth[r, c] < min_depth_m or len(chosen) == n:
            break
        if all((r - a) ** 2 + (c - b) ** 2 > sep for a, b in chosen):
            chosen.append((r, c))
    return chosen


def prompts(target: str, resolution: float = 40) -> dict:
    """The target's prompt points on its measurement grid, from ESA WorldCover 2021 (cached per target)."""
    path = data_dir(target) / f"sam_prompts_{resolution:g}m.json"
    if path.exists():
        return json.loads(path.read_text())
    gb = grid(WATER_TARGETS[target]["bbox"], resolution)
    points = deep_points(classes_on(gb) == WATER, resolution)
    out = {"source": "ESA WorldCover 2021 permanent water, deepest points of the largest water body",
           "grid_shape": list(gb.shape), "points_rc": points}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def wet_points(rgb: np.ndarray, points_rc: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The prompt points that look like water in this image (dark), not land or cloud (bright)."""
    gray = ndimage.median_filter(rgb.mean(axis=2), size=5)
    return [(r, c) for r, c in points_rc if gray[int(r), int(c)] < DRY]


def segment(rgb: np.ndarray, points_rc: list[tuple[float, float]]) -> tuple[np.ndarray, float]:
    """SAM's mask of the one object under all the points (on the image's own pixels) and SAM's own quality score.

    Points that look dry or cloudy in this image (bright in every colour) are dropped first: a lake that has
    shrunk away from a prompt must not drag the shore into the mask."""
    import torch

    keep = wet_points(rgb, points_rc)
    if not keep:
        return np.zeros(rgb.shape[:2], bool), 0.0
    with _lock, torch.no_grad():
        model, proc = _model()
        inputs = proc(images=Image.fromarray(rgb), input_points=[[[[c, r] for r, c in keep]]],
                      input_labels=[[[1] * len(keep)]], return_tensors="pt")
        out = model(**inputs, multimask_output=False)
    mask = proc.post_process_masks(out.pred_masks, inputs["original_sizes"])[0][0, 0].numpy().astype(bool)
    return mask, float(out.iou_scores[0, 0, 0])


def outline_month(target: str, day: str, chip_path: Path, grid_shape: tuple[int, int], resolution: float = 40) -> dict:
    """SAM's lake mask for one month's image, on the measurement grid, with its area. A date's image is the same
    under every SOP, so results are cached per target and date."""
    cache = data_dir(target) / "sam" / f"{resolution:g}m"
    meta_path, mask_path = cache / f"{day}.json", cache / f"{day}.npy"
    h, w = grid_shape
    if meta_path.exists() and mask_path.exists():
        meta = json.loads(meta_path.read_text())
        return meta | {"mask": np.unpackbits(np.load(mask_path))[: h * w].reshape(h, w).astype(bool)}
    p = prompts(target, resolution)
    chip = np.asarray(Image.open(chip_path).convert("RGB"))
    sy, sx = chip.shape[0] / h, chip.shape[1] / w
    t0 = time.time()
    mask, score = segment(chip, [(r * sy, c * sx) for r, c in p["points_rc"]])
    on_grid = np.asarray(Image.fromarray(mask).resize((w, h), Image.NEAREST))
    meta = {"date": day, "area_km2": round(float(on_grid.sum() * resolution ** 2 / 1e6), 2),
            "sam_score": round(score, 3), "seconds": round(time.time() - t0, 2)}
    cache.mkdir(parents=True, exist_ok=True)
    np.save(mask_path, np.packbits(on_grid))
    meta_path.write_text(json.dumps(meta))
    return meta | {"mask": on_grid}


def compare_image(chip_path: Path, instrument: np.ndarray, sam_mask: np.ndarray, title: str, legend: str) -> np.ndarray:
    """The month's image with both outlines: cyan = the water-index instrument, magenta = SAM."""
    chip = np.asarray(Image.open(chip_path).convert("RGB"))
    size = (chip.shape[1], chip.shape[0])
    on_chip = lambda m: np.asarray(Image.fromarray(m).resize(size, Image.NEAREST))  # noqa: E731
    img = render.overlay(chip, on_chip(instrument), color=(0, 229, 255), alpha=0.35)
    img[ndimage.binary_dilation(find_boundaries(on_chip(sam_mask), mode="inner"))] = (255, 40, 200)
    return render.caption(img, title, legend)
