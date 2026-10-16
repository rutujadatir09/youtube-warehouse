"""
stage.py
========
Flattens raw YouTube API JSON and loads it into SQLite staging tables
inside warehouse.db.

Staging tables
--------------
  stg_channels         – one row per channel per snapshot
  stg_playlist_items   – one row per playlist item per snapshot
  stg_videos           – one row per video per snapshot (full detail)
  stg_video_snapshots  – one row per video per snapshot (statistics only,
                         designed for growth analysis across the 7-day window)

Design decisions
----------------
  * pd.json_normalize() is used to flatten nested JSON – the raw field names
    (dot-separated) are kept exactly as-is so nothing is lost at this layer.
  * All counts stay as strings (as returned by the API) – no casting yet.
  * Two metadata columns are added to every table:
      load_timestamp  – UTC datetime this row was loaded into staging
      source_file     – basename of the raw JSON file it came from
  * Tables are replaced entirely on each run (IF EXISTS DROP then recreate)
    so re-running is idempotent.

Usage
-----
  python src/stage.py              # runs extract first, then stages
  python src/stage.py --skip-extract  # stages already-extracted today's files
"""

from __future__ import annotations

import sys
import json
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

# ── ensure src/ is on the path ────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    RAW_DIR,
    WAREHOUSE_DB,
    DATE_FMT,
    STG_CHANNELS,
    STG_PLAYLIST_ITEMS,
    STG_VIDEOS,
    STG_VIDEO_SNAPSHOTS,
    SOURCE,
)

# ── Force UTF-8 output on Windows ────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _now_utc() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _add_metadata(df: pd.DataFrame, source_file: str, load_ts: str) -> pd.DataFrame:
    """Append load_timestamp and source_file columns to every staging frame."""
    df = df.copy()
    df["load_timestamp"] = load_ts
    df["source_file"]    = source_file
    return df


def _safe_normalize(items: list[dict]) -> pd.DataFrame:
    """
    Flatten a list of YouTube resource dicts with pd.json_normalize().

    Returns an empty DataFrame if items is empty, with no columns crash.
    """
    if not items:
        return pd.DataFrame()
    return pd.json_normalize(items, sep=".")


def _load_raw(resource_type: str, date_str: str) -> tuple[dict, str]:
    """
    Load a raw JSON file from raw/ for *resource_type* on *date_str*.

    Falls back to the most-recent file if today's file doesn't exist.
    Returns (data_dict, filename).
    """
    preferred = RAW_DIR / f"{resource_type}_{date_str}.json"
    if preferred.exists():
        path = preferred
    else:
        candidates = sorted(RAW_DIR.glob(f"{resource_type}_*.json"), reverse=True)
        if not candidates:
            raise FileNotFoundError(
                f"No raw file found for '{resource_type}' in {RAW_DIR}"
            )
        path = candidates[0]
        log.warning(
            "Today's raw file not found for '%s' – using %s instead",
            resource_type, path.name,
        )

    with path.open(encoding="utf-8") as fh:
        return json.load(fh), path.name


# ══════════════════════════════════════════════════════════════════════════════
# Flatten functions  (one per staging table)
# ══════════════════════════════════════════════════════════════════════════════

def flatten_channels(data: dict, source_file: str, load_ts: str) -> pd.DataFrame:
    """
    Flatten channels ListResponse items → stg_channels.

    Key normalized columns (prefix kept):
      id, snippet.title, snippet.country, snippet.publishedAt,
      statistics.viewCount, statistics.subscriberCount,
      statistics.videoCount, statistics.hiddenSubscriberCount,
      contentDetails.relatedPlaylists.uploads
    """
    items = data.get("items", [])
    df    = _safe_normalize(items)

    # topicDetails fields are lists – join to pipe-delimited strings
    _list_cols = [
        "topicDetails.topicIds",
        "topicDetails.topicCategories",
        "snippet.tags",
        "snippet.localized",    # sometimes a list in edge cases
    ]
    for col in _list_cols:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda v: "|".join(v) if isinstance(v, list) else (str(v) if v is not None else "")
            )

    df = _add_metadata(df, source_file, load_ts)
    log.info("  stg_channels        : %d rows  (%d columns)", len(df), len(df.columns))
    return df


