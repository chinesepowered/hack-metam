"""Turn reflectance and masks into evidence images and time-lapse videos."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import xarray as xr
from PIL import Image, ImageDraw, ImageFont
from skimage.segmentation import find_boundaries
from skimage.transform import resize

FONT_PATHS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf")


def true_color(ds: xr.Dataset, gain: float = 3.2, gamma: float = 0.85) -> np.ndarray:
    """Fixed stretch so every date in a time-lapse is directly comparable."""
    rgb = np.stack([ds.red.values, ds.green.values, ds.blue.values], axis=-1)
    rgb = np.nan_to_num(rgb, nan=0.0)
    rgb = np.clip(rgb * gain, 0, 1) ** gamma
    return (rgb * 255).astype(np.uint8)


def fit(img: np.ndarray, max_side: int = 1024) -> np.ndarray:
    h, w = img.shape[:2]
    s = max_side / max(h, w)
    if s >= 1:
        return img
    shape = (round(h * s), round(w * s)) + img.shape[2:]
    if img.dtype == bool:
        return resize(img.astype(np.float32), shape, order=0) > 0.5
    return (resize(img, shape, order=1, preserve_range=True, anti_aliasing=True)).astype(img.dtype)


def overlay(rgb: np.ndarray, mask: np.ndarray, color=(0, 229, 255), alpha: float = 0.28, outline: bool = True) -> np.ndarray:
    out = rgb.astype(np.float32).copy()
    m = mask.astype(bool)
    out[m] = out[m] * (1 - alpha) + np.array(color, np.float32) * alpha
    if outline:
        edge = find_boundaries(m, mode="outer")
        out[edge] = color
    return out.astype(np.uint8)


def _font(size: int):
    for p in FONT_PATHS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def caption(img: np.ndarray, text: str, sub: str | None = None) -> np.ndarray:
    im = Image.fromarray(img)
    d = ImageDraw.Draw(im, "RGBA")
    size = max(14, im.width // 32)
    f, fs = _font(size), _font(int(size * 0.7))
    pad = size // 2
    box_h = size + pad * 2 + (int(size * 0.9) if sub else 0)
    d.rectangle([0, 0, im.width, box_h], fill=(5, 12, 24, 170))
    d.text((pad, pad), text, font=f, fill=(255, 255, 255, 255))
    if sub:
        d.text((pad, pad + size + 2), sub, font=fs, fill=(170, 220, 255, 255))
    return np.asarray(im)


def save_png(img: np.ndarray, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(path, optimize=True)
    return path


def frames_to_mp4(frames: list[Path], out: Path, fps: float = 4.0) -> Path:
    """H.264 MP4 — the container/codec SAM 3.1 accepts for video tracking."""
    out.parent.mkdir(parents=True, exist_ok=True)
    listfile = out.with_suffix(".frames.txt")
    listfile.write_text("".join(f"file '{p.resolve()}'\nduration {1 / fps:.4f}\n" for p in frames)
                        + f"file '{frames[-1].resolve()}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listfile),
         "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p", "-c:v", "libx264", "-r", str(fps),
         "-movflags", "+faststart", str(out)],
        check=True,
    )
    listfile.unlink(missing_ok=True)
    return out
