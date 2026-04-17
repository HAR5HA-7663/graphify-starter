"""Central configuration for the brain vector layer."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BRAIN_ROOT = Path.home() / "brain"
RAW_DIR = BRAIN_ROOT / "raw"
WIKI_PAGES = BRAIN_ROOT / "wiki" / "pages"
WIKI_TOP = BRAIN_ROOT / "wiki"
CHROMA_DIR = BRAIN_ROOT / ".chroma"
SCRIPTS_DIR = BRAIN_ROOT / "scripts"
LOG_PATH = SCRIPTS_DIR / "watch.log"

COLL_RAW = "brain_raw"
COLL_WIKI = "brain_wiki"
ALLOWED_COLLECTIONS = (COLL_RAW, COLL_WIKI)

EMBED_MODEL = "text-embedding-3-small"
EMBED_PROVIDER = "openai"

CHUNK_MAX_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 100

CONFIDENCE_THRESHOLDS = {"openai": 0.75, "local": 0.55}
TRAINING_FALLBACK_FLOOR = 0.60

QUERY_CONTEXT_BUDGET_TOKENS = 1500

MAX_FILE_MB = 50
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

WATCH_DEBOUNCE_SECONDS = 2.0

load_dotenv(BRAIN_ROOT / ".env")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
PRIVACY_STRICT = os.environ.get("BRAIN_PRIVACY_STRICT", "").strip() in ("1", "true", "on", "yes")

THRESHOLD_OVERRIDE = os.environ.get("BRAIN_CONFIDENCE_THRESHOLD")


def resolve_threshold() -> float:
    if THRESHOLD_OVERRIDE:
        return float(THRESHOLD_OVERRIDE)
    return CONFIDENCE_THRESHOLDS.get(EMBED_PROVIDER, 0.75)


def validate_or_exit() -> None:
    """Fail closed on bad config. Called by every CLI at startup."""
    if PRIVACY_STRICT and EMBED_MODEL.startswith("text-embedding-"):
        sys.exit(
            "BRAIN_PRIVACY_STRICT active — OpenAI embeddings disallowed. "
            "Switch EMBED_MODEL to a local provider in scripts/config.py."
        )
    if EMBED_PROVIDER == "openai" and not OPENAI_API_KEY:
        sys.exit(
            "OPENAI_API_KEY missing. Write one to ~/brain/.env (chmod 600) "
            "or switch EMBED_PROVIDER in scripts/config.py."
        )
