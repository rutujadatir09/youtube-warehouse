"""
make_sample_data.py
====================
Generates realistic fake data that mirrors the exact JSON shape returned by the
YouTube Data API v3 for three resource types:

  • channels      – channel metadata + statistics
  • playlistItems – items inside an "uploads" playlist
  • videos        – per-video snippet, statistics, and contentDetails

Messy cases deliberately included
-----------------------------------
  • Some videos have hidden likes (likesHidden=True) or hidden comments
  • A few duplicate video IDs scattered across the dataset
  • Zero-view videos (brand-new / unlisted)
  • Deleted-video tombstone entries
  • Mixed ISO 8601 durations (PT30S up to PT1H45M20S)

Output
------
  raw/channels_YYYY-MM-DD.json
  raw/playlist_items_YYYY-MM-DD.json
  raw/videos_YYYY-MM-DD.json

Seven daily snapshots are generated so growth can be analysed.
The "today" snapshot uses the script's actual run date; the other six
snapshots are dated to the six preceding days.

Usage
-----
  python src/make_sample_data.py
"""

import json
import random
import string
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

# ── Force UTF-8 output so Unicode chars print cleanly on Windows terminals ────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── make sure src/ is importable regardless of CWD ────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    RAW_DIR,
    CHANNELS_CSV,
    VIDEOS_PER_CHANNEL,
    SNAPSHOT_DAYS,
    DUPLICATE_COUNT,
    ZERO_VIEW_COUNT,
    DELETED_VIDEO_COUNT,
    HIDDEN_COUNTS_PROB,
    DATE_FMT,
)

# ── reproducible randomness ───────────────────────────────────────────────────
random.seed(42)

# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _rand_id(length: int = 11) -> str:
    """Generate a random YouTube-style alphanumeric ID."""
    chars = string.ascii_letters + string.digits + "_-"
    return "".join(random.choices(chars, k=length))


def _rand_date(start: datetime, end: datetime) -> datetime:
    """Return a random datetime between *start* and *end*."""
    delta = end - start
    return start + timedelta(seconds=random.randint(0, int(delta.total_seconds())))


def _iso8601_duration(total_seconds: int) -> str:
    """Convert *total_seconds* to an ISO 8601 duration string (e.g. PT1H5M30S)."""
    h, rem = divmod(total_seconds, 3600)
    m, s   = divmod(rem, 60)
    parts  = "PT"
    if h:
        parts += f"{h}H"
    if m:
        parts += f"{m}M"
    if s or (not h and not m):   # always emit seconds for sub-minute durations
        parts += f"{s}S"
    return parts


