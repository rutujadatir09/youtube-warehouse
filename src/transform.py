"""
transform.py
============
Reads the four SQLite staging tables, applies all cleaning and enrichment
rules, and returns clean pandas DataFrames ready for loading.

Nothing is written to the warehouse here – that is Step 4's job.

Pipeline overview
-----------------
  read_staging()          → raw staging DataFrames
  clean_videos()          → cleaned video rows + rejects DataFrame
  clean_channels()        → cleaned channel rows
  build_dim_date()        → dim_date  (full date spine)
  build_dim_time()        → dim_time  (0-23 hour lookup)
  build_dim_channel()     → dim_channel  (surrogate key)
  build_dim_category()    → dim_category (surrogate key)
  build_dim_video()       → dim_video  (surrogate key)
  build_fact_video_stats()    → fact_video_stats
  build_fact_channel_daily()  → fact_channel_daily
  run()                   → orchestrates everything, prints summary

Transformation rules applied
-----------------------------
  1.  ISO 8601 duration → duration_seconds + duration_band
  2.  publishedAt UTC   → also stored as IST (Asia/Kolkata)
  3.  Stats cast to pandas Int64 (nullable); hidden = NULL not 0
  4.  engagement_rate   = (likes + comments) / views  (NULL-safe)
  5.  Title features    : title_length, word_count,
                          has_number_in_title, has_question_mark
  6.  Dedup on video_id (keep latest load_timestamp)
      Deleted / private videos → rejects DataFrame with reason
  7.  dim_date, dim_time, dim_channel, dim_category, dim_video
  8.  fact_video_stats, fact_channel_daily
"""

from __future__ import annotations

import re
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

import isodate
import pandas as pd
import numpy as np
from sqlalchemy import create_engine

# ── resolve src/ imports regardless of CWD ────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    WAREHOUSE_DB,
    CHANNELS_CSV,
    STG_CHANNELS,
    STG_PLAYLIST_ITEMS,
    STG_VIDEOS,
    STG_VIDEO_SNAPSHOTS,
)

# ── UTF-8 safe output on Windows ─────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── IST timezone ──────────────────────────────────────────────────────────────
try:
    from zoneinfo import ZoneInfo          # Python 3.9+
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

# ── Standard YouTube category ID → name mapping ───────────────────────────────
YOUTUBE_CATEGORIES: dict[str, str] = {
    "1":  "Film & Animation",
    "2":  "Autos & Vehicles",
    "10": "Music",
    "15": "Pets & Animals",
    "17": "Sports",
    "18": "Short Movies",
    "19": "Travel & Events",
    "20": "Gaming",
    "21": "Videoblogging",
    "22": "People & Blogs",
    "23": "Comedy",
    "24": "Entertainment",
    "25": "News & Politics",
    "26": "Howto & Style",
    "27": "Education",
    "28": "Science & Technology",
    "29": "Nonprofits & Activism",
}

# Duration band thresholds (in seconds)
_SHORT_MAX  =  4 * 60        #   < 4 min
_MEDIUM_MAX = 20 * 60        # 4–20 min  (> 20 min → long)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Read staging
# ══════════════════════════════════════════════════════════════════════════════

def read_staging() -> dict[str, pd.DataFrame]:
    """
    Load all four staging tables from warehouse.db into DataFrames.

    Returns
    -------
    dict with keys: 'channels', 'playlist_items', 'videos', 'video_snapshots'
    """
    engine = create_engine(f"sqlite:///{WAREHOUSE_DB}", echo=False)
    log.info("Reading staging tables from %s ...", WAREHOUSE_DB)

    tables = {
        "channels":        STG_CHANNELS,
        "playlist_items":  STG_PLAYLIST_ITEMS,
        "videos":          STG_VIDEOS,
        "video_snapshots": STG_VIDEO_SNAPSHOTS,
    }

    frames: dict[str, pd.DataFrame] = {}
    for key, tbl in tables.items():
        df = pd.read_sql_table(tbl, engine)
        log.info("  %-20s  %d rows", tbl, len(df))
        frames[key] = df

    return frames


