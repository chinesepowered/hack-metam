"""Paths, endpoints and model IDs. Secrets come only from environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = Path(os.environ.get("ORBITAL_RUNS_DIR", ROOT / "runs"))
CACHE_DIR = Path(os.environ.get("ORBITAL_CACHE_DIR", ROOT / ".cache"))

STAC_URL = "https://earth-search.aws.element84.com/v1"
S2_COLLECTION = "sentinel-2-l2a"

# Muse Spark Open (open weights) served by Baseten for the hackathon.
BASETEN_BASE_URL = "https://inference.baseten.co/v1"
SPARK_OPEN_MODEL = "meta-models/muse-spark-open-1026"

# Meta Model API (SAM 3.1, Muse Image, Muse Spark/Wonder).
META_BASE_URL = "https://api.meta.ai/v1"
SAM_MODEL = "sam-3.1"
MUSE_IMAGE_MODEL = "muse-image-1.0"


def baseten_key() -> str | None:
    return os.environ.get("BASETEN_API_KEY")


def meta_key() -> str | None:
    return os.environ.get("MODEL_API_KEY")