def _fmt_dt(dt: datetime) -> str:
    """Format a datetime as a YouTube API RFC-3339 string."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ══════════════════════════════════════════════════════════════════════════════
# Channel resource builder
# ══════════════════════════════════════════════════════════════════════════════

def build_channel_resource(row: dict, snapshot_date: datetime, seed_offset: int) -> dict:
    """
    Build a fake YouTube Data API v3 *channel* resource for one channel.

    Parameters
    ----------
    row           : dict  – One row from channels.csv
    snapshot_date : datetime – The date of this particular snapshot
    seed_offset   : int   – Added to the random seed so each snapshot differs
    """
    rng = random.Random(hash(row["channel_id"]) + seed_offset)

    # Subscriber counts vary slightly per snapshot to simulate growth
    base_subs = rng.randint(500_000, 18_000_000)
    subs      = base_subs + seed_offset * rng.randint(100, 5_000)

    base_views = rng.randint(10_000_000, 5_000_000_000)
    views      = base_views + seed_offset * rng.randint(1_000, 50_000)

    video_count = rng.randint(150, 1_500)

    upload_playlist = "UU" + row["channel_id"][2:]   # standard uploads playlist pattern

    published = _rand_date(
        datetime(2005, 1, 1, tzinfo=timezone.utc),
        datetime(2018, 12, 31, tzinfo=timezone.utc),
    )

    return {
        "kind": "youtube#channel",
        "etag": _rand_id(27),
        "id": row["channel_id"],
        "snippet": {
            "title": row["channel_name"],
            "description": f"Official YouTube channel of {row['channel_name']}.",
            "customUrl": "@" + row["channel_name"].lower().replace(" ", "").replace("–", ""),
            "publishedAt": _fmt_dt(published),
            "thumbnails": {
                "default": {"url": f"https://i.ytimg.com/channel/{row['channel_id']}/default.jpg"},
                "medium":  {"url": f"https://i.ytimg.com/channel/{row['channel_id']}/mqdefault.jpg"},
                "high":    {"url": f"https://i.ytimg.com/channel/{row['channel_id']}/hqdefault.jpg"},
            },
            "country": row["country"],
        },
        "contentDetails": {
            "relatedPlaylists": {
                "likes":   "",
                "uploads": upload_playlist,
            }
        },
        "statistics": {
            "viewCount":             str(views),
            "subscriberCount":       str(subs),
            "hiddenSubscriberCount": False,
            "videoCount":            str(video_count),
        },
        "topicDetails": {
            "topicIds":        ["/m/07c1v"],   # simplified – technology
            "topicCategories": [f"https://en.wikipedia.org/wiki/{row['niche'].capitalize()}"],
        },
    }


def build_channels_response(rows: list[dict], snapshot_date: datetime, seed_offset: int) -> dict:
    """Wrap a list of channel resources in a ListResponse envelope."""
    items = [build_channel_resource(r, snapshot_date, seed_offset) for r in rows]
    return {
        "kind":          "youtube#channelListResponse",
        "etag":          _rand_id(27),
        "pageInfo":      {"totalResults": len(items), "resultsPerPage": 50},
        "items":         items,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Video ID pool – generated once, shared between playlist items & video details
# ══════════════════════════════════════════════════════════════════════════════

def build_video_id_pool(rows: list[dict]) -> dict[str, list[str]]:
    """
    Create a mapping of channel_id → list of video IDs.

    Duplicates, zero-view, and deleted entries are injected at this stage
    and flagged so later builders can treat them specially.
    """
    pool: dict[str, list[str]] = {}
    all_ids: list[str]         = []

    for row in rows:
        ids = [_rand_id(11) for _ in range(VIDEOS_PER_CHANNEL)]
        pool[row["channel_id"]] = ids
        all_ids.extend(ids)

    return pool


# ══════════════════════════════════════════════════════════════════════════════
# Playlist-items resource builder
# ══════════════════════════════════════════════════════════════════════════════

def build_playlist_item(
    video_id:       str,
    channel_id:     str,
    channel_title:  str,
    position:       int,
    published_at:   datetime,
    is_deleted:     bool = False,
) -> dict:
    """Build one *playlistItem* resource in the YouTube API v3 shape."""
    playlist_item_id = _rand_id(34)
    upload_playlist  = "UU" + channel_id[2:]

    if is_deleted:
        # Deleted videos surface with privacyStatus=private and no snippet title
        return {
            "kind":  "youtube#playlistItem",
            "etag":  _rand_id(27),
            "id":    playlist_item_id,
            "snippet": {
                "publishedAt":            _fmt_dt(published_at),
                "channelId":              channel_id,
                "title":                  "Deleted video",
                "description":            "",
                "channelTitle":           channel_title,
                "playlistId":             upload_playlist,
                "position":               position,
                "resourceId": {
                    "kind":    "youtube#video",
                    "videoId": video_id,
                },
                "videoOwnerChannelTitle": channel_title,
                "videoOwnerChannelId":    channel_id,
                "thumbnails":             {},
            },
            "status": {"privacyStatus": "private"},
            "contentDetails": {
                "videoId":            video_id,
                "videoPublishedAt":   _fmt_dt(published_at),
                "endAt":              None,
                "startAt":            None,
                "note":               None,
            },
        }

    return {
        "kind":  "youtube#playlistItem",
        "etag":  _rand_id(27),
        "id":    playlist_item_id,
        "snippet": {
            "publishedAt":            _fmt_dt(published_at),
            "channelId":              channel_id,
            "title":                  f"Video #{position + 1} – {channel_title}",
            "description":            f"Auto-generated description for video {video_id}.",
            "channelTitle":           channel_title,
            "playlistId":             upload_playlist,
            "position":               position,
            "resourceId": {
                "kind":    "youtube#video",
                "videoId": video_id,
            },
            "videoOwnerChannelTitle": channel_title,
            "videoOwnerChannelId":    channel_id,
            "thumbnails": {
                "default": {"url": f"https://i.ytimg.com/vi/{video_id}/default.jpg",  "width": 120, "height": 90},
                "medium":  {"url": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg","width": 320, "height": 180},
                "high":    {"url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg","width": 480, "height": 360},
                "standard":{"url": f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg","width": 640, "height": 480},
            },
        },
        "status": {"privacyStatus": "public"},
        "contentDetails": {
            "videoId":          video_id,
            "videoPublishedAt": _fmt_dt(published_at),
            "endAt":            None,
            "startAt":          None,
            "note":             None,
        },
    }


def build_playlist_items_response(
    rows:        list[dict],
    video_pool:  dict[str, list[str]],
    snapshot_date: datetime,
    deleted_ids: set[str],
) -> dict:
    """
    Build a combined playlistItems ListResponse covering all channels.

    In a real API each channel would be a separate paginated request;
    here we aggregate everything into one file for simplicity.
    """
    all_items: list[dict] = []
    cutoff = datetime(2015, 1, 1, tzinfo=timezone.utc)
    now    = snapshot_date

    for row in rows:
        ids   = video_pool[row["channel_id"]]
        title = row["channel_name"]
        cid   = row["channel_id"]

        for pos, vid in enumerate(ids):
            pub = _rand_date(cutoff, now)
            is_deleted = vid in deleted_ids
            item = build_playlist_item(vid, cid, title, pos, pub, is_deleted=is_deleted)
            all_items.append(item)

    return {
        "kind":     "youtube#playlistItemListResponse",
        "etag":     _rand_id(27),
        "pageInfo": {"totalResults": len(all_items), "resultsPerPage": 50},
        "items":    all_items,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Video resource builder
# ══════════════════════════════════════════════════════════════════════════════

# Broad video categories (id, name) used across niches
_VIDEO_CATEGORIES = {
    "tech":      [("28", "Science & Technology"), ("27", "Education"), ("28", "Science & Technology")],
    "gaming":    [("20", "Gaming"), ("24", "Entertainment"), ("20", "Gaming")],
    "education": [("27", "Education"), ("28", "Science & Technology"), ("22", "People & Blogs")],
    "music":     [("10", "Music"), ("24", "Entertainment"), ("10", "Music")],
}

_TAGS_BY_NICHE = {
    "tech":      ["technology", "review", "unboxing", "tutorial", "programming", "hardware"],
    "gaming":    ["gaming", "gameplay", "let's play", "walkthrough", "esports", "streaming"],
    "education": ["education", "learning", "science", "history", "explained", "facts"],
    "music":     ["music", "official video", "album", "lyrics", "cover", "live performance"],
}


def build_video_resource(
    video_id:      str,
    channel_id:    str,
    channel_title: str,
    niche:         str,
    published_at:  datetime,
    snapshot_date: datetime,
    seed_offset:   int,
    is_zero_view:  bool = False,
    is_deleted:    bool = False,
    likes_hidden:  bool = False,
    comments_hidden: bool = False,
) -> dict:
    """
    Build a YouTube Data API v3 *video* resource with snippet, statistics,
    contentDetails, and topicDetails parts.
    """
    rng = random.Random(hash(video_id) + seed_offset)

    age_days = max(1, (snapshot_date - published_at).days)

    # ── Duration (ISO 8601) ───────────────────────────────────────────────────
    duration_secs = rng.randint(30, 6300)   # 30 s … ~1 h 45 m
    duration_iso  = _iso8601_duration(duration_secs)

    # ── Statistics (counts returned as strings by the API) ────────────────────
    if is_deleted:
        # Deleted videos return no statistics
        statistics: dict = {}
    elif is_zero_view:
        statistics = {
            "viewCount":    "0",
            "likeCount":    "0",
            "favoriteCount":"0",
            "commentCount": "0",
        }
    else:
        views    = rng.randint(1_000, 50_000_000) + seed_offset * rng.randint(0, 500)
        likes    = int(views * rng.uniform(0.02, 0.12))
        comments = int(views * rng.uniform(0.001, 0.03))

        statistics = {
            "viewCount":     str(views),
            "favoriteCount": "0",   # always "0" in v3 API
        }
        # Hidden likes/comments – API omits the key entirely
        if not likes_hidden:
            statistics["likeCount"]    = str(likes)
        if not comments_hidden:
            statistics["commentCount"] = str(comments)

    # ── Snippet ───────────────────────────────────────────────────────────────
    category_id, category_name = rng.choice(_VIDEO_CATEGORIES.get(niche, [("28", "Science & Technology")]))
    tags  = rng.sample(_TAGS_BY_NICHE.get(niche, []), k=min(5, len(_TAGS_BY_NICHE.get(niche, []))))

    snippet: dict = {
        "publishedAt":        _fmt_dt(published_at),
        "channelId":          channel_id,
        "title":              ("This video is unavailable" if is_deleted
                               else f"{channel_title} – Video {video_id[:6]}"),
        "description":        ("" if is_deleted
                               else f"Watch more from {channel_title}. Tags: {', '.join(tags)}."),
        "thumbnails": (
            {} if is_deleted else {
                "default":  {"url": f"https://i.ytimg.com/vi/{video_id}/default.jpg",   "width": 120, "height": 90},
                "medium":   {"url": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg", "width": 320, "height": 180},
                "high":     {"url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg", "width": 480, "height": 360},
                "standard": {"url": f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg", "width": 640, "height": 480},
                "maxres":   {"url": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg","width":1280,"height":720},
            }
        ),
        "channelTitle":       channel_title,
        "tags":               [] if is_deleted else tags,
        "categoryId":         category_id,
        "liveBroadcastContent": "none",
        "defaultLanguage":    "en",
        "localized": {
            "title":       ("This video is unavailable" if is_deleted
                            else f"{channel_title} – Video {video_id[:6]}"),
            "description": "",
        },
    }

    # ── contentDetails ────────────────────────────────────────────────────────
    content_details: dict = {
        "duration":        ("PT0S" if is_deleted else duration_iso),
        "dimension":       "2d",
        "definition":      rng.choice(["hd", "sd"]),
        "caption":         str(rng.choice([True, False])).lower(),
        "licensedContent": rng.choice([True, False]),
        "contentRating":   {},
        "projection":      "rectangular",
    }

    return {
        "kind":           "youtube#video",
        "etag":           _rand_id(27),
        "id":             video_id,
        "snippet":        snippet,
        "contentDetails": content_details,
        "statistics":     statistics,
        "topicDetails": {
            "topicCategories": [
                f"https://en.wikipedia.org/wiki/{category_name.replace(' ', '_')}"
            ]
        },
    }


def build_videos_response(
    rows:           list[dict],
    video_pool:     dict[str, list[str]],
    snapshot_date:  datetime,
    seed_offset:    int,
    deleted_ids:    set[str],
    zero_view_ids:  set[str],
    duplicate_ids:  list[str],
) -> dict:
    """
    Build a combined video ListResponse for all channels, injecting messy cases.

    Duplicate video_ids appear multiple times (same id, slightly different stats).
    """
    items:          list[dict] = []
    seen_ids:       set[str]  = set()   # track originals before we append dupes
    cutoff = datetime(2015, 1, 1, tzinfo=timezone.utc)

    for row in rows:
        niche = row["niche"]
        cid   = row["channel_id"]
        title = row["channel_name"]

        for vid in video_pool[cid]:
            is_deleted      = vid in deleted_ids
            is_zero         = vid in zero_view_ids
            likes_hidden    = (not is_deleted) and (random.random() < HIDDEN_COUNTS_PROB)
            comments_hidden = (not is_deleted) and (random.random() < HIDDEN_COUNTS_PROB)

            published_at = _rand_date(cutoff, snapshot_date)

            resource = build_video_resource(
                video_id        = vid,
                channel_id      = cid,
                channel_title   = title,
                niche           = niche,
                published_at    = published_at,
                snapshot_date   = snapshot_date,
                seed_offset     = seed_offset,
                is_zero_view    = is_zero,
                is_deleted      = is_deleted,
                likes_hidden    = likes_hidden,
                comments_hidden = comments_hidden,
            )
            items.append(resource)
            seen_ids.add(vid)

    # ── Inject duplicate entries ──────────────────────────────────────────────
    # Pick *duplicate_ids* from already-seen IDs and append them again with a
    # slightly perturbed seed so stats differ (simulating a pagination edge-case).
    dup_candidates = [i for i in seen_ids if i in duplicate_ids]
    for dup_id in dup_candidates:
        # Find the original resource to clone channel context
        orig = next(r for r in items if r["id"] == dup_id)
        cid   = orig["snippet"]["channelId"]
        title = orig["snippet"]["channelTitle"]
        niche = next(r["niche"] for r in rows if r["channel_id"] == cid)
        pub   = orig["snippet"]["publishedAt"]
        pub_dt = datetime.strptime(pub, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

        dup_resource = build_video_resource(
            video_id      = dup_id,
            channel_id    = cid,
            channel_title = title,
            niche         = niche,
            published_at  = pub_dt,
            snapshot_date = snapshot_date,
            seed_offset   = seed_offset + 9999,   # different seed → slightly different counts
        )
        items.append(dup_resource)

    return {
        "kind":     "youtube#videoListResponse",
        "etag":     _rand_id(27),
        "pageInfo": {"totalResults": len(items), "resultsPerPage": 50},
        "items":    items,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Snapshot writer
# ══════════════════════════════════════════════════════════════════════════════

def write_json(path: Path, data: dict) -> None:
    """Serialise *data* to *path* as pretty-printed JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    print(f"  Wrote {path.relative_to(path.parent.parent.parent)} "
          f"({path.stat().st_size:,} bytes)")


