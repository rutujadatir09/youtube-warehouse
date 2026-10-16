"""
extract.py
==========
Extracts YouTube channel, playlist-item, and video data and saves raw JSON
files to raw/ with today's date in the filename.

Two modes, controlled by SOURCE in config.py
--------------------------------------------
  "sample"  – Read the pre-generated JSON files from raw/ (no API key needed).
               Returns the most-recent set of raw files found for each type.

  "api"     – Call the YouTube Data API v3 using YOUTUBE_API_KEY from .env.
               For each channel in data/channels.csv:
                 1. channels.list  (snippet, statistics, contentDetails)
                 2. playlistItems.list  (page through uploads playlist)
                 3. videos.list  (snippet, statistics, contentDetails – 50 at a time)
               The search endpoint is NEVER used.

Quota tracking
--------------
Every list() call costs 1 quota unit. A running total is printed at the end.

Retry / backoff
---------------
Transient HTTP errors (429, 500, 502, 503, 504) are retried up to
API_MAX_RETRIES times with exponential back-off (API_BACKOFF_BASE ** attempt).

Usage
-----
  python src/extract.py
"""

from __future__ import annotations

import json
import sys
import time
import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── ensure src/ is on the path ────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    RAW_DIR,
    CHANNELS_CSV,
    YOUTUBE_API_KEY,
    SOURCE,
    API_MAX_RETRIES,
    API_BACKOFF_BASE,
    API_PAGE_SIZE,
    API_VIDEO_BATCH,
    DATE_FMT,
)

# ── Force UTF-8 output on Windows terminals ───────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# YouTube Data API v3 base URL
_YT_API_BASE = "https://www.googleapis.com/youtube/v3"

# ══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════════════════════

def _load_channels() -> list[dict]:
    """Read channels.csv and return a list of row dicts."""
    with CHANNELS_CSV.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_raw(name: str, data: dict, date_str: str) -> Path:
    """Serialise *data* to raw/<name>_<date_str>.json and return the path."""
    path = RAW_DIR / f"{name}_{date_str}.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    log.info("Wrote %s  (%s bytes)", path.name, f"{path.stat().st_size:,}")
    return path


def _today_str() -> str:
    return datetime.now(tz=timezone.utc).strftime(DATE_FMT)


# ══════════════════════════════════════════════════════════════════════════════
# MODE: sample  –  read existing raw/ files
# ══════════════════════════════════════════════════════════════════════════════

def _latest_raw_files() -> dict[str, Path]:
    """
    Return the most-recent raw file for each resource type.

    Looks for files matching raw/<type>_YYYY-MM-DD.json and picks the one
    with the lexicographically greatest date string (i.e. newest date).
    """
    types = ("channels", "playlist_items", "videos")
    result: dict[str, Path] = {}
    for t in types:
        candidates = sorted(RAW_DIR.glob(f"{t}_*.json"), reverse=True)
        if not candidates:
            raise FileNotFoundError(
                f"No raw file found for type '{t}' in {RAW_DIR}. "
                "Run src/make_sample_data.py first."
            )
        result[t] = candidates[0]
        log.info("Sample mode – using %s", candidates[0].name)
    return result


def extract_sample() -> dict[str, dict]:
    """
    Load the latest sample raw files and return them as in-memory dicts.
    Also copies them under today's date if today's file doesn't already exist.
    """
    files  = _latest_raw_files()
    today  = _today_str()
    result = {}

    for resource_type, src_path in files.items():
        with src_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        result[resource_type] = data

        # Write a today-dated copy so stage.py always finds a fresh file
        dest = RAW_DIR / f"{resource_type}_{today}.json"
        if not dest.exists():
            _write_raw(resource_type, data, today)
        else:
            log.info("Today's file already exists: %s – skipping copy", dest.name)

    return result


# ══════════════════════════════════════════════════════════════════════════════
# MODE: api  –  call YouTube Data API v3
# ══════════════════════════════════════════════════════════════════════════════

