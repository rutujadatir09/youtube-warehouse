"""
run_pipeline.py
===============
Master pipeline orchestrator for the YouTube Content & Engagement Warehouse.

Executes all pipeline phases in order:
  1. Extract        (live YouTube Data API v3 or sample data)
  2. Stage          (flatten raw JSON into SQLite staging tables)
  3. Transform      (data cleaning, NLP enrichment, star schema builders)
  4. Load           (idempotent write of dimensions & fact tables)
  5. Quality Checks (runs DQ validation suite, logs to dq_results)

Maintains full audit lineage in etl_log:
  - Inserts row with status='RUNNING' at start
  - Updates row with counts and status='SUCCESS' at completion
  - Logs status='FAILED' with error details on exception or failed DQ check
  - Exits with non-zero exit code if any DQ check fails

Usage
-----
  python src/run_pipeline.py
"""

from __future__ import annotations

import sys
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

# ── Ensure src/ is on the Python path ─────────────────────────────────────────
SRC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_DIR))

from config import WAREHOUSE_DB, SOURCE
import extract
import stage
import transform
import load
import quality_checks

# ── Force UTF-8 terminal encoding on Windows ──────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("run_pipeline")


def _get_engine():
    return create_engine(f"sqlite:///{WAREHOUSE_DB.as_posix()}", echo=False)


def _ensure_log_tables(engine) -> None:
    """Ensure etl_log and dq_results tables exist before running."""
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
            CREATE TABLE IF NOT EXISTS dq_results (
                run_id              TEXT NOT NULL,
                check_name          TEXT NOT NULL,
                status              TEXT NOT NULL,
                details             TEXT,
                PRIMARY KEY (run_id, check_name)
            );
        """))


def _init_run_log(engine, run_id: str, start_time: str, source_name: str) -> None:
    """Insert initial RUNNING record in etl_log."""
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO etl_log (
                    run_id, start_time, status, source_name, api_units_used
                ) VALUES (
                    :run_id, :start_time, 'RUNNING', :source_name, 0
                );
            """),
            {
                "run_id": run_id,
                "start_time": start_time,
                "source_name": source_name,
            },
        )