def generate_snapshot(
    rows:          list[dict],
    video_pool:    dict[str, list[str]],
    snapshot_date: datetime,
    seed_offset:   int,
    deleted_ids:   set[str],
    zero_view_ids: set[str],
    duplicate_ids: list[str],
) -> None:
    """Generate and write the three JSON files for a single snapshot date."""
    date_str = snapshot_date.strftime(DATE_FMT)
    print(f"\n>>  Snapshot {date_str} (seed_offset={seed_offset})")

    # 1. Channel resources
    ch_resp = build_channels_response(rows, snapshot_date, seed_offset)
    write_json(RAW_DIR / f"channels_{date_str}.json", ch_resp)

    # 2. PlaylistItem resources
    pi_resp = build_playlist_items_response(rows, video_pool, snapshot_date, deleted_ids)
    write_json(RAW_DIR / f"playlist_items_{date_str}.json", pi_resp)

    # 3. Video resources
    vid_resp = build_videos_response(
        rows, video_pool, snapshot_date, seed_offset,
        deleted_ids, zero_view_ids, duplicate_ids,
    )
    write_json(RAW_DIR / f"videos_{date_str}.json", vid_resp)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    print("-" * 60)
    print("YouTube Warehouse - Sample Data Generator")
    print("-" * 60)

    # ── Read channel list ─────────────────────────────────────────────────────
    with CHANNELS_CSV.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    print(f"\nLoaded {len(rows)} channels from {CHANNELS_CSV.name}")

    # ── Build the video-ID pool (shared across all snapshots for consistency) ─
    video_pool = build_video_id_pool(rows)
    all_ids    = [vid for ids in video_pool.values() for vid in ids]
    total_ids  = len(all_ids)

    # ── Pick special-case video IDs ───────────────────────────────────────────
    # Deleted videos (across different channels)
    deleted_ids   = set(random.sample(all_ids, DELETED_VIDEO_COUNT))

    # Zero-view videos (must NOT overlap with deleted)
    remaining     = [v for v in all_ids if v not in deleted_ids]
    zero_view_ids = set(random.sample(remaining, ZERO_VIEW_COUNT))

    # Duplicate IDs to inject (must be "normal" videos)
    normal        = [v for v in remaining if v not in zero_view_ids]
    duplicate_ids = random.sample(normal, DUPLICATE_COUNT)

    print(f"Total unique video IDs  : {total_ids}")
    print(f"Deleted video IDs       : {sorted(deleted_ids)}")
    print(f"Zero-view video IDs     : {sorted(zero_view_ids)}")
    print(f"Duplicate injection IDs : {duplicate_ids}")

    # ── Generate SNAPSHOT_DAYS daily snapshots ────────────────────────────────
    today = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    for day_offset in range(SNAPSHOT_DAYS - 1, -1, -1):   # oldest → newest
        snapshot_date = today - timedelta(days=day_offset)
        seed_offset   = SNAPSHOT_DAYS - 1 - day_offset     # 0 for oldest, 6 for today

        generate_snapshot(
            rows          = rows,
            video_pool    = video_pool,
            snapshot_date = snapshot_date,
            seed_offset   = seed_offset,
            deleted_ids   = deleted_ids,
            zero_view_ids = zero_view_ids,
            duplicate_ids = duplicate_ids,
        )

    # ── Summary ───────────────────────────────────────────────────────────────
    raw_files = sorted(RAW_DIR.glob("*.json"))
    print(f"\n{'─' * 60}")
    print(f"Done! {len(raw_files)} files written to raw/")
    print(f"{'─' * 60}")
    for f in raw_files:
        print(f"  {f.name:45s}  {f.stat().st_size:>10,} bytes")


if __name__ == "__main__":
    main()