class QuotaCounter:
    """Tracks API quota units consumed during one extraction run."""

    def __init__(self) -> None:
        self._units = 0

    def charge(self, units: int = 1) -> None:
        self._units += units

    @property
    def total(self) -> int:
        return self._units

    def report(self) -> None:
        log.info("Total API quota units consumed this run: %d", self._units)


_quota = QuotaCounter()


def _api_get(endpoint: str, params: dict) -> dict:
    """
    GET <endpoint> with retry / exponential back-off.

    Retryable status codes: 429, 500, 502, 503, 504.
    Non-retryable errors (e.g. 400, 403) raise immediately.

    Charges 1 quota unit per successful call.
    """
    if not YOUTUBE_API_KEY:
        raise RuntimeError(
            "YOUTUBE_API_KEY is not set. Add it to .env or set SOURCE='sample'."
        )

    url    = f"{_YT_API_BASE}/{endpoint}"
    params = {**params, "key": YOUTUBE_API_KEY}

    retryable = {429, 500, 502, 503, 504}

    for attempt in range(API_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            if attempt == API_MAX_RETRIES:
                raise
            wait = API_BACKOFF_BASE ** attempt
            log.warning("Network error (%s) – retry %d in %ds", exc, attempt + 1, wait)
            time.sleep(wait)
            continue

        if resp.status_code == 200:
            _quota.charge(1)
            return resp.json()

        if resp.status_code in retryable:
            if attempt == API_MAX_RETRIES:
                resp.raise_for_status()
            wait = API_BACKOFF_BASE ** attempt
            log.warning(
                "HTTP %d for %s – retry %d/%d in %ds",
                resp.status_code, endpoint, attempt + 1, API_MAX_RETRIES, wait,
            )
            time.sleep(wait)
            continue

        # Non-retryable error
        resp.raise_for_status()

    # Should never reach here, but satisfy type-checkers
    raise RuntimeError("Exhausted retries")


# ── channels.list ─────────────────────────────────────────────────────────────

def _fetch_channels(channel_ids: list[str]) -> list[dict]:
    """
    Fetch channel resources for all IDs in one call (max 50 per call).

    Parts: snippet, statistics, contentDetails, topicDetails
    """
    items: list[dict] = []
    # Process in batches of 50 (API limit)
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i : i + 50]
        data  = _api_get(
            "channels",
            {
                "part": "snippet,statistics,contentDetails,topicDetails",
                "id":   ",".join(batch),
                "maxResults": 50,
            },
        )
        items.extend(data.get("items", []))
    log.info("Fetched %d channel records", len(items))
    return items


# ── playlistItems.list  (paginated) ──────────────────────────────────────────

def _fetch_playlist_items(playlist_id: str) -> list[dict]:
    """
    Page through all items in an uploads playlist.

    Each page call costs 1 quota unit; snippet + contentDetails + status
    are fetched so we can detect deleted/private videos.
    """
    items:       list[dict]    = []
    next_page:   str | None    = None
    page_number: int           = 0

    while True:
        params: dict = {
            "part":       "snippet,contentDetails,status",
            "playlistId": playlist_id,
            "maxResults": API_PAGE_SIZE,
        }
        if next_page:
            params["pageToken"] = next_page

        data      = _api_get("playlistItems", params)
        page_number += 1
        page_items = data.get("items", [])
        items.extend(page_items)

        log.debug(
            "  playlistItems page %d – got %d items (total so far: %d)",
            page_number, len(page_items), len(items),
        )

        next_page = data.get("nextPageToken")
        if not next_page:
            break

    log.info("  Fetched %d playlist items from playlist %s", len(items), playlist_id)
    return items


# ── videos.list  (batched by 50 IDs) ─────────────────────────────────────────

