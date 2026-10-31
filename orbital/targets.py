"""Places the institute studies, with their areas of interest and ground-truth sources."""
from __future__ import annotations

from orbital.config import RUNS_DIR

WATER_TARGETS = {
    "lake-mead": {
        "name": "Lake Mead (Nevada/Arizona)",
        "bbox": [-114.90, 35.98, -113.95, 36.58],
        "truth": "USBR end-of-month elevation at Hoover Dam",
        "context": "Largest reservoir in the US by capacity; supplies water to ~25M people in NV, AZ, CA and Mexico.",
    },
}


def data_dir(target: str):
    return RUNS_DIR / target