def flatten_playlist_items(data: dict, source_file: str, load_ts: str) -> pd.DataFrame:
    """
    Flatten playlistItems ListResponse items → stg_playlist_items.

    Key normalized columns:
      id, snippet.channelId, snippet.title, snippet.publishedAt,
      snippet.resourceId.videoId, snippet.position,
      contentDetails.videoId, contentDetails.videoPublishedAt,
      status.privacyStatus
    """
    items = data.get("items", [])
    df    = _safe_normalize(items)
    df    = _add_metadata(df, source_file, load_ts)
    log.info("  stg_playlist_items  : %d rows  (%d columns)", len(df), len(df.columns))
    return df


def flatten_videos(data: dict, source_file: str, load_ts: str) -> pd.DataFrame:
    """
    Flatten videos ListResponse items → stg_videos.

    Key normalized columns:
      id, snippet.title, snippet.channelId, snippet.publishedAt,
      snippet.categoryId, snippet.tags,
      statistics.viewCount, statistics.likeCount,
      statistics.commentCount, statistics.favoriteCount,
      contentDetails.duration, contentDetails.definition
    """
    items = data.get("items", [])
    df    = _safe_normalize(items)

    # Convert list-type 'snippet.tags' to a pipe-delimited string so SQLite
    # can store it in a single TEXT column without errors.
    if "snippet.tags" in df.columns:
        df["snippet.tags"] = df["snippet.tags"].apply(
            lambda v: "|".join(v) if isinstance(v, list) else (v or "")
        )

    # topicDetails.topicCategories is also a list – join to string
    if "topicDetails.topicCategories" in df.columns:
        df["topicDetails.topicCategories"] = df["topicDetails.topicCategories"].apply(
            lambda v: "|".join(v) if isinstance(v, list) else (v or "")
        )

    # topicDetails.topicIds may also be a list
    if "topicDetails.topicIds" in df.columns:
        df["topicDetails.topicIds"] = df["topicDetails.topicIds"].apply(
            lambda v: "|".join(v) if isinstance(v, list) else (v or "")
        )

    df = _add_metadata(df, source_file, load_ts)
    log.info("  stg_videos          : %d rows  (%d columns)", len(df), len(df.columns))
    return df


def flatten_video_snapshots(data: dict, source_file: str, load_ts: str, snapshot_date: str) -> pd.DataFrame:
    """
    Build stg_video_snapshots – a narrow table of per-day statistics.

    One row per (video_id, snapshot_date) for growth analysis.
    Counts stay as strings.

    Columns:
      video_id, channel_id, snapshot_date,
      view_count, like_count, comment_count, favorite_count,
      load_timestamp, source_file
    """
    items = data.get("items", [])
    if not items:
        return pd.DataFrame()

    rows = []
    for item in items:
        stats = item.get("statistics", {})   # may be {} for deleted videos
        rows.append(
            {
                "video_id":       item.get("id", ""),
                "channel_id":     item.get("snippet", {}).get("channelId", ""),
                "snapshot_date":  snapshot_date,
                # Keep as strings – no casting at staging layer
                "view_count":     stats.get("viewCount"),      # None if deleted/hidden
                "like_count":     stats.get("likeCount"),      # None if hidden
                "comment_count":  stats.get("commentCount"),   # None if hidden
                "favorite_count": stats.get("favoriteCount"),  # always "0" from API
            }
        )

    df = pd.DataFrame(rows)
    df = _add_metadata(df, source_file, load_ts)
    log.info("  stg_video_snapshots : %d rows  (%d columns)", len(df), len(df.columns))
    return df


# ══════════════════════════════════════════════════════════════════════════════
# Database helpers
# ══════════════════════════════════════════════════════════════════════════════

def _get_engine():
    """Create (or reuse) a SQLAlchemy engine pointing at warehouse.db."""
    db_url = f"sqlite:///{WAREHOUSE_DB}"
    log.info("Warehouse DB: %s", WAREHOUSE_DB)
    return create_engine(db_url, echo=False)