def _fetch_videos(video_ids: list[str]) -> list[dict]:
    """
    Fetch full video resources in batches of API_VIDEO_BATCH (max 50).

    Parts: snippet, statistics, contentDetails, topicDetails
    """
    items: list[dict] = []

    for i in range(0, len(video_ids), API_VIDEO_BATCH):
        batch = video_ids[i : i + API_VIDEO_BATCH]
        data  = _api_get(
            "videos",
            {
                "part": "snippet,statistics,contentDetails,topicDetails",
                "id":   ",".join(batch),
                "maxResults": API_VIDEO_BATCH,
            },
        )
        items.extend(data.get("items", []))
        log.debug("  videos batch %d–%d: fetched %d", i + 1, i + len(batch), len(data.get("items", [])))

    log.info("Fetched %d video records (from %d IDs)", len(items), len(video_ids))
    return items


# ── orchestrator ──────────────────────────────────────────────────────────────

def extract_api(rows: list[dict]) -> dict[str, dict]:
    """
    Full API extraction pipeline for all channels.

    Returns
    -------
    dict with keys 'channels', 'playlist_items', 'videos'
    each holding a YouTube ListResponse-shaped dict.
    """
    channel_ids = [r["channel_id"] for r in rows]

    # ── 1. Channels ───────────────────────────────────────────────────────────
    log.info("Fetching channel metadata for %d channels...", len(channel_ids))
    ch_items = _fetch_channels(channel_ids)

    channels_response = {
        "kind":     "youtube#channelListResponse",
        "etag":     "",
        "pageInfo": {"totalResults": len(ch_items), "resultsPerPage": 50},
        "items":    ch_items,
    }

    # ── 2. PlaylistItems  (uploads playlist per channel) ─────────────────────
    all_playlist_items: list[dict] = []
    all_video_ids:      list[str]  = []

    for ch in ch_items:
        cid         = ch["id"]
        upload_pl   = (
            ch.get("contentDetails", {})
              .get("relatedPlaylists", {})
              .get("uploads", "")
        )
        if not upload_pl:
            log.warning("No uploads playlist found for channel %s – skipping", cid)
            continue

        log.info("Paging through uploads playlist %s (channel %s)...", upload_pl, cid)
        pi_items = _fetch_playlist_items(upload_pl)
        all_playlist_items.extend(pi_items)

        # Collect video IDs, ignoring deleted (no videoPublishedAt)
        for item in pi_items:
            vid = (
                item.get("snippet", {})
                    .get("resourceId", {})
                    .get("videoId", "")
            )
            if vid:
                all_video_ids.append(vid)

    playlist_items_response = {
        "kind":     "youtube#playlistItemListResponse",
        "etag":     "",
        "pageInfo": {"totalResults": len(all_playlist_items), "resultsPerPage": API_PAGE_SIZE},
        "items":    all_playlist_items,
    }

    # ── 3. Videos ─────────────────────────────────────────────────────────────
    log.info("Fetching video details for %d video IDs...", len(all_video_ids))
    vid_items = _fetch_videos(all_video_ids)

    videos_response = {
        "kind":     "youtube#videoListResponse",
        "etag":     "",
        "pageInfo": {"totalResults": len(vid_items), "resultsPerPage": API_VIDEO_BATCH},
        "items":    vid_items,
    }

    return {
        "channels":       channels_response,
        "playlist_items": playlist_items_response,
        "videos":         videos_response,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run() -> dict[str, dict]:
    """
    Run the extraction in the mode defined by SOURCE in config.py.

    Returns a dict with keys 'channels', 'playlist_items', 'videos'.
    """
    today = _today_str()
    log.info("Extract started  |  source=%s  |  date=%s", SOURCE, today)

    if SOURCE == "sample":
        data = extract_sample()

    elif SOURCE == "api":
        rows = _load_channels()
        data = extract_api(rows)
        # Save raw JSON files
        for resource_type, payload in data.items():
            _write_raw(resource_type, payload, today)
        _quota.report()

    else:
        raise ValueError(f"Unknown SOURCE '{SOURCE}'. Must be 'sample' or 'api'.")

    # Summarise
    for rtype, payload in data.items():
        item_count = len(payload.get("items", []))
        log.info("  %-20s  %d items", rtype, item_count)

    log.info("Extraction complete.")
    return data


if __name__ == "__main__":
    run()
