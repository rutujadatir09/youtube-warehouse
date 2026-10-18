"""
load.py
=======
Loads transformed DataFrames into SQLite warehouse tables (star schema),
runs data quality checks, and records ETL execution metrics in etl_log.

Loading order:
  1. Dimensions: dim_date, dim_time, dim_channel, dim_category, dim_video
  2. Fact tables: fact_video_stats, fact_channel_daily
  3. Quality checks & reconciliation
  4. ETL log record
"""

from __future__ import annotations

import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

# ── Ensure src/ is importable ─────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import WAREHOUSE_DB
import transform

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _get_engine():
    return create_engine(f"sqlite:///{WAREHOUSE_DB.as_posix()}", echo=False)


def run_quality_checks(engine, tables: dict[str, pd.DataFrame]) -> list[str]:
    """
    Run data quality checks on loaded warehouse tables:
      1. No duplicate video IDs in dim_video
      2. No negative counts in fact_video_stats
      3. No orphan foreign keys in fact_video_stats
    """
    issues = []
    with engine.connect() as conn:
        # Check 1: Duplicate video IDs
        dup_query = text("SELECT video_id, COUNT(*) FROM dim_video GROUP BY video_id HAVING COUNT(*) > 1;")
        dups = conn.execute(dup_query).fetchall()
        if dups:
            issues.append(f"FAIL: Found {len(dups)} duplicate video_id(s) in dim_video")
        else:
            log.info("CHECK PASSED: No duplicate video IDs in dim_video")

        # Check 2: Negative view/like/comment counts
        neg_query = text("SELECT COUNT(*) FROM fact_video_stats WHERE view_count < 0 OR like_count < 0 OR comment_count < 0;")
        neg_count = conn.execute(neg_query).scalar()
        if neg_count and neg_count > 0:
            issues.append(f"FAIL: Found {neg_count} rows with negative metrics in fact_video_stats")
        else:
            log.info("CHECK PASSED: No negative counts in fact_video_stats")

        # Check 3: Orphan foreign keys
        orphan_query = text("""
            SELECT COUNT(*) FROM fact_video_stats f
            WHERE f.channel_key NOT IN (SELECT channel_key FROM dim_channel)
               OR f.category_key NOT IN (SELECT category_key FROM dim_category)
               OR f.video_key NOT IN (SELECT video_key FROM dim_video);
        """)
        orphans = conn.execute(orphan_query).scalar()
        if orphans and orphans > 0:
            issues.append(f"FAIL: Found {orphans} orphan foreign key rows in fact_video_stats")
        else:
            log.info("CHECK PASSED: Zero orphan foreign keys in fact_video_stats")

        # Check 4: Composite primary key uniqueness in fact_channel_daily
        cd_dup_query = text("SELECT channel_key, date_key, COUNT(*) FROM fact_channel_daily GROUP BY channel_key, date_key HAVING COUNT(*) > 1;")
        cd_dups = conn.execute(cd_dup_query).fetchall()
        if cd_dups:
            issues.append(f"FAIL: Found {len(cd_dups)} duplicate (channel_key, date_key) in fact_channel_daily")
        else:
            log.info("CHECK PASSED: Composite PK (channel_key, date_key) is unique in fact_channel_daily")

    return issues


def log_etl_run(engine, start_time: datetime, rows_in: int, rows_loaded: int, rows_rejected: int, status: str = "SUCCESS", error_message: str = None):
    """Record execution metrics into etl_log."""
    end_time = datetime.now(tz=timezone.utc)
    run_id = f"load_{start_time.strftime('%Y%m%d_%H%M%S')}"
    
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS etl_log (
                run_id              TEXT PRIMARY KEY,
                start_time          TEXT NOT NULL,
                end_time            TEXT,
                status              TEXT NOT NULL,
                source_name         TEXT,
                rows_extracted      INTEGER,
                rows_staged         INTEGER,
                rows_transformed    INTEGER,
                rows_loaded         INTEGER,
                rows_rejected       INTEGER,
                api_units_used      INTEGER,
                error_message       TEXT
            );
        """))
        conn.execute(text("""
            INSERT INTO etl_log (
                run_id, start_time, end_time, status, source_name,
                rows_extracted, rows_staged, rows_transformed, rows_loaded, rows_rejected,
                api_units_used, error_message
            ) VALUES (
                :run_id, :start_time, :end_time, :status, 'load_module',
                :rows_in, :rows_in, :rows_in, :rows_loaded, :rows_rejected,
                0, :error_message
            )
        """), {
            "run_id": run_id,
            "start_time": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time": end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": status,
            "rows_in": rows_in,
            "rows_loaded": rows_loaded,
            "rows_rejected": rows_rejected,
            "error_message": error_message,
        })
    log.info("ETL run metrics recorded in etl_log table.")


def load_data(t_data: dict[str, pd.DataFrame], engine=None) -> dict[str, int]:
    """
    Load all transformed dimension and fact tables into warehouse.db idempotently.

    Returns dict of {table_name: row_count}.
    """
    if engine is None:
        engine = _get_engine()

    dim_tables = [
        ("dim_date", t_data["dim_date"]),
        ("dim_time", t_data["dim_time"]),
        ("dim_channel", t_data["dim_channel"]),
        ("dim_category", t_data["dim_category"]),
        ("dim_video", t_data["dim_video"]),
    ]

    fact_tables = [
        ("fact_video_stats", t_data["fact_video_stats"]),
        ("fact_channel_daily", t_data["fact_channel_daily"]),
    ]

    counts = {}
    for name, df in dim_tables:
        df.to_sql(name, con=engine, if_exists="replace", index=False)
        counts[name] = len(df)
        log.info("Loaded dimension %-20s : %d rows", name, len(df))

    for name, df in fact_tables:
        df.to_sql(name, con=engine, if_exists="replace", index=False)
        counts[name] = len(df)
        log.info("Loaded fact table %-18s : %d rows", name, len(df))

    return counts


def run():
    start_time = datetime.now(tz=timezone.utc)
    log.info("Starting warehouse load process...")
    engine = _get_engine()

    # 1. Run transform pipeline
    t_data = transform.run()

    # 2. Load tables
    counts = load_data(t_data, engine)

    # 3. Run Quality Checks
    dim_and_fact = {k: t_data[k] for k in ["dim_date", "dim_time", "dim_channel", "dim_category", "dim_video", "fact_video_stats", "fact_channel_daily"]}
    issues = run_quality_checks(engine, dim_and_fact)
    status = "SUCCESS" if not issues else f"WARNING: {len(issues)} issues"
    for issue in issues:
        log.warning(issue)

    # 4. Record ETL Run Log
    rows_in = len(t_data["clean_videos"]) + len(t_data["rejects"])
    rows_loaded = len(t_data["fact_video_stats"]) + len(t_data["fact_channel_daily"])
    rows_rejected = len(t_data["rejects"])
    log_etl_run(engine, start_time, rows_in, rows_loaded, rows_rejected, status=status)

    print("\n" + "=" * 56)
    print("  WAREHOUSE LOAD COMPLETE")
    print("=" * 56)
    print(f"  Dimensions loaded : 5")
    print(f"  Facts loaded      : 2")
    print(f"  Total Video Facts : {len(t_data['fact_video_stats']):,}")
    print(f"  Total Channel Dailies: {len(t_data['fact_channel_daily']):,}")
    print(f"  Quality Status    : {status}")
    print("=" * 56 + "\n")


if __name__ == "__main__":
    run()
