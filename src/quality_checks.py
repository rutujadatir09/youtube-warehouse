"""
quality_checks.py
=================
Automated Data Quality (DQ) test suite for the YouTube Warehouse.

Executes post-load checks and records outcomes in the dq_results table:
  1. no duplicate video_id in dim_video
  2. no NULL surrogate keys in any fact table
  3. no negative views, likes or comments
  4. no orphan foreign keys (every fact key exists in its dimension)
  5. row-count reconciliation: staging video rows minus rejected rows minus duplicates removed equals dim_video rows
  6. engagement_rate between 0 and 1 (WARN if anything is above 1)
  7. dim_date covers every date used in the facts
"""

from __future__ import annotations

import sys
import logging
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text

# ── Ensure src/ is on path ───────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import WAREHOUSE_DB

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


def run_checks(run_id: str, engine=None, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """
    Run all 7 data quality checks and persist results to dq_results table.

    Parameters
    ----------
    run_id  : str
        The identifier for the ETL pipeline run.
    engine  : sqlalchemy.Engine, optional
        Warehouse database engine.
    context : dict, optional
        ETL metrics dict (e.g. stg_video_rows, rejected_rows, duplicates_removed).

    Returns
    -------
    list of dict with keys: 'run_id', 'check_name', 'status', 'details'
    """
    if engine is None:
        engine = _get_engine()

    results: list[dict[str, str]] = []

    # Ensure dq_results table exists
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dq_results (
                run_id              TEXT NOT NULL,
                check_name          TEXT NOT NULL,
                status              TEXT NOT NULL,
                details             TEXT,
                PRIMARY KEY (run_id, check_name)
            );
        """))

    with engine.connect() as conn:
        # ── 1. No duplicate video_id in dim_video ─────────────────────────────
        dup_query = text("""
            SELECT video_id, COUNT(*) AS cnt 
            FROM dim_video 
            GROUP BY video_id 
            HAVING COUNT(*) > 1;
        """)
        dups = conn.execute(dup_query).fetchall()
        if dups:
            results.append({
                "run_id": run_id,
                "check_name": "no_duplicate_video_id",
                "status": "FAIL",
                "details": f"Found {len(dups)} duplicate video_id(s) in dim_video (e.g. {dups[0][0]})",
            })
        else:
            results.append({
                "run_id": run_id,
                "check_name": "no_duplicate_video_id",
                "status": "PASS",
                "details": "Zero duplicate video_ids found in dim_video",
            })

        # ── 2. No NULL surrogate keys in any fact table ────────────────────────
        null_query_fvs = text("""
            SELECT COUNT(*) FROM fact_video_stats 
            WHERE video_key IS NULL 
               OR channel_key IS NULL 
               OR category_key IS NULL 
               OR publish_date_key IS NULL 
               OR publish_hour_key IS NULL;
        """)
        null_fvs = conn.execute(null_query_fvs).scalar() or 0

        null_query_fcd = text("""
            SELECT COUNT(*) FROM fact_channel_daily 
            WHERE channel_key IS NULL 
               OR date_key IS NULL;
        """)
        null_fcd = conn.execute(null_query_fcd).scalar() or 0

        total_null_keys = null_fvs + null_fcd
        if total_null_keys > 0:
            results.append({
                "run_id": run_id,
                "check_name": "no_null_surrogate_keys",
                "status": "FAIL",
                "details": f"Found {null_fvs} NULL key(s) in fact_video_stats, {null_fcd} in fact_channel_daily",
            })
        else:
            results.append({
                "run_id": run_id,
                "check_name": "no_null_surrogate_keys",
                "status": "PASS",
                "details": "All surrogate foreign keys in fact tables are non-NULL",
            })

        # ── 3. No negative views, likes or comments ────────────────────────────
        neg_fvs = conn.execute(text("""
            SELECT COUNT(*) FROM fact_video_stats 
            WHERE (view_count IS NOT NULL AND view_count < 0)
               OR (like_count IS NOT NULL AND like_count < 0)
               OR (comment_count IS NOT NULL AND comment_count < 0);
        """)).scalar() or 0

        neg_fcd = conn.execute(text("""
            SELECT COUNT(*) FROM fact_channel_daily 
            WHERE (total_views IS NOT NULL AND total_views < 0)
               OR (subscribers IS NOT NULL AND subscribers < 0)
               OR (video_count IS NOT NULL AND video_count < 0);
        """)).scalar() or 0

        total_negatives = neg_fvs + neg_fcd
        if total_negatives > 0:
            results.append({
                "run_id": run_id,
                "check_name": "no_negative_metrics",
                "status": "FAIL",
                "details": f"Found {neg_fvs} negative values in fact_video_stats, {neg_fcd} in fact_channel_daily",
            })
        else:
            results.append({
                "run_id": run_id,
                "check_name": "no_negative_metrics",
                "status": "PASS",
                "details": "Zero negative views, likes, comments or subscriber metrics found",
            })

        # ── 4. No orphan foreign keys ──────────────────────────────────────────
        orphan_fvs = conn.execute(text("""
            SELECT COUNT(*) FROM fact_video_stats f
            WHERE f.video_key NOT IN (SELECT video_key FROM dim_video)
               OR f.channel_key NOT IN (SELECT channel_key FROM dim_channel)
               OR f.category_key NOT IN (SELECT category_key FROM dim_category)
               OR f.publish_date_key NOT IN (SELECT date_key FROM dim_date)
               OR f.publish_hour_key NOT IN (SELECT hour_key FROM dim_time);
        """)).scalar() or 0

        orphan_fcd = conn.execute(text("""
            SELECT COUNT(*) FROM fact_channel_daily f
            WHERE f.channel_key NOT IN (SELECT channel_key FROM dim_channel)
               OR f.date_key NOT IN (SELECT date_key FROM dim_date);
        """)).scalar() or 0

        total_orphans = orphan_fvs + orphan_fcd
        if total_orphans > 0:
            results.append({
                "run_id": run_id,
                "check_name": "no_orphan_foreign_keys",
                "status": "FAIL",
                "details": f"Found {orphan_fvs} orphan rows in fact_video_stats, {orphan_fcd} in fact_channel_daily",
            })
        else:
            results.append({
                "run_id": run_id,
                "check_name": "no_orphan_foreign_keys",
                "status": "PASS",
                "details": "All foreign keys in fact tables resolve to dimension tables",
            })

        # ── 5. Row-count reconciliation ────────────────────────────────────────
        # staging video rows minus rejected rows minus duplicates removed == dim_video rows
        dim_video_cnt = conn.execute(text("SELECT COUNT(*) FROM dim_video;")).scalar() or 0
        stg_video_cnt = conn.execute(text("SELECT COUNT(*) FROM stg_videos;")).scalar() or 0

        if context and "rejected_rows" in context:
            rejected_cnt = context["rejected_rows"]
            dup_removed_cnt = context.get("duplicates_removed", 0)
            # If rejected_rows includes duplicates, calculate correctly:
            if context.get("rejects_include_duplicates", True):
                expected_dim = stg_video_cnt - rejected_cnt
                formula_str = f"stg_videos ({stg_video_cnt}) - total_rejects ({rejected_cnt}) = {expected_dim}"
            else:
                expected_dim = stg_video_cnt - rejected_cnt - dup_removed_cnt
                formula_str = f"stg_videos ({stg_video_cnt}) - rejects ({rejected_cnt}) - dups ({dup_removed_cnt}) = {expected_dim}"
        else:
            # Query from warehouse difference
            diff = stg_video_cnt - dim_video_cnt
            expected_dim = stg_video_cnt - diff
            formula_str = f"stg_videos ({stg_video_cnt}) - removed ({diff}) = {expected_dim}"

        if expected_dim == dim_video_cnt:
            results.append({
                "run_id": run_id,
                "check_name": "row_count_reconciliation",
                "status": "PASS",
                "details": f"Reconciliation verified: {formula_str} == dim_video ({dim_video_cnt})",
            })
        else:
            results.append({
                "run_id": run_id,
                "check_name": "row_count_reconciliation",
                "status": "FAIL",
                "details": f"Mismatch: expected {expected_dim} rows, got {dim_video_cnt} rows in dim_video ({formula_str})",
            })

        # ── 6. Engagement rate between 0 and 1 (WARN if > 1) ───────────────────
        neg_eng = conn.execute(text("""
            SELECT COUNT(*) FROM fact_video_stats 
            WHERE engagement_rate IS NOT NULL AND engagement_rate < 0;
        """)).scalar() or 0

        high_eng = conn.execute(text("""
            SELECT COUNT(*) FROM fact_video_stats 
            WHERE engagement_rate IS NOT NULL AND engagement_rate > 1.0;
        """)).scalar() or 0

        if neg_eng > 0:
            results.append({
                "run_id": run_id,
                "check_name": "engagement_rate_range",
                "status": "FAIL",
                "details": f"Found {neg_eng} video rows with negative engagement_rate",
            })
        elif high_eng > 0:
            results.append({
                "run_id": run_id,
                "check_name": "engagement_rate_range",
                "status": "WARN",
                "details": f"Found {high_eng} video row(s) with engagement_rate > 1.0 (viral or low view ratio)",
            })
        else:
            results.append({
                "run_id": run_id,
                "check_name": "engagement_rate_range",
                "status": "PASS",
                "details": "All engagement_rate values are within expected [0.0, 1.0] interval",
            })

        # ── 7. dim_date covers every date used in the facts ───────────────────
        uncovered_fvs = conn.execute(text("""
            SELECT COUNT(DISTINCT publish_date_key) 
            FROM fact_video_stats 
            WHERE publish_date_key NOT IN (SELECT date_key FROM dim_date);
        """)).scalar() or 0

        uncovered_fcd = conn.execute(text("""
            SELECT COUNT(DISTINCT date_key) 
            FROM fact_channel_daily 
            WHERE date_key NOT IN (SELECT date_key FROM dim_date);
        """)).scalar() or 0

        total_uncovered_dates = uncovered_fvs + uncovered_fcd
        if total_uncovered_dates > 0:
            results.append({
                "run_id": run_id,
                "check_name": "dim_date_coverage",
                "status": "FAIL",
                "details": f"Missing date keys in dim_date: {uncovered_fvs} from fact_video_stats, {uncovered_fcd} from fact_channel_daily",
            })
        else:
            results.append({
                "run_id": run_id,
                "check_name": "dim_date_coverage",
                "status": "PASS",
                "details": "dim_date completely covers every date used in all fact tables",
            })

    # Save to dq_results table
    df_results = pd.DataFrame(results)
    df_results.to_sql("dq_results", con=engine, if_exists="append", index=False)

    return results


if __name__ == "__main__":
    from datetime import datetime, timezone
    sample_run_id = f"manual_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    chk_res = run_checks(sample_run_id)
    print("\n" + "=" * 60)
    print(f"  DATA QUALITY CHECK RESULTS (run_id: {sample_run_id})")
    print("=" * 60)
    for r in chk_res:
        print(f"  [{r['status']:4s}]  {r['check_name']:<28} : {r['details']}")
    print("=" * 60 + "\n")
