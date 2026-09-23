"""Central configuration for the brain vector layer."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BRAIN_ROOT = Path(os.environ.get("BRAIN_ROOT", Path.home() / "brain")).expanduser()
RAW_DIR = BRAIN_ROOT / "raw"
WIKI_PAGES = BRAIN_ROOT / "wiki" / "pages"
WIKI_TOP = BRAIN_ROOT / "wiki"
CHROMA_DIR = BRAIN_ROOT / ".chroma"
SCRIPTS_DIR = BRAIN_ROOT / "scripts"
LOG_PATH = SCRIPTS_DIR / "watch.log"
# launchd label of the watcher (installer writes ~/Library/LaunchAgents/<label>.plist)
WATCH_LABEL = os.environ.get("BRAIN_WATCH_LABEL", f"com.{os.environ.get('USER', 'user')}.brain-watch")

COLL_RAW = "brain_raw"
COLL_WIKI = "brain_wiki"
ALLOWED_COLLECTIONS = (COLL_RAW, COLL_WIKI)

EMBED_MODEL = "text-embedding-3-small"
EMBED_PROVIDER = "openai"

CHUNK_MAX_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 100

# Tuned 2026-06-13: text-embedding-3-small rarely exceeds ~0.75 cosine even on
# strong matches (good wiki hits land 0.52–0.76), so the old 0.75 wiki gate
# discarded almost every real wiki match and fell through to raw. 0.45 separates
# real matches from noise (irrelevant/raw tops sit ~0.40–0.50).
CONFIDENCE_THRESHOLDS = {"openai": 0.45, "local": 0.55}
TRAINING_FALLBACK_FLOOR = 0.40

QUERY_CONTEXT_BUDGET_TOKENS = 1500

MAX_FILE_MB = 50
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

WATCH_DEBOUNCE_SECONDS = 2.0

load_dotenv(BRAIN_ROOT / ".env")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
PRIVACY_STRICT = os.environ.get("BRAIN_PRIVACY_STRICT", "").strip() in ("1", "true", "on", "yes")

THRESHOLD_OVERRIDE = os.environ.get("BRAIN_CONFIDENCE_THRESHOLD")

# --- Jev (TypeSafe decision model) — optional judgement layer, added 2026-09-21 ---
# Always advisory: every caller falls back to the pre-Jev behaviour when Jev is off,
# slow, or failing. Never consulted under BRAIN_PRIVACY_STRICT. BRAIN_JEV=off disables.
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-1.13.0"  # pinned; "jev-latest" floats and can change verdicts silently
JEV_ENABLED = os.environ.get("BRAIN_JEV", "on").strip().lower() not in ("0", "off", "false", "no")
JEV_KEY_NAME = "TYPESAFE_API_KEY"  # env var or ~/brain/.env; unset = Jev off, cosine-only
JEV_LOG_PATH = SCRIPTS_DIR / "jev.log"
JEV_QUERY_TIMEOUT_S = 1.2  # hard total deadline on what a rerank can add to one query
# After this many consecutive failed calls, skip Jev for the cool-down instead of making
# every ambiguous query wait out the deadline during an outage.
JEV_BREAKER_FAILURES = 3
JEV_BREAKER_COOLDOWN_S = 600
JEV_BATCH_TIMEOUT_S = 8.0  # ingest / staleness calls (off the interactive path)
JEV_WORKERS = 8

# Pages carrying this tag are never sent to Jev (they keep the cosine-only path).
PRIVATE_TAG = "private"

# Rerank is consulted only when the best cosine score is inside this band — outside
# it the score is already decisive, so those queries pay zero extra latency. The band
# is the measured overlap: irrelevant tops reach ~0.50, real hits start ~0.40.
# Asymmetric on purpose (a wrong "not in brain" makes the assistant ignore the brain):
#   Jev may RESCUE any query in the band (some passage answers -> in brain), but may
#   REJECT only one cosine already rated weak (below the wiki gate). From the gate up,
#   a Jev "nothing answers" only marks the result confidence=low.
RERANK_BAND = (0.30, 0.50)
RERANK_IN_BRAIN_P = 0.50  # max P(passage answers question) needed for an in-brain verdict
# Jev is not deterministic: identical input moved p by ~0.1 across runs (measured 0.40-0.49 on a
# true miss). Overturning a cosine score that is below the floor therefore needs a clear margin.
RERANK_RESCUE_P = 0.65
RERANK_DROP_P = 0.15  # passages below this are dropped from an in-brain result set
RERANK_RAW_CANDIDATES = 3  # raw chunks judged alongside the top-k wiki chunks
# Chunks that are only a heading plus [[links]] (e.g. "## Cross-References") score high on
# cosine for any on-topic question and crowd out real content. Dropped at query time.
QUERY_OVERFETCH = 6
LINK_ONLY_MAX_WORDS = 3  # words left after removing headings and [[links]]

INGEST_NEIGHBORS = 3
INGEST_MAX_CHUNKS = 60
INGEST_FLAG_P = 0.50

# Review interval in days per volatility level 0..3 (evergreen → fast-moving);
# fractional scores interpolate. Flat fallback when Jev is unavailable.
STALENESS_REVIEW_DAYS = (730, 365, 90, 21)
STALENESS_FLAT_DAYS = 90
STALENESS_CACHE = SCRIPTS_DIR / ".staleness_cache.json"


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

# --- Query daemon (added 2026-09-23) — keeps chromadb imported and the index warm ---
# scripts/query.py asks it first over a unix socket and runs in-process when it is not
# up, starting one for next time. It exits after QUERY_DAEMON_IDLE_S without a query,
# restarts itself when scripts/*.py, .env or the BRAIN_* env vars change, and reopens
# the store when another process (the watcher) has written to .chroma.
# BRAIN_QUERY_DAEMON=off keeps every query in-process.
QUERY_SOCKET = BRAIN_ROOT / ".query.sock"
QUERY_LOCK = BRAIN_ROOT / ".query.lock"
QUERY_DAEMON_LOG = SCRIPTS_DIR / "query_daemon.log"
QUERY_DAEMON_IDLE_S = 900
QUERY_DAEMON_ENABLED = os.environ.get("BRAIN_QUERY_DAEMON", "on").strip().lower() not in ("0", "off", "false", "no")
