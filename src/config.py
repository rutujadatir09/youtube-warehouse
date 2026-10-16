"""
config.py – Central configuration for the YouTube Warehouse project.

All path constants and tuneable settings live here so the rest of the
codebase stays free of hard-coded strings.
"""

from pathlib import Path
from dotenv import load_dotenv
import os

# ── Load .env (if present) ────────────────────────────────────────────────────
load_dotenv()

# ── Root directory (one level above src/) ────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent

# ── Directory layout ──────────────────────────────────────────────────────────
RAW_DIR       = ROOT_DIR / "raw"        # JSON blobs straight from the API / generator
STAGING_DIR   = ROOT_DIR / "staging"    # Cleaned / normalised CSV / Parquet files
SQL_DIR       = ROOT_DIR / "sql"        # DDL and analytical SQL files
DATA_DIR      = ROOT_DIR / "data"       # Static reference data (channels.csv, etc.)
DASHBOARD_DIR = ROOT_DIR / "dashboard"  # Dashboard assets
DOCS_DIR      = ROOT_DIR / "docs"       # Documentation
TESTS_DIR     = ROOT_DIR / "tests"      # Pytest suite

# Ensure key runtime directories always exist
for _d in (RAW_DIR, STAGING_DIR, SQL_DIR, DATA_DIR, DASHBOARD_DIR, DOCS_DIR, TESTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── Reference files ───────────────────────────────────────────────────────────
CHANNELS_CSV = DATA_DIR / "channels.csv"

# ── Warehouse (SQLite) ────────────────────────────────────────────────────────
WAREHOUSE_DB = ROOT_DIR / "warehouse.db"

# ── API ───────────────────────────────────────────────────────────────────────
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

# ── Data source: "sample" reads raw/ JSON; "api" calls YouTube Data API v3 ───
# Switch this to "api" once you have a valid YOUTUBE_API_KEY in .env
SOURCE = "sample"

# ── Sample-data generation settings ──────────────────────────────────────────
VIDEOS_PER_CHANNEL  = 40    # ~40 videos per channel
SNAPSHOT_DAYS       = 7     # Generate 7 daily snapshots for growth analysis
DUPLICATE_COUNT     = 3     # Number of deliberate duplicate video IDs to inject
ZERO_VIEW_COUNT     = 2     # Videos with 0 views
DELETED_VIDEO_COUNT = 2     # Tombstone "deleted" video entries
HIDDEN_COUNTS_PROB  = 0.12  # Probability that a video hides likes/comments

# ── API retry / rate-limit settings ──────────────────────────────────────────
API_MAX_RETRIES     = 5     # Maximum retry attempts per request
API_BACKOFF_BASE    = 2     # Exponential-backoff base (seconds)
API_PAGE_SIZE       = 50    # Items per page for playlistItems (max 50)
API_VIDEO_BATCH     = 50    # IDs per videos.list call (max 50)

# ── Date format used in filenames ─────────────────────────────────────────────
DATE_FMT = "%Y-%m-%d"

# ── Staging table names ───────────────────────────────────────────────────────
STG_CHANNELS        = "stg_channels"
STG_PLAYLIST_ITEMS  = "stg_playlist_items"
STG_VIDEOS          = "stg_videos"
STG_VIDEO_SNAPSHOTS = "stg_video_snapshots"