# ══════════════════════════════════════════════════════════════════════════════
# 2. Helper transformations
# ══════════════════════════════════════════════════════════════════════════════

def _parse_duration(iso_str) -> int | None:
    """
    Parse an ISO 8601 duration string (e.g. 'PT1H5M30S') into total seconds.

    Returns None for missing / unparseable values.
    """
    if not isinstance(iso_str, str) or not iso_str.startswith("PT"):
        return None
    try:
        td = isodate.parse_duration(iso_str)
        return int(td.total_seconds())
    except (isodate.isoerror.ISO8601Error, ValueError, AttributeError):
        return None


def _duration_band(secs) -> str | None:
    """Categorise duration_seconds into short / medium / long."""
    if pd.isna(secs):
        return None
    secs = int(secs)
    if secs < _SHORT_MAX:
        return "short"
    if secs <= _MEDIUM_MAX:
        return "medium"
    return "long"


def _to_ist(utc_str) -> pd.Timestamp | None:
    """
    Convert a UTC timestamp string ('2022-09-07T06:51:17Z') to IST.

    Returns a tz-aware pandas Timestamp in IST, or None if unparseable.
    """
    if not isinstance(utc_str, str):
        return None
    try:
        ts = pd.Timestamp(utc_str, tz="UTC")
        return ts.tz_convert(IST)
    except Exception:
        return None


def _safe_int(series: pd.Series) -> pd.Series:
    """
    Cast a string series to pandas Int64 (nullable integer).

    None / NaN / empty strings remain <NA> (not 0).
    """
    return pd.to_numeric(series, errors="coerce").astype("Int64")


def _engagement_rate(
    views: pd.Series,
    likes: pd.Series,
    comments: pd.Series,
) -> pd.Series:
    """
    engagement_rate = (likes + comments) / views

    Rules:
      - NULL if views is 0 or NULL
      - NULL if both likes AND comments are NULL (hidden / disabled)
      - Otherwise treat individually NULL likes or comments as 0
    """
    # Work in Float64 to preserve NA propagation
    v  = views.astype("Float64")
    l_ = likes.astype("Float64")
    c_ = comments.astype("Float64")

    # Where both likes and comments are NA → result is NA
    both_hidden = l_.isna() & c_.isna()

    # Fill individual NAs with 0 so arithmetic works; re-mask both_hidden later
    numerator = l_.fillna(0) + c_.fillna(0)

    rate = pd.Series(np.where(
        (v == 0) | v.isna() | both_hidden,
        pd.NA,
        numerator / v,
    ), dtype="Float64", index=views.index)

    return rate


# ══════════════════════════════════════════════════════════════════════════════
# 3. Clean videos
# ══════════════════════════════════════════════════════════════════════════════