def _update_run_log(
    engine,
    run_id: str,
    end_time: str,
    status: str,
    rows_extracted: int = 0,
    rows_staged: int = 0,
    rows_transformed: int = 0,
    rows_loaded: int = 0,
    rows_rejected: int = 0,
    api_units_used: int = 0,
    error_message: str | None = None,
) -> None:
    """Update execution results in etl_log."""
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE etl_log
                SET end_time          = :end_time,
                    status            = :status,
                    rows_extracted    = :rows_extracted,
                    rows_staged       = :rows_staged,
                    rows_transformed  = :rows_transformed,
                    rows_loaded       = :rows_loaded,
                    rows_rejected     = :rows_rejected,
                    api_units_used    = :api_units_used,
                    error_message     = :error_message
                WHERE run_id          = :run_id;
            """),
            {
                "run_id": run_id,
                "end_time": end_time,
                "status": status,
                "rows_extracted": rows_extracted,
                "rows_staged": rows_staged,
                "rows_transformed": rows_transformed,
                "rows_loaded": rows_loaded,
                "rows_rejected": rows_rejected,
                "api_units_used": api_units_used,
                "error_message": error_message,
            },
        )


def run_pipeline() -> bool:
    """
    Execute full pipeline workflow. Returns True if SUCCESS, False if FAILED.
    """
    engine = _get_engine()
    _ensure_log_tables(engine)

    now_utc = datetime.now(tz=timezone.utc)
    run_id = f"run_{now_utc.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    start_time_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    print("\n" + "=" * 65)
    print(f"  YOUTUBE WAREHOUSE PIPELINE RUNNER  |  run_id: {run_id}")
    print("=" * 65)

    _init_run_log(engine, run_id, start_time_str, SOURCE)

    rows_extracted = 0
    rows_staged = 0
    rows_transformed = 0
    rows_loaded = 0
    rows_rejected = 0
    api_units_used = 0

    try:
        # ── Stage 1: Extract ──────────────────────────────────────────────────
        log.info("▶ Stage 1/5: Extracting data (source=%s)...", SOURCE)
        ext_data = extract.run()
        for payload in ext_data.values():
            rows_extracted += len(payload.get("items", []))
        if hasattr(extract, "_quota"):
            api_units_used = getattr(extract._quota, "units_used", 0)

        # ── Stage 2: Stage ────────────────────────────────────────────────────
        log.info("▶ Stage 2/5: Staging raw data into SQLite...")
        stg_counts = stage.run(skip_extract=True)
        rows_staged = sum(stg_counts.values())

        # ── Stage 3: Transform ────────────────────────────────────────────────
        log.info("▶ Stage 3/5: Transforming staging data into Star Schema...")
        t_data = transform.run()
        clean_videos = t_data["clean_videos"]
        rejects = t_data["rejects"]
        fact_vs = t_data["fact_video_stats"]
        fact_cd = t_data["fact_channel_daily"]

        rows_transformed = len(clean_videos) + len(fact_cd)
        rows_rejected = len(rejects)
        duplicates_removed = len(
            rejects[rejects["reject_reason"].str.contains("duplicate", case=False, na=False)]
        ) if "reject_reason" in rejects.columns else 0

        # ── Stage 4: Load ─────────────────────────────────────────────────────
        log.info("▶ Stage 4/5: Loading dimensions and facts into warehouse...")
        load_counts = load.load_data(t_data, engine)
        rows_loaded = len(fact_vs) + len(fact_cd)

        # ── Stage 5: Quality Checks ───────────────────────────────────────────
        log.info("▶ Stage 5/5: Executing data quality validation suite...")
        context = {
            "stg_video_rows": stg_counts.get("stg_videos", len(clean_videos) + rows_rejected),
            "rejected_rows": rows_rejected,
            "duplicates_removed": duplicates_removed,
            "rejects_include_duplicates": True,
        }
        dq_res = quality_checks.run_checks(run_id=run_id, engine=engine, context=context)

        # Print stage rows summary
        print("\n" + "-" * 65)
        print("  PIPELINE STAGE ROW COUNTS")
        print("-" * 65)
        print(f"  1. Extracted rows        : {rows_extracted:>8,}")
        print(f"  2. Staged rows           : {rows_staged:>8,}")
        for tbl, cnt in stg_counts.items():
            print(f"       -> {tbl:<22} : {cnt:>6,}")
        print(f"  3. Transformed rows      : {rows_transformed:>8,}")
        print(f"     Rejected rows         : {rows_rejected:>8,}")
        print(f"  4. Loaded fact rows      : {rows_loaded:>8,}")
        print(f"       -> fact_video_stats : {len(fact_vs):>6,}")
        print(f"       -> fact_channel_daily: {len(fact_cd):>5,}")

        # Print check results summary
        print("\n" + "-" * 65)
        print("  DATA QUALITY CHECK RESULTS")
        print("-" * 65)
        has_failure = False
        for r in dq_res:
            icon = "✅" if r["status"] == "PASS" else ("⚠️" if r["status"] == "WARN" else "❌")
            print(f"  {icon} [{r['status']:4s}]  {r['check_name']:<27} : {r['details']}")
            if r["status"] == "FAIL":
                has_failure = True

        end_time_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if has_failure:
            err_msg = "One or more data quality checks failed."
            _update_run_log(
                engine, run_id, end_time_str, "FAILED",
                rows_extracted, rows_staged, rows_transformed, rows_loaded, rows_rejected,
                api_units_used, err_msg
            )
            print("\n" + "=" * 65)
            print("  ❌ PIPELINE FAILED (Quality Checks Unsuccessful)")
            print("=" * 65 + "\n")
            return False

        _update_run_log(
            engine, run_id, end_time_str, "SUCCESS",
            rows_extracted, rows_staged, rows_transformed, rows_loaded, rows_rejected,
            api_units_used, None
        )
        print("\n" + "=" * 65)
        print("  ✅ PIPELINE RUN COMPLETED SUCCESSFULLY")
        print("=" * 65 + "\n")
        return True

    except Exception as exc:
        end_time_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log.exception("Pipeline execution failed with exception: %s", exc)
        _update_run_log(
            engine, run_id, end_time_str, "FAILED",
            rows_extracted, rows_staged, rows_transformed, rows_loaded, rows_rejected,
            api_units_used, str(exc)
        )
        print("\n" + "=" * 65)
        print(f"  ❌ PIPELINE FAILED (Exception: {exc})")
        print("=" * 65 + "\n")
        return False


if __name__ == "__main__":
    success = run_pipeline()
    if not success:
        sys.exit(1)
    sys.exit(0)
