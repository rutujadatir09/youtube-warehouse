"""
tests/test_stage.py
===================
Pytest tests for the staging layer.

Tests verify:
  1. All four staging tables exist in warehouse.db after stage.run().
  2. Row counts match the number of items in the latest raw JSON files.
  3. The stg_video_snapshots row count equals the number of video items.
  4. load_timestamp and source_file columns are present in every table.
  5. stg_videos retains counts as strings (no premature casting).

The tests import stage.run() and trigger a real staging pipeline
(using existing raw/ files – no API calls).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import pandas as pd
from sqlalchemy import create_engine, inspect, text

# ── add src/ to path so imports resolve ───────────────────────────────────────
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from config import (
    RAW_DIR,
    WAREHOUSE_DB,
    STG_CHANNELS,
    STG_PLAYLIST_ITEMS,
    STG_VIDEOS,
    STG_VIDEO_SNAPSHOTS,
)


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="session")
def engine():
    """SQLAlchemy engine for the warehouse DB (shared across the session)."""
    return create_engine(f"sqlite:///{WAREHOUSE_DB}", echo=False)


@pytest.fixture(scope="session")
def staged_counts(engine):
    """
    Run the staging pipeline once for the whole test session and return
    the dict of {table_name: row_count} reported by stage.run().
    """
    import stage
    counts = stage.run(skip_extract=True)
    return counts


@pytest.fixture(scope="session")
def latest_raw_items() -> dict[str, int]:
    """
    Load the most-recent raw JSON files and return the item counts
    for channels, playlist_items, and videos.
    """
    result = {}
    for rtype in ("channels", "playlist_items", "videos"):
        candidates = sorted(RAW_DIR.glob(f"{rtype}_*.json"), reverse=True)
        assert candidates, f"No raw file found for '{rtype}' in {RAW_DIR}"
        with candidates[0].open(encoding="utf-8") as fh:
            data = json.load(fh)
        result[rtype] = len(data.get("items", []))
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Tests: tables exist
# ══════════════════════════════════════════════════════════════════════════════

ALL_TABLES = [STG_CHANNELS, STG_PLAYLIST_ITEMS, STG_VIDEOS, STG_VIDEO_SNAPSHOTS]


@pytest.mark.parametrize("table_name", ALL_TABLES)
def test_table_exists(table_name, engine, staged_counts):
    """Each staging table must exist in warehouse.db after staging."""
    inspector = inspect(engine)
    existing  = inspector.get_table_names()
    assert table_name in existing, (
        f"Table '{table_name}' not found in {WAREHOUSE_DB}. "
        f"Existing tables: {existing}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Tests: row counts match raw files
# ══════════════════════════════════════════════════════════════════════════════

def test_stg_channels_row_count(engine, staged_counts, latest_raw_items):
    """stg_channels row count must match channels raw item count."""
    expected = latest_raw_items["channels"]
    with engine.connect() as conn:
        actual = conn.execute(text(f"SELECT COUNT(*) FROM {STG_CHANNELS}")).scalar()
    assert actual == expected, (
        f"stg_channels: expected {expected} rows, got {actual}"
    )


def test_stg_playlist_items_row_count(engine, staged_counts, latest_raw_items):
    """stg_playlist_items row count must match playlist_items raw item count."""
    expected = latest_raw_items["playlist_items"]
    with engine.connect() as conn:
        actual = conn.execute(text(f"SELECT COUNT(*) FROM {STG_PLAYLIST_ITEMS}")).scalar()
    assert actual == expected, (
        f"stg_playlist_items: expected {expected} rows, got {actual}"
    )


def test_stg_videos_row_count(engine, staged_counts, latest_raw_items):
    """stg_videos row count must match videos raw item count."""
    expected = latest_raw_items["videos"]
    with engine.connect() as conn:
        actual = conn.execute(text(f"SELECT COUNT(*) FROM {STG_VIDEOS}")).scalar()
    assert actual == expected, (
        f"stg_videos: expected {expected} rows, got {actual}"
    )


def test_stg_video_snapshots_row_count(engine, staged_counts, latest_raw_items):
    """
    stg_video_snapshots row count must equal the number of video items
    (one snapshot row per video per run).
    """
    expected = latest_raw_items["videos"]
    with engine.connect() as conn:
        actual = conn.execute(text(f"SELECT COUNT(*) FROM {STG_VIDEO_SNAPSHOTS}")).scalar()
    assert actual == expected, (
        f"stg_video_snapshots: expected {expected} rows, got {actual}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Tests: metadata columns
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("table_name", ALL_TABLES)
def test_metadata_columns_exist(table_name, engine, staged_counts):
    """Every staging table must have load_timestamp and source_file columns."""
    inspector = inspect(engine)
    columns   = {col["name"] for col in inspector.get_columns(table_name)}
    assert "load_timestamp" in columns, (
        f"'{table_name}' is missing 'load_timestamp'"
    )
    assert "source_file" in columns, (
        f"'{table_name}' is missing 'source_file'"
    )


@pytest.mark.parametrize("table_name", ALL_TABLES)
def test_metadata_columns_not_null(table_name, engine, staged_counts):
    """load_timestamp and source_file must not be NULL in any row."""
    with engine.connect() as conn:
        null_ts = conn.execute(
            text(f"SELECT COUNT(*) FROM {table_name} WHERE load_timestamp IS NULL")
        ).scalar()
        null_sf = conn.execute(
            text(f"SELECT COUNT(*) FROM {table_name} WHERE source_file IS NULL")
        ).scalar()
    assert null_ts == 0, f"'{table_name}' has {null_ts} NULL load_timestamp values"
    assert null_sf == 0, f"'{table_name}' has {null_sf} NULL source_file values"


# ══════════════════════════════════════════════════════════════════════════════
# Tests: data quality / no premature casting
# ══════════════════════════════════════════════════════════════════════════════

def test_view_count_remains_string_in_stg_videos(engine, staged_counts):
    """
    statistics__viewCount must remain a string (TEXT) type in stg_videos –
    no casting should happen at the staging layer.
    Non-NULL viewCount values should be digit-only strings.
    """
    with engine.connect() as conn:
        # Fetch rows where view count is not NULL
        rows = conn.execute(
            text(
                f"SELECT statistics__viewCount FROM {STG_VIDEOS} "
                f"WHERE statistics__viewCount IS NOT NULL LIMIT 20"
            )
        ).fetchall()

    assert rows, "No non-NULL viewCount rows found in stg_videos"
    for (val,) in rows:
        assert isinstance(val, str), (
            f"viewCount should be TEXT, got {type(val).__name__}: {val!r}"
        )
        assert val.isdigit(), (
            f"viewCount should be digit-only string, got: {val!r}"
        )


def test_stg_video_snapshots_has_snapshot_date(engine, staged_counts):
    """stg_video_snapshots must have a non-NULL snapshot_date in every row."""
    with engine.connect() as conn:
        null_count = conn.execute(
            text(
                f"SELECT COUNT(*) FROM {STG_VIDEO_SNAPSHOTS} "
                f"WHERE snapshot_date IS NULL"
            )
        ).scalar()
    assert null_count == 0, (
        f"stg_video_snapshots has {null_count} rows with NULL snapshot_date"
    )


def test_no_empty_video_ids_in_snapshots(engine, staged_counts):
    """video_id must not be empty/NULL in stg_video_snapshots."""
    with engine.connect() as conn:
        bad = conn.execute(
            text(
                f"SELECT COUNT(*) FROM {STG_VIDEO_SNAPSHOTS} "
                f"WHERE video_id IS NULL OR video_id = ''"
            )
        ).scalar()
    assert bad == 0, (
        f"stg_video_snapshots has {bad} rows with empty/NULL video_id"
    )