def clean_videos(
    stg_videos: pd.DataFrame,
    stg_playlist_items: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Clean and enrich the stg_videos DataFrame.

    Returns
    -------
    (clean_df, rejects_df)

    Rejects contain: deleted/private videos, and extra duplicate rows.
    Each rejected row has a 'reject_reason' column.
    """
    df = stg_videos.copy()
    in_count = len(df)
    log.info("clean_videos: %d rows in", in_count)

    rejects_list: list[pd.DataFrame] = []

    # ── A. Identify deleted / private video IDs from playlist_items ───────────
    # playlist_items has a status__privacyStatus column; 'private' = deleted
    private_ids: set[str] = set()
    if "status__privacyStatus" in stg_playlist_items.columns:
        private_mask = stg_playlist_items["status__privacyStatus"] == "private"
        vid_id_col   = "snippet__resourceId__videoId"
        if vid_id_col in stg_playlist_items.columns:
            private_ids = set(
                stg_playlist_items.loc[private_mask, vid_id_col].dropna().unique()
            )
    log.info("  Found %d private/deleted video IDs from playlist_items", len(private_ids))

    # Also detect deleted videos in stg_videos itself:
    # deleted videos have empty/null statistics AND title "This video is unavailable"
    deleted_mask = (
        df["snippet__title"].str.strip().str.lower()
        .isin(["this video is unavailable", "deleted video", ""])
    )
    # Combine: either in private_ids set OR detected by title
    reject_mask = deleted_mask | df["id"].isin(private_ids)

    # Capture rejects before removal
    if reject_mask.any():
        rej = df[reject_mask].copy()
        rej["reject_reason"] = np.where(
            rej["id"].isin(private_ids),
            "private/deleted (playlist_items status=private)",
            "deleted video (title marker)",
        )
        rejects_list.append(rej)
        df = df[~reject_mask].reset_index(drop=True)
        log.info("  Rejected %d deleted/private rows", reject_mask.sum())

    # ── B. Deduplicate on video ID (keep latest load_timestamp) ───────────────
    # Sort descending by load_timestamp then drop_duplicates keeps the first (=latest)
    df["load_timestamp"] = pd.to_datetime(df["load_timestamp"], utc=True, errors="coerce")
    df = df.sort_values("load_timestamp", ascending=False)
    dup_mask = df.duplicated(subset=["id"], keep="first")

    if dup_mask.any():
        rej_dup = df[dup_mask].copy()
        rej_dup["reject_reason"] = "duplicate video_id (older copy removed)"
        rejects_list.append(rej_dup)
        df = df[~dup_mask].reset_index(drop=True)
        log.info("  Removed %d duplicate video rows", dup_mask.sum())

    # ── C. Parse ISO 8601 duration → duration_seconds + duration_band ─────────
    df["duration_seconds"] = df["contentDetails__duration"].apply(_parse_duration)
    df["duration_band"]    = df["duration_seconds"].apply(_duration_band)

    # ── D. publishedAt UTC → also IST ─────────────────────────────────────────
    df["published_at_utc"] = pd.to_datetime(
        df["snippet__publishedAt"], utc=True, errors="coerce"
    )
    df["published_at_ist"] = df["snippet__publishedAt"].apply(_to_ist)

    # ── E. Cast statistics to nullable Int64 ──────────────────────────────────
    # Hidden / disabled metrics remain NULL (not 0) because the API simply
    # omits the key; _safe_int converts NaN → <NA>.
    df["view_count"]    = _safe_int(df["statistics__viewCount"])
    df["like_count"]    = _safe_int(df["statistics__likeCount"])     # NULL if hidden
    df["comment_count"] = _safe_int(df["statistics__commentCount"])  # NULL if hidden

    # ── F. Engagement rate (NULL-safe) ────────────────────────────────────────
    df["engagement_rate"] = _engagement_rate(
        df["view_count"], df["like_count"], df["comment_count"]
    )

    # ── G. Title features ─────────────────────────────────────────────────────
    titles = df["snippet__title"].fillna("")
    df["title_length"]         = titles.str.len().astype("Int64")
    df["word_count"]           = titles.str.split().str.len().astype("Int64")
    df["has_number_in_title"]  = titles.str.contains(r"\d", regex=True)
    df["has_question_mark"]    = titles.str.contains("?", regex=False)

    # ── Build combined rejects DataFrame ──────────────────────────────────────
    rejects_df = (
        pd.concat(rejects_list, ignore_index=True)
        if rejects_list
        else pd.DataFrame(columns=[*df.columns, "reject_reason"])
    )

    log.info(
        "clean_videos: %d rows in → %d clean, %d rejected",
        in_count, len(df), len(rejects_df),
    )
    return df, rejects_df


# ══════════════════════════════════════════════════════════════════════════════
# 4. Clean channels
# ══════════════════════════════════════════════════════════════════════════════

def clean_channels(stg_channels: pd.DataFrame) -> pd.DataFrame:
    """
    Clean stg_channels: cast numeric columns, convert publishedAt to IST.
    """
    df = stg_channels.copy()
    log.info("clean_channels: %d rows in", len(df))

    df["published_at_utc"] = pd.to_datetime(
        df["snippet__publishedAt"], utc=True, errors="coerce"
    )
    df["published_at_ist"] = df["snippet__publishedAt"].apply(_to_ist)

    df["subscriber_count"] = _safe_int(df["statistics__subscriberCount"])
    df["view_count"]       = _safe_int(df["statistics__viewCount"])
    df["video_count"]      = _safe_int(df["statistics__videoCount"])

    log.info("clean_channels: %d rows out", len(df))
    return df


# ══════════════════════════════════════════════════════════════════════════════
# 5. Dimension builders
# ══════════════════════════════════════════════════════════════════════════════

def build_dim_date(
    clean_videos: pd.DataFrame,
    stg_video_snapshots: pd.DataFrame,
    stg_channels: pd.DataFrame = None,
) -> pd.DataFrame:
    """
    Build dim_date: a full date spine covering all publish dates + snapshot dates.

    Columns
    -------
    date_key, full_date, day, month, month_name, quarter, year,
    weekday, weekday_name, is_weekend
    """
    # Collect all relevant dates
    all_dates: list[pd.Timestamp] = []

    pub_dates = pd.to_datetime(
        clean_videos["published_at_utc"], utc=True, errors="coerce"
    ).dt.tz_localize(None).dropna()
    all_dates.extend(pub_dates.dt.normalize().unique())

    snap_dates = pd.to_datetime(
        stg_video_snapshots["snapshot_date"], errors="coerce"
    ).dropna()
    if hasattr(snap_dates.dt, "tz_localize"):
        try:
            snap_dates = snap_dates.dt.tz_localize(None)
        except TypeError:
            pass
    all_dates.extend(snap_dates.dt.normalize().unique())

    if stg_channels is not None and "snapshot_date" in stg_channels.columns:
        ch_snap_dates = pd.to_datetime(
            stg_channels["snapshot_date"], errors="coerce"
        ).dropna()
        if hasattr(ch_snap_dates.dt, "tz_localize"):
            try:
                ch_snap_dates = ch_snap_dates.dt.tz_localize(None)
            except TypeError:
                pass
        all_dates.extend(ch_snap_dates.dt.normalize().unique())

    if not all_dates:
        log.warning("dim_date: no dates found, returning empty DataFrame")
        return pd.DataFrame()

    min_date = min(all_dates)
    if hasattr(min_date, "tzinfo") and min_date.tzinfo is not None:
        min_date = min_date.replace(tzinfo=None)
    max_date = max(all_dates)
    if hasattr(max_date, "tzinfo") and max_date.tzinfo is not None:
        max_date = max_date.replace(tzinfo=None)

    spine = pd.date_range(start=min_date, end=max_date, freq="D")
    df = pd.DataFrame({"full_date": spine})

    df["date_key"]    = df["full_date"].dt.strftime("%Y%m%d").astype(int)
    df["day"]         = df["full_date"].dt.day.astype("Int64")
    df["month"]       = df["full_date"].dt.month.astype("Int64")
    df["month_name"]  = df["full_date"].dt.month_name()
    df["quarter"]     = df["full_date"].dt.quarter.astype("Int64")
    df["year"]        = df["full_date"].dt.year.astype("Int64")
    df["weekday"]     = df["full_date"].dt.weekday.astype("Int64")   # Mon=0, Sun=6
    df["weekday_name"]= df["full_date"].dt.day_name()
    df["is_weekend"]  = df["weekday"] >= 5

    df = df.sort_values("date_key").reset_index(drop=True)
    log.info("dim_date: %d rows (%s to %s)", len(df), min_date.date(), max_date.date())
    return df


def build_dim_time() -> pd.DataFrame:
    """
    Build dim_time: 24-row lookup table, one row per hour of the day.

    Columns
    -------
    hour_key (0–23), hour, time_band (Night / Morning / Afternoon / Evening)
    """
    def _band(h: int) -> str:
        if 0 <= h < 6:
            return "Night"
        if 6 <= h < 12:
            return "Morning"
        if 12 <= h < 17:
            return "Afternoon"
        return "Evening"

    df = pd.DataFrame({"hour_key": range(24)})
    df["hour"]      = df["hour_key"]
    df["time_band"] = df["hour_key"].apply(_band)
    log.info("dim_time: %d rows", len(df))
    return df


def build_dim_channel(
    clean_channels: pd.DataFrame,
    channels_csv_path=CHANNELS_CSV,
) -> pd.DataFrame:
    """
    Build dim_channel with a surrogate key channel_key.

    Merges the cleaned channel data with channels.csv to get niche.

    Columns
    -------
    channel_key, channel_id, channel_name, country, niche, created_date
    """
    # Load the niche reference from channels.csv
    ref = pd.read_csv(channels_csv_path, dtype=str)

    df = clean_channels[
        ["id", "snippet__title", "snippet__country", "published_at_utc"]
    ].copy()
    df = df.rename(columns={
        "id":                  "channel_id",
        "snippet__title":      "channel_name",
        "snippet__country":    "country",
        "published_at_utc":    "created_date",
    })

    # Bring in niche from CSV reference
    df = df.merge(
        ref[["channel_id", "niche"]],
        on="channel_id",
        how="left",
    )

    df = df.drop_duplicates(subset=["channel_id"]).reset_index(drop=True)

    # Surrogate key (1-based integer)
    df.insert(0, "channel_key", range(1, len(df) + 1))

    log.info("dim_channel: %d rows", len(df))
    return df


def build_dim_category(clean_videos: pd.DataFrame) -> pd.DataFrame:
    """
    Build dim_category from the category IDs seen in the video data,
    enriched with standard YouTube category names.

    Columns
    -------
    category_key, category_id, category_name
    """
    cat_ids = (
        clean_videos["snippet__categoryId"]
        .dropna()
        .astype(str)
        .unique()
    )

    rows = []
    for cid in sorted(cat_ids, key=lambda x: int(x) if x.isdigit() else 999):
        rows.append({
            "category_id":   cid,
            "category_name": YOUTUBE_CATEGORIES.get(cid, f"Unknown ({cid})"),
        })

    df = pd.DataFrame(rows)
    df.insert(0, "category_key", range(1, len(df) + 1))

    log.info("dim_category: %d rows", len(df))
    return df


def build_dim_video(clean_videos: pd.DataFrame) -> pd.DataFrame:
    """
    Build dim_video with a surrogate key video_key.

    Columns
    -------
    video_key, video_id, title, duration_seconds, duration_band,
    has_number_in_title, has_question_mark, title_length, word_count
    """
    df = clean_videos[[
        "id",
        "snippet__title",
        "duration_seconds",
        "duration_band",
        "has_number_in_title",
        "has_question_mark",
        "title_length",
        "word_count",
    ]].copy()

    df = df.rename(columns={
        "id":              "video_id",
        "snippet__title":  "title",
    })

    df = df.drop_duplicates(subset=["video_id"]).reset_index(drop=True)
    df.insert(0, "video_key", range(1, len(df) + 1))

    log.info("dim_video: %d rows", len(df))
    return df


# ══════════════════════════════════════════════════════════════════════════════
# 6. Fact builders
# ══════════════════════════════════════════════════════════════════════════════

def build_fact_video_stats(
    clean_videos:  pd.DataFrame,
    dim_video:     pd.DataFrame,
    dim_channel:   pd.DataFrame,
    dim_category:  pd.DataFrame,
    dim_date:      pd.DataFrame,
    dim_time:      pd.DataFrame,
) -> pd.DataFrame:
    """
    Build fact_video_stats – one row per clean video (latest snapshot).

    Foreign keys use surrogate keys from the dimension tables.

    Columns
    -------
    video_key, channel_key, category_key,
    publish_date_key, publish_hour_key,
    view_count, like_count, comment_count, engagement_rate
    """
    df = clean_videos.copy()

    # ── derive date_key and hour_key from published_at_utc ────────────────────
    pub = df["published_at_utc"]
    df["publish_date_key"] = (
        pd.to_datetime(pub, utc=True, errors="coerce")
        .dt.strftime("%Y%m%d")
        .astype("Int64")
    )
    df["publish_hour_key"] = (
        pd.to_datetime(pub, utc=True, errors="coerce")
        .dt.hour
        .astype("Int64")
    )

    # ── Join surrogate keys ────────────────────────────────────────────────────
    # video_key
    df = df.merge(
        dim_video[["video_key", "video_id"]].rename(columns={"video_id": "id"}),
        on="id", how="left",
    )

    # channel_key
    df = df.merge(
        dim_channel[["channel_key", "channel_id"]].rename(columns={"channel_id": "snippet__channelId"}),
        on="snippet__channelId", how="left",
    )

    # category_key (snippet__categoryId as str)
    df["snippet__categoryId"] = df["snippet__categoryId"].astype(str)
    df = df.merge(
        dim_category[["category_key", "category_id"]].rename(
            columns={"category_id": "snippet__categoryId"}
        ),
        on="snippet__categoryId", how="left",
    )

    fact = df[[
        "video_key",
        "channel_key",
        "category_key",
        "publish_date_key",
        "publish_hour_key",
        "view_count",
        "like_count",
        "comment_count",
        "engagement_rate",
    ]].copy()

    log.info("fact_video_stats: %d rows", len(fact))
    return fact


def build_fact_channel_daily(
    stg_video_snapshots: pd.DataFrame,
    stg_channels:        pd.DataFrame,
    dim_channel:         pd.DataFrame,
    dim_date:            pd.DataFrame,
) -> pd.DataFrame:
    """
    Build fact_channel_daily – one row per channel_key per date_key across all snapshots.
    Composite primary key is (channel_key, date_key).

    Columns
    -------
    channel_key, date_key, subscribers, total_views, video_count
    """
    ch = stg_channels.copy()
    ch = ch.rename(columns={"id": "channel_id"})

    ch["subscribers"] = _safe_int(ch["statistics__subscriberCount"])
    ch["total_views"] = _safe_int(ch["statistics__viewCount"])
    ch["video_count"] = _safe_int(ch["statistics__videoCount"])
    ch["subscriber_count"] = ch["subscribers"]
    ch["snapshot_views"]   = ch["total_views"]

    # date_key
    ch["date_key"] = (
        pd.to_datetime(ch["snapshot_date"], errors="coerce")
        .dt.strftime("%Y%m%d")
        .astype("Int64")
    )

    # channel_key
    ch = ch.merge(
        dim_channel[["channel_key", "channel_id"]],
        on="channel_id", how="inner",
    )

    fact = ch[[
        "channel_key",
        "date_key",
        "subscribers",
        "total_views",
        "video_count",
        "subscriber_count",
        "snapshot_views",
    ]].copy()

    fact = (
        fact.drop_duplicates(subset=["channel_key", "date_key"])
        .sort_values(by=["channel_key", "date_key"])
        .reset_index(drop=True)
    )

    log.info("fact_channel_daily: %d rows", len(fact))
    return fact


# ══════════════════════════════════════════════════════════════════════════════
# 7. Orchestrator
# ══════════════════════════════════════════════════════════════════════════════

def run() -> dict[str, pd.DataFrame]:
    """
    Execute the full transform pipeline and return all output DataFrames.

    Returns
    -------
    dict with keys:
      clean_videos, rejects,
      dim_date, dim_time, dim_channel, dim_category, dim_video,
      fact_video_stats, fact_channel_daily
    """
    # ── 1. Read staging ───────────────────────────────────────────────────────
    staging = read_staging()
    stg_ch  = staging["channels"]
    stg_pi  = staging["playlist_items"]
    stg_vid = staging["videos"]
    stg_snp = staging["video_snapshots"]

    # ── 2. Clean ──────────────────────────────────────────────────────────────
    clean_vid, rejects = clean_videos(stg_vid, stg_pi)
    clean_ch            = clean_channels(stg_ch)

    # ── 3. Dimensions ─────────────────────────────────────────────────────────
    dim_date     = build_dim_date(clean_vid, stg_snp, stg_ch)
    dim_time     = build_dim_time()
    dim_channel  = build_dim_channel(clean_ch)
    dim_category = build_dim_category(clean_vid)
    dim_video    = build_dim_video(clean_vid)

    # ── 4. Facts ──────────────────────────────────────────────────────────────
    fact_vs  = build_fact_video_stats(
        clean_vid, dim_video, dim_channel, dim_category, dim_date, dim_time
    )
    fact_cd  = build_fact_channel_daily(stg_snp, stg_ch, dim_channel, dim_date)

    # ── 5. Summary ────────────────────────────────────────────────────────────
    _print_summary(
        stg_vid, stg_ch, stg_snp,
        clean_vid, rejects,
        dim_date, dim_time, dim_channel, dim_category, dim_video,
        fact_vs, fact_cd,
    )

    return {
        "clean_videos":       clean_vid,
        "rejects":            rejects,
        "dim_date":           dim_date,
        "dim_time":           dim_time,
        "dim_channel":        dim_channel,
        "dim_category":       dim_category,
        "dim_video":          dim_video,
        "fact_video_stats":   fact_vs,
        "fact_channel_daily": fact_cd,
    }


def _print_summary(
    stg_vid, stg_ch, stg_snp,
    clean_vid, rejects,
    dim_date, dim_time, dim_channel, dim_category, dim_video,
    fact_vs, fact_cd,
) -> None:
    """Print a human-readable transform summary to stdout."""
    sep = "-" * 56

    print(f"\n{'=' * 56}")
    print("  TRANSFORM SUMMARY")
    print(f"{'=' * 56}")

    print(f"\n  {'STAGING IN':30s}  {'ROWS':>8}")
    print(sep)
    print(f"  {'stg_videos':30s}  {len(stg_vid):>8,}")
    print(f"  {'stg_channels':30s}  {len(stg_ch):>8,}")
    print(f"  {'stg_video_snapshots':30s}  {len(stg_snp):>8,}")

    print(f"\n  {'CLEANED OUTPUT':30s}  {'ROWS':>8}")
    print(sep)
    print(f"  {'clean_videos':30s}  {len(clean_vid):>8,}")
    print(f"  {'rejects':30s}  {len(rejects):>8,}")

    if len(rejects) > 0 and "reject_reason" in rejects.columns:
        reason_counts = rejects["reject_reason"].value_counts()
        for reason, cnt in reason_counts.items():
            short = (reason[:42] + "...") if len(reason) > 45 else reason
            print(f"    {'  --> ' + short:46s}  {cnt:>4,}")

    print(f"\n  {'DIMENSION':30s}  {'ROWS':>8}")
    print(sep)
    print(f"  {'dim_date':30s}  {len(dim_date):>8,}")
    print(f"  {'dim_time':30s}  {len(dim_time):>8,}")
    print(f"  {'dim_channel':30s}  {len(dim_channel):>8,}")
    print(f"  {'dim_category':30s}  {len(dim_category):>8,}")
    print(f"  {'dim_video':30s}  {len(dim_video):>8,}")

    print(f"\n  {'FACT TABLE':30s}  {'ROWS':>8}")
    print(sep)
    print(f"  {'fact_video_stats':30s}  {len(fact_vs):>8,}")
    print(f"  {'fact_channel_daily':30s}  {len(fact_cd):>8,}")
    print(f"\n{'=' * 56}\n")


if __name__ == "__main__":
    run()