def _write_table(df: pd.DataFrame, table_name: str, engine) -> int:
    """
    Drop-and-recreate a staging table, then load *df* into it.

    Returns the number of rows written.
    """
    if df.empty:
        log.warning("  %s – DataFrame is empty, skipping write.", table_name)
        return 0

    # Sanitise column names: replace '.' with '__' for SQLite compatibility
    df = df.copy()
    df.columns = [c.replace(".", "__") for c in df.columns]

    # Catch-all: coerce any column that still holds Python lists or dicts
    # to a JSON string so SQLite never sees an unsupported type.
    for col in df.columns:
        if df[col].apply(lambda v: isinstance(v, (list, dict))).any():
            import json as _json
            df[col] = df[col].apply(
                lambda v: _json.dumps(v) if isinstance(v, (list, dict)) else v
            )

    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))

    df.to_sql(table_name, engine, if_exists="replace", index=False)
    log.info("  Loaded %-22s  %d rows", table_name, len(df))
    return len(df)


def _print_row_counts(engine) -> None:
    """Query and print row counts for all four staging tables."""
    tables = [STG_CHANNELS, STG_PLAYLIST_ITEMS, STG_VIDEOS, STG_VIDEO_SNAPSHOTS]
    print("\n" + "=" * 50)
    print("  STAGING TABLE ROW COUNTS")
    print("=" * 50)
    with engine.connect() as conn:
        for tbl in tables:
            try:
                row = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}")).fetchone()
                count = row[0] if row else 0
            except Exception:
                count = "TABLE NOT FOUND"
            print(f"  {tbl:<28}  {count:>8}")
    print("=" * 50 + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# Orchestrator
# ══════════════════════════════════════════════════════════════════════════════

def run(skip_extract: bool = False) -> dict[str, int]:
    """
    Full staging pipeline.

    1. Optionally run extract first.
    2. Load today's raw JSON files.
    3. Flatten each resource type.
    4. Write to SQLite staging tables.
    5. Print row counts.

    Returns a dict of {table_name: row_count}.
    """
    today    = datetime.now(tz=timezone.utc).strftime(DATE_FMT)
    load_ts  = _now_utc()
    engine   = _get_engine()
    counts: dict[str, int] = {}

    # ── Step 1: Extract (unless caller says skip) ─────────────────────────────
    if not skip_extract:
        log.info("Running extraction first...")
        import extract  # imported here to avoid circular issues
        extract.run()

    # ── Step 2: Load raw JSON ─────────────────────────────────────────────────
    log.info("Loading raw JSON files for date %s...", today)
    ch_data,  ch_file  = _load_raw("channels",       today)
    pi_data,  pi_file  = _load_raw("playlist_items", today)
    vid_data, vid_file = _load_raw("videos",          today)

    # Derive snapshot date from the filename (strip type prefix and extension)
    snapshot_date = vid_file.replace("videos_", "").replace(".json", "")

    # ── Step 3: Flatten ───────────────────────────────────────────────────────
    log.info("Flattening and staging...")
    df_ch  = flatten_channels(ch_data,  ch_file,  load_ts)
    df_pi  = flatten_playlist_items(pi_data, pi_file, load_ts)
    df_vid = flatten_videos(vid_data, vid_file, load_ts)
    df_snp = flatten_video_snapshots(vid_data, vid_file, load_ts, snapshot_date)

    # ── Step 4: Write to SQLite ───────────────────────────────────────────────
    counts[STG_CHANNELS]        = _write_table(df_ch,  STG_CHANNELS,        engine)
    counts[STG_PLAYLIST_ITEMS]  = _write_table(df_pi,  STG_PLAYLIST_ITEMS,  engine)
    counts[STG_VIDEOS]          = _write_table(df_vid, STG_VIDEOS,          engine)
    counts[STG_VIDEO_SNAPSHOTS] = _write_table(df_snp, STG_VIDEO_SNAPSHOTS, engine)

    # ── Step 5: Print row counts ──────────────────────────────────────────────
    _print_row_counts(engine)

    log.info("Staging complete.")
    return counts


# ══════════════════════════════════════════════════════════════════════════════
# CLI entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage YouTube raw data into SQLite.")
    parser.add_argument(
        "--skip-extract",
        action="store_true",
        help="Skip the extraction step and use existing raw/ files.",
    )
    args = parser.parse_args()
    run(skip_extract=args.skip_extract)
