"""
make_sample_data.py
====================
Generates realistic fake data that mirrors the exact JSON shape returned by the
YouTube Data API v3 for three resource types:

  • channels      – channel metadata + statistics
  • playlistItems – items inside an "uploads" playlist
  • videos        – per-video snippet, statistics, and contentDetails

Realistic & Analytical Features
-------------------------------
  • Subscriber counts grow monotonically across all 7 snapshot days with
    realistic channel baselines and distinct growth rates.
  • Video views, likes, and comments are non-decreasing across snapshots, with
    higher acceleration in the first days following publication.
  • Rich, realistic titles: ~25% question titles, ~40% titles with numbers, ~35% plain.
  • Views and engagement rates loosely depend on duration band and title syntax features.
  • Messy edge cases preserved: hidden likes/comments, duplicate IDs, zero-view
    videos, and deleted video tombstones.

Output
------
  raw/channels_YYYY-MM-DD.json
  raw/playlist_items_YYYY-MM-DD.json
  raw/videos_YYYY-MM-DD.json

Usage
-----
  python src/make_sample_data.py
"""

from __future__ import annotations

import csv
import json
import random
import string
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Force UTF-8 output on Windows terminals ──────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Ensure src/ is importable regardless of CWD ──────────────────────────────
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

# ── Seed for reproducible generation ──────────────────────────────────────────
random.seed(42)

# ══════════════════════════════════════════════════════════════════════════════
# Channel Profiles (Realistic base metrics and daily growth rates)
# ══════════════════════════════════════════════════════════════════════════════

CHANNEL_PROFILES: dict[str, dict] = {
    # Tech
    "UCXuqSBlHAE6Xw-yeJA0Tunw": {  # Linus Tech Tips
        "base_subs": 15_600_000, "daily_subs": 8_500, "base_views": 7_200_000_000, "daily_views": 4_200_000, "video_count": 6200
    },
    "UCBJycsmduvYEL83-oldO34A": {  # Marques Brownlee
        "base_subs": 18_200_000, "daily_subs": 12_000, "base_views": 4_100_000_000, "daily_views": 3_500_000, "video_count": 1650
    },
    "UCY1kMZp36IQSyNx_9h4mpCg": {  # Mark Rober
        "base_subs": 53_500_000, "daily_subs": 85_000, "base_views": 5_600_000_000, "daily_views": 18_000_000, "video_count": 120
    },
    # Education
    "UCsXVk37bltHxD1rDPwtNM8Q": {  # Kurzgesagt – In a Nutshell
        "base_subs": 22_100_000, "daily_subs": 22_000, "base_views": 2_600_000_000, "daily_views": 5_100_000, "video_count": 195
    },
    "UCsooa4yRKGN_zEE8iknghZA": {  # TED-Ed
        "base_subs": 19_400_000, "daily_subs": 16_000, "base_views": 4_250_000_000, "daily_views": 4_800_000, "video_count": 2100
    },
    "UCX6b17PVsYBQ0ip5gyeme-Q": {  # CrashCourse
        "base_subs": 15_100_000, "daily_subs": 7_500, "base_views": 1_880_000_000, "daily_views": 2_100_000, "video_count": 1500
    },
    # Gaming
    "UCIPPMRA040LQr5QPyJEbmXA": {  # MrBeast Gaming (Fastest subscriber growth)
        "base_subs": 42_000_000, "daily_subs": 115_000, "base_views": 7_300_000_000, "daily_views": 25_000_000, "video_count": 160
    },
    "UCddiUEpeqJcYeBxX1IVBKvQ": {  # Markiplier
        "base_subs": 36_400_000, "daily_subs": 11_000, "base_views": 20_500_000_000, "daily_views": 6_000_000, "video_count": 5400
    },
    "UCYzPXprvl5Y-Sf0g4vX-m6g": {  # Jacksepticeye
        "base_subs": 30_600_000, "daily_subs": 9_000, "base_views": 16_200_000_000, "daily_views": 5_200_000, "video_count": 5100
    },
    # Music
    "UCq-Fj5jknLsUf-MWSy4_brA": {  # T-Series (Highest total volume)
        "base_subs": 245_000_000, "daily_subs": 70_000, "base_views": 235_000_000_000, "daily_views": 45_000_000, "video_count": 19500
    },
    "UCiGm_E4ZwYSHV3bcW1pnmPg": {  # Billie Eilish
        "base_subs": 48_800_000, "daily_subs": 19_000, "base_views": 14_100_000_000, "daily_views": 9_500_000, "video_count": 95
    },
    "UC20vb-R_px4CguHzzBPhoyQ": {  # Eminem
        "base_subs": 58_100_000, "daily_subs": 14_000, "base_views": 27_200_000_000, "daily_views": 7_800_000, "video_count": 170
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# Rich Realistic Video Titles by Niche
# ══════════════════════════════════════════════════════════════════════════════

NICHE_TITLES: dict[str, dict[str, list[str]]] = {
    "tech": {
        "question": [
            "Is This the Best Smartphone of 2026?",
            "Can an AI Really Build an Operating System?",
            "Why Did Intel Fall Behind AMD?",
            "Should You Still Buy an M2 Mac?",
            "Will Quantum Computing Break All Modern Encryption?",
            "Is Linux Finally Ready for Mainstream PC Gamers?",
            "Why Do Modern Flagships Cost So Much?",
            "Can You Actually Run Windows 11 on a Raspberry Pi?",
            "Is Cloud Gaming Dead in 2026?",
            "What Really Happens When You Delete a File?",
        ],
        "number": [
            "Top 10 Tech Gadgets You Need to See in 2026",
            "I Tried Living Without a Smartphone for 7 Days",
            "Building a $5000 Ultimate Clean Desk Setup",
            "5 Critical Coding Mistakes Every Junior Developer Makes",
            "The 3 Biggest Hardware Security Flaws Ever Found",
            "I Tested 10 Mechanical Keyboards So You Don't Have To",
            "Upgrading My Main Workstation With 64GB of DDR5 RAM",
            "Top 5 Open Source Developer Tools That Save 10 Hours a Week",
            "We Built a Stealth PC Inside a 500 Dollar Retro Console",
            "10 Command Line Utilities Every Software Engineer Should Know",
            "I Replaced My Entire Laptop Workflow with 1 Tablet",
            "The 7 Levels of Python Programming Mastery",
            "I Built an Automated Home Server for Under $200",
            "Top 8 Browser Extensions for Maximum Privacy and Security",
            "Why 99% of Wireless ANC Earbuds Sound the Same",
            "Running 100 Isolated Virtual Machines on a Single Server",
        ],
        "plain": [
            "The Complete Guide to Modern Computer Architecture",
            "Deep Dive Into Modern Cloud Infrastructure and Kubernetes",
            "How Modern Microprocessors Handle Dynamic Branch Prediction",
            "The Evolution of High Performance Solid State Storage",
            "Reviewing the Ultimate Wireless Noise Canceling Headphones",
            "Clean Code Architecture and Scalable Software Design Patterns",
            "Understanding Graphics Pipelines and Realtime Ray Tracing",
            "The Engineering Marvel of Modern Semiconductor Fabrication",
            "Mastering Vim and Keyboard Driven Terminal Productivity",
            "Designing High Availability Systems and Fault Tolerance",
            "The True Cost of Technical Debt in Large Enterprise Codebases",
            "Inside the Precision Engineering of Custom Key Switches",
            "Optimizing Database Query Execution Plans and B-Tree Indexes",
            "The Philosophy and Future of Open Source Collaboration",
        ],
    },
    "education": {
        "question": [
            "Why Do We Dream When We Sleep?",
            "What Actually Happens at the Event Horizon of a Black Hole?",
            "Can Humans Ever Travel Faster Than the Speed of Light?",
            "How Did the Roman Republic Really Fall?",
            "What If Earth Stopped Orbiting the Sun for 5 Seconds?",
            "Why Is Time Only Able to Move in One Direction?",
            "Could Life Exist in the Liquid Oceans of Europa?",
            "How Does the Human Brain Store Long-Term Memories?",
            "Why Do We Feel Nostalgia for Times We Never Lived In?",
            "What If All Insects Disappeared From Earth Tomorrow?",
        ],
        "number": [
            "7 Unsolved Physics Paradoxes That Confuse Scientists",
            "The 10 Greatest Archaeological Discoveries in Human History",
            "How 3 Strategic Decisions Shaped the Entire Modern World Map",
            "5 Extinct Prehistoric Species Geneticists Could Revive",
            "10 Mind Bending Facts About the Scale of the Observable Universe",
            "The 4 Great Classical Philosophies That Changed Human Thought",
            "How 1 Small Mutation Created Human Civilization",
            "6 Optical Illusions That Expose Flaws in Human Perception",
            "The 100 Year Mystery of Deep Sea Bioluminescence",
            "8 Ancient Engineering Feats Historians Still Cannot Explain",
            "Why 95% of Deep Oceanic Trenches Remain Completely Unexplored",
            "3 Natural Catastrophes That Nearly Wiped Out Humanity",
            "The 12 Crucial Turning Points in Biological Evolution",
            "How 2 Rival Inventors Revolutionized Modern Electricity",
            "5 Cognitive Biases That Affect Every Decision You Make",
            "The 500 Year History of the Global Financial Banking System",
        ],
        "plain": [
            "The Mystery of Dark Matter and Cosmic Expansion Explained",
            "How Specialized Antibodies Defend Against Mutating Pathogens",
            "Understanding Central Bank Monetary Policy and Global Inflation",
            "The Dynamic Geochemical Evolution of Earth Atmosphere",
            "Philosophical Perspectives on Consciousness and Free Will",
            "The Rise and Fall of the Bronze Age Trade Networks",
            "Quantum Superposition and the Measurement Problem",
            "How the Renaissance Redefined Global Art and Scientific Inquiry",
            "Plate Tectonics and the Formation of Supercontinents",
            "The Neuroscience of Decision Making and Habit Formation",
            "Comparative Linguistics and the Origin of Human Speech",
            "Ecological Trophic Cascades in Forest Conservation",
            "The Mathematical Elegance of Prime Number Distribution",
            "Understanding Gravitational Waves and Spacetime Curvature",
        ],
    },
    "gaming": {
        "question": [
            "Can You Beat Minecraft Without Ever Jumping?",
            "Why Did Everyone Suddenly Stop Playing This Game?",
            "Is This the Most Unfair Boss Fight in Souls History?",
            "What Happens If You Reach the Maximum Level Cap?",
            "Can You Survive 100 Days in Ultra Hardcore Survival?",
            "Is This Overlooked Indie Gem the Game of the Year?",
            "Why Are Modern Video Games Taking 7 Years to Make?",
            "Can an AI Outplay the Best Esports Champions in the World?",
            "What Made Classic Game Audio Design So Unforgettable?",
            "Did Developers Intentionally Leave This Famous Glitch In?",
        ],
        "number": [
            "10 Hidden Easter Eggs Developers Thought Nobody Would Find",
            "Top 5 World Record Speedrun Tricks That Broke the Game",
            "I Spent 50 Straight Hours Mining for Rare End-Game Resources",
            "The Top 7 Most Overpowered Weapons in Multiplayer History",
            "Ranking All 12 Final Bosses From Easiest to Impossible",
            "We Tested 20 Crazy Gaming Myths to See Which Ones Are Real",
            "Surviving Against 100 Hunters in an Endless Open World",
            "The 5 Most Infamous Video Game Bugs Ever Shipped to Retail",
            "Top 10 RPG Mechanics That Every Game Should Steal",
            "I Survived 30 Days on an Isolated Desert Island Map",
            "The 4 Best Level Design Decisions in Platformer History",
            "Ranking the 15 Greatest Soundtracks in Video Game Lore",
            "I Built a Massive Automated Kingdom in Under 24 Hours",
            "Top 8 Tactical Mistakes That Cause Players to Lose Every Round",
            "How 1 Player Singlehandedly Saved an Abandoned MMO Server",
            "The 5 Worst Video Game Launch Disasters of All Time",
        ],
        "plain": [
            "The Complete Retrospective on Open World Game Design",
            "Grand Finals World Championship Extended Highlights",
            "Mastering Movement Mechanics and Crosshair Placement",
            "The Rise and Collapse of Sandbox Survival Multiplayers",
            "Exploring Forgotten Beta Content and Unused Assets",
            "The Brilliant Psychology of Effective Level Pacing",
            "Why Classic Low Poly Graphics Are Making a Major Comeback",
            "Analyzing Environmental Storytelling in Post Apocalyptic Games",
            "The Secret History Behind Iconic Video Game Sound Effects",
            "Speedrun Mechanics and Pixel Perfect Collision Detection",
            "How Procedural Generation Redefined Modern Rogue-Lites",
            "The Art and Challenge of Writing Compelling Dialogue Trees",
            "Crafting Memorable Boss Encounters Through Audio Cues",
            "The Evolution of Combat Mechanics in Narrative Action RPGs",
        ],
    },
    "music": {
        "question": [
            "Is This the Greatest Recorded Guitar Solo in Rock History?",
            "Why Do Minor Chords Evoke Such Profound Sadness?",
            "Can You Recognize These Iconic Songs From Just One Note?",
            "How Did This Low Budget Track Hit 1 Billion Global Streams?",
            "Is Vintage 80s Analog Synthpop Making a Permanent Return?",
            "Why Does Nobody Write Bridge Sections in Modern Pop Anymore?",
            "Can Proper Music Theory Actually Make You a Better Producer?",
            "What Makes That Famous Drum Break So Addictive to Sample?",
            "Is Perfect Pitch a Natural Gift or a Trainable Skill?",
            "Why Did Vinyl Records Outsell CDs for the First Time in Decades?",
        ],
        "number": [
            "Top 10 Most Streamed Tracks in Streaming History",
            "5 Universal Chord Progressions Found in Almost Every Hit Song",
            "Ranking All 9 Studio Albums From Absolute Worst to Best",
            "The 100 Million Dollar Legal Lawsuit That Altered Copyright Law",
            "7 Production Techniques That Give Vocals That Radio Polish",
            "Top 4 Mixing Mistakes That Muddle Up Your Low End Frequencies",
            "We Tried Writing a Chart Topping Pop Song in Under 60 Minutes",
            "The 3 Essential Synthesizers That Defined Electronic Music",
            "Top 10 Greatest Live Concert Encores Caught on Camera",
            "I Practiced Complex Jazz Drum Rudiments for 30 Straight Days",
            "5 Timeless Guitar Pedals That Every Musician Should Own",
            "Ranking the Top 8 Most Difficult Piano Concertos of All Time",
            "How 2 Producers Created the Most Influential Sample Pack",
            "The 12 Most Expensive Rare Vinyl Records Ever Sold at Auction",
            "6 Secret Vocal Effects Behind Iconic Vocal Performances",
            "Top 5 Modular Synthesizer Patches That Sound Like the Future",
        ],
        "plain": [
            "Official Studio Music Video – Acoustic Sunset Session",
            "Midnight Symphony Orchestra World Premiere Performance",
            "Live From the Royal Albert Hall – Full Orchestral Set",
            "Deep Dive Into Harmonic Modulation and Counterpoint",
            "Electric Guitar Tone Shaping and Dynamic Range Masterclass",
            "The Evolution of Lo-Fi Production and Tape Saturation",
            "Vocal Harmonization and Microtonal Tuning Techniques",
            "The Cultural Heritage and Rhythmic Roots of Afrobeat",
            "Mastering Audio Compression and Dynamic Envelope Control",
            "The Architecture of Vintage Analog Recording Studios",
            "Exploring Modal Jazz Improvisation and Melodic Phrasing",
            "The Art of Subtle Foley and Atmospheric Texture in Composition",
            "Deconstructing Ambient Soundscapes and Spatial Reverb",
            "The Golden Era of Analog Tape Recording and Mixing Consoles",
        ],
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ══════════════════════════════════════════════════════════════════════════════

def _rand_id(length: int = 11) -> str:
    """Generate a random YouTube-style alphanumeric ID."""
    chars = string.ascii_letters + string.digits + "_-"
    return "".join(random.choices(chars, k=length))


def _iso8601_duration(total_seconds: int) -> str:
    """Convert total_seconds to ISO 8601 duration string (e.g. PT1H5M30S)."""
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    parts = "PT"
    if h:
        parts += f"{h}H"
    if m:
        parts += f"{m}M"
    if s or (not h and not m):
        parts += f"{s}S"
    return parts


def _fmt_dt(dt: datetime) -> str:
    """Format a datetime as a YouTube API RFC-3339 string."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ══════════════════════════════════════════════════════════════════════════════
# Channel Resource Builder
# ══════════════════════════════════════════════════════════════════════════════

def build_channel_resource(row: dict, snapshot_date: datetime, seed_offset: int) -> dict:
    """
    Build a YouTube Data API v3 channel resource for one channel.
    Metrics grow monotonically across snapshot dates (seed_offset = 0..6).
    """
    cid = row["channel_id"]
    prof = CHANNEL_PROFILES.get(cid, {
        "base_subs": 10_000_000, "daily_subs": 10_000,
        "base_views": 1_000_000_000, "daily_views": 1_000_000,
        "video_count": 500,
    })

    # Monotonic progression: base + seed_offset * daily_gain
    subs = prof["base_subs"] + seed_offset * prof["daily_subs"] + (seed_offset * 111)
    views = prof["base_views"] + seed_offset * prof["daily_views"] + (seed_offset * 3333)
    video_count = prof["video_count"] + (1 if seed_offset >= 4 else 0)

    upload_playlist = "UU" + cid[2:]
    published = datetime(2012, 5, 10, tzinfo=timezone.utc)

    return {
        "kind": "youtube#channel",
        "etag": _rand_id(27),
        "id": cid,
        "snippet": {
            "title": row["channel_name"],
            "description": f"Official YouTube channel of {row['channel_name']}.",
            "customUrl": "@" + row["channel_name"].lower().replace(" ", "").replace("–", ""),
            "publishedAt": _fmt_dt(published),
            "thumbnails": {
                "default": {"url": f"https://i.ytimg.com/channel/{cid}/default.jpg"},
                "medium":  {"url": f"https://i.ytimg.com/channel/{cid}/mqdefault.jpg"},
                "high":    {"url": f"https://i.ytimg.com/channel/{cid}/hqdefault.jpg"},
            },
            "country": row["country"],
        },
        "contentDetails": {
            "relatedPlaylists": {
                "likes": "",
                "uploads": upload_playlist,
            }
        },
        "statistics": {
            "viewCount": str(views),
            "subscriberCount": str(subs),
            "hiddenSubscriberCount": False,
            "videoCount": str(video_count),
        },
        "topicDetails": {
            "topicIds": ["/m/07c1v"],
            "topicCategories": [f"https://en.wikipedia.org/wiki/{row['niche'].capitalize()}"],
        },
    }


def build_channels_response(rows: list[dict], snapshot_date: datetime, seed_offset: int) -> dict:
    """Wrap channel resources in a ListResponse envelope."""
    items = [build_channel_resource(r, snapshot_date, seed_offset) for r in rows]
    return {
        "kind": "youtube#channelListResponse",
        "etag": _rand_id(27),
        "pageInfo": {"totalResults": len(items), "resultsPerPage": 50},
        "items": items,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Video Registry & Pool
# ══════════════════════════════════════════════════════════════════════════════

# Official YouTube categories
_NICHE_CATEGORIES = {
    "tech": [("28", "Science & Technology"), ("27", "Education")],
    "gaming": [("20", "Gaming"), ("24", "Entertainment")],
    "education": [("27", "Education"), ("28", "Science & Technology"), ("22", "People & Blogs")],
    "music": [("10", "Music"), ("24", "Entertainment")],
}

_NICHE_TAGS = {
    "tech": ["technology", "review", "gadgets", "programming", "hardware", "setup"],
    "gaming": ["gaming", "gameplay", "walkthrough", "speedrun", "highlights", "multiplayer"],
    "education": ["education", "science", "history", "learning", "explained", "documentary"],
    "music": ["music", "acoustic", "official video", "live", "performance", "guitar"],
}


def build_video_registry(rows: list[dict], anchor_date: datetime) -> dict[str, dict]:
    """
    Generate static metadata and baseline properties for all videos.
    This guarantees that video titles, durations, publish dates, and baseline
    views remain consistent across all 7 snapshot days.
    """
    registry: dict[str, dict] = {}
    rng = random.Random(42)

    for row in rows:
        cid = row["channel_id"]
        c_title = row["channel_name"]
        niche = row["niche"]

        titles_pool = NICHE_TITLES.get(niche, NICHE_TITLES["tech"])
        q_titles = titles_pool["question"]    # 10
        num_titles = titles_pool["number"]    # 16
        plain_titles = titles_pool["plain"]  # 14

        for pos in range(VIDEOS_PER_CHANNEL):
            vid = _rand_id(11)

            # Determine title: 10 questions (~25%), 16 numbers (~40%), 14 plain (~35%)
            if pos < 10:
                title = q_titles[pos % len(q_titles)]
                has_q = True
                has_num = any(ch.isdigit() for ch in title)
            elif pos < 26:
                title = num_titles[(pos - 10) % len(num_titles)]
                has_q = False
                has_num = True
            else:
                title = plain_titles[(pos - 26) % len(plain_titles)]
                has_q = False
                has_num = False

            # Duration distribution: 10 short (<4m), 20 medium (4-20m), 10 long (>20m)
            if pos % 4 == 0:
                dur_secs = rng.randint(45, 230)            # Short
                dur_band = "short"
            elif pos % 4 in (1, 2):
                dur_secs = rng.randint(280, 1150)          # Medium
                dur_band = "medium"
            else:
                dur_secs = rng.randint(1300, 4800)         # Long
                dur_band = "long"

            # Publish date: some recent (1-7 days before anchor), most older (1-6 years ago)
            if pos < 4:
                pub_days_ago = pos + 1
            else:
                pub_days_ago = rng.randint(20, 2200)
            published_at = anchor_date - timedelta(days=pub_days_ago, hours=rng.randint(1, 23), minutes=rng.randint(0, 59))

            # Base views calculation (subtle plausible effects)
            niche_base = {
                "music": rng.randint(15_000_000, 35_000_000),
                "gaming": rng.randint(6_000_000, 20_000_000),
                "tech": rng.randint(4_000_000, 16_000_000),
                "education": rng.randint(3_000_000, 14_000_000),
            }.get(niche, 5_000_000)

            dur_mult = {"short": 0.85, "medium": 1.15, "long": 0.95}[dur_band]
            title_mult = 1.12 if has_num else (1.05 if has_q else 1.0)
            base_views = int(niche_base * dur_mult * title_mult)

            # Engagement per video = base value for duration band + normal noise (std dev ~1 percentage point), clipped at 0
            base_eng = {"short": 0.095, "medium": 0.075, "long": 0.055}[dur_band]
            noise = rng.gauss(0.0, 0.01)  # std dev ~1 percentage point (0.01)
            eng_rate = max(0.0, base_eng + noise)

            like_rate = eng_rate * 0.88
            comment_rate = eng_rate * 0.12

            cat_id, cat_name = rng.choice(_NICHE_CATEGORIES.get(niche, [("28", "Science & Technology")]))
            tags = rng.sample(_NICHE_TAGS.get(niche, ["video"]), k=min(4, len(_NICHE_TAGS.get(niche, []))))

            registry[vid] = {
                "video_id": vid,
                "channel_id": cid,
                "channel_title": c_title,
                "niche": niche,
                "title": title,
                "position": pos,
                "has_question": has_q,
                "has_number": has_num,
                "duration_seconds": dur_secs,
                "duration_iso": _iso8601_duration(dur_secs),
                "duration_band": dur_band,
                "published_at": published_at,
                "base_views": base_views,
                "like_rate": like_rate,
                "comment_rate": comment_rate,
                "category_id": cat_id,
                "category_name": cat_name,
                "tags": tags,
                "likes_hidden": rng.random() < HIDDEN_COUNTS_PROB,
                "comments_hidden": rng.random() < HIDDEN_COUNTS_PROB,
            }

    return registry


def build_video_id_pool(rows: list[dict], registry: dict[str, dict]) -> dict[str, list[str]]:
    """Group video IDs by channel ID."""
    pool: dict[str, list[str]] = {r["channel_id"]: [] for r in rows}
    for vid, meta in registry.items():
        pool[meta["channel_id"]].append(vid)
    return pool


# ══════════════════════════════════════════════════════════════════════════════
# PlaylistItem Builder
# ══════════════════════════════════════════════════════════════════════════════

def build_playlist_item(
    video_id: str,
    meta: dict,
    is_deleted: bool = False,
) -> dict:
    """Build one playlistItem resource in YouTube API v3 shape."""
    cid = meta["channel_id"]
    upload_playlist = "UU" + cid[2:]
    playlist_item_id = _rand_id(34)

    if is_deleted:
        return {
            "kind": "youtube#playlistItem",
            "etag": _rand_id(27),
            "id": playlist_item_id,
            "snippet": {
                "publishedAt": _fmt_dt(meta["published_at"]),
                "channelId": cid,
                "title": "Deleted video",
                "description": "",
                "channelTitle": meta["channel_title"],
                "playlistId": upload_playlist,
                "position": meta["position"],
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
                "videoOwnerChannelTitle": meta["channel_title"],
                "videoOwnerChannelId": cid,
                "thumbnails": {},
            },
            "status": {"privacyStatus": "private"},
            "contentDetails": {
                "videoId": video_id,
                "videoPublishedAt": _fmt_dt(meta["published_at"]),
                "endAt": None, "startAt": None, "note": None,
            },
        }

    return {
        "kind": "youtube#playlistItem",
        "etag": _rand_id(27),
        "id": playlist_item_id,
        "snippet": {
            "publishedAt": _fmt_dt(meta["published_at"]),
            "channelId": cid,
            "title": meta["title"],
            "description": f"Watch more from {meta['channel_title']}. Tags: {', '.join(meta['tags'])}.",
            "channelTitle": meta["channel_title"],
            "playlistId": upload_playlist,
            "position": meta["position"],
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
            "videoOwnerChannelTitle": meta["channel_title"],
            "videoOwnerChannelId": cid,
            "thumbnails": {
                "default": {"url": f"https://i.ytimg.com/vi/{video_id}/default.jpg", "width": 120, "height": 90},
                "medium":  {"url": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg", "width": 320, "height": 180},
                "high":    {"url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg", "width": 480, "height": 360},
            },
        },
        "status": {"privacyStatus": "public"},
        "contentDetails": {
            "videoId": video_id,
            "videoPublishedAt": _fmt_dt(meta["published_at"]),
            "endAt": None, "startAt": None, "note": None,
        },
    }


def build_playlist_items_response(
    rows: list[dict],
    video_pool: dict[str, list[str]],
    registry: dict[str, dict],
    snapshot_date: datetime,
    deleted_ids: set[str],
) -> dict:
    """Build playlistItems ListResponse covering all channels."""
    items: list[dict] = []
    for row in rows:
        cid = row["channel_id"]
        for vid in video_pool[cid]:
            meta = registry[vid]
            is_deleted = vid in deleted_ids
            items.append(build_playlist_item(vid, meta, is_deleted=is_deleted))

    return {
        "kind": "youtube#playlistItemListResponse",
        "etag": _rand_id(27),
        "pageInfo": {"totalResults": len(items), "resultsPerPage": 50},
        "items": items,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Video Resource Builder (Monotonic growth across snapshots)
# ══════════════════════════════════════════════════════════════════════════════

def build_video_resource(
    video_id: str,
    meta: dict,
    snapshot_date: datetime,
    seed_offset: int,
    is_zero_view: bool = False,
    is_deleted: bool = False,
) -> dict:
    """
    Build one video resource.
    Ensures non-decreasing views, likes, and comments across snapshots.
    """
    if is_deleted:
        statistics = {}
    elif is_zero_view:
        statistics = {
            "viewCount": "0",
            "likeCount": "0",
            "favoriteCount": "0",
            "commentCount": "0",
        }
    else:
        # Calculate monotonic view growth
        # Age at snapshot date
        age_days = max(1, (snapshot_date - meta["published_at"]).days)

        # Base view progression across snapshot days (seed_offset = 0..6)
        # First 7 days post-publication experience higher gain rates
        daily_growth_sum = 0
        for step in range(1, seed_offset + 1):
            step_age = age_days - (seed_offset - step)
            if step_age <= 7:
                daily_pct = 0.05 + 0.01 * max(1, (8 - step_age))
            else:
                daily_pct = 0.001
            daily_growth_sum += int(meta["base_views"] * daily_pct)

        views = meta["base_views"] + daily_growth_sum
        likes = int(views * meta["like_rate"])
        comments = int(views * meta["comment_rate"])

        statistics = {
            "viewCount": str(views),
            "favoriteCount": "0",
        }
        if not meta["likes_hidden"]:
            statistics["likeCount"] = str(likes)
        if not meta["comments_hidden"]:
            statistics["commentCount"] = str(comments)

    snippet = {
        "publishedAt": _fmt_dt(meta["published_at"]),
        "channelId": meta["channel_id"],
        "title": "This video is unavailable" if is_deleted else meta["title"],
        "description": "" if is_deleted else f"Watch more from {meta['channel_title']}. Tags: {', '.join(meta['tags'])}.",
        "thumbnails": {} if is_deleted else {
            "default": {"url": f"https://i.ytimg.com/vi/{video_id}/default.jpg", "width": 120, "height": 90},
            "medium":  {"url": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg", "width": 320, "height": 180},
            "high":    {"url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg", "width": 480, "height": 360},
        },
        "channelTitle": meta["channel_title"],
        "tags": [] if is_deleted else meta["tags"],
        "categoryId": meta["category_id"],
        "liveBroadcastContent": "none",
        "defaultLanguage": "en",
        "localized": {
            "title": "This video is unavailable" if is_deleted else meta["title"],
            "description": "",
        },
    }

    content_details = {
        "duration": "PT0S" if is_deleted else meta["duration_iso"],
        "dimension": "2d",
        "definition": "hd",
        "caption": "false",
        "licensedContent": True,
        "contentRating": {},
        "projection": "rectangular",
    }

    return {
        "kind": "youtube#video",
        "etag": _rand_id(27),
        "id": video_id,
        "snippet": snippet,
        "contentDetails": content_details,
        "statistics": statistics,
        "topicDetails": {
            "topicCategories": [
                f"https://en.wikipedia.org/wiki/{meta['category_name'].replace(' ', '_')}"
            ]
        },
    }


def build_videos_response(
    rows: list[dict],
    video_pool: dict[str, list[str]],
    registry: dict[str, dict],
    snapshot_date: datetime,
    seed_offset: int,
    deleted_ids: set[str],
    zero_view_ids: set[str],
    duplicate_ids: list[str],
) -> dict:
    """Build videos ListResponse for all channels with messy edge cases injected."""
    items: list[dict] = []
    seen_ids: set[str] = set()

    for row in rows:
        cid = row["channel_id"]
        for vid in video_pool[cid]:
            meta = registry[vid]
            is_deleted = vid in deleted_ids
            is_zero = vid in zero_view_ids

            res = build_video_resource(
                video_id=vid,
                meta=meta,
                snapshot_date=snapshot_date,
                seed_offset=seed_offset,
                is_zero_view=is_zero,
                is_deleted=is_deleted,
            )
            items.append(res)
            seen_ids.add(vid)

    # Inject duplicate entries (same video_id with slightly altered metrics)
    dup_candidates = [i for i in seen_ids if i in duplicate_ids]
    for dup_id in dup_candidates:
        meta = registry[dup_id]
        dup_res = build_video_resource(
            video_id=dup_id,
            meta=meta,
            snapshot_date=snapshot_date,
            seed_offset=seed_offset + 1,
            is_zero_view=False,
            is_deleted=False,
        )
        items.append(dup_res)

    return {
        "kind": "youtube#videoListResponse",
        "etag": _rand_id(27),
        "pageInfo": {"totalResults": len(items), "resultsPerPage": 50},
        "items": items,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Snapshot Writer
# ══════════════════════════════════════════════════════════════════════════════

def write_json(path: Path, data: dict) -> None:
    """Serialise data to path as pretty-printed JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    print(f"  Wrote {path.relative_to(path.parent.parent.parent)} ({path.stat().st_size:,} bytes)")


def generate_snapshot(
    rows: list[dict],
    video_pool: dict[str, list[str]],
    registry: dict[str, dict],
    snapshot_date: datetime,
    seed_offset: int,
    deleted_ids: set[str],
    zero_view_ids: set[str],
    duplicate_ids: list[str],
) -> None:
    """Generate and write the three JSON files for a single snapshot date."""
    date_str = snapshot_date.strftime(DATE_FMT)
    print(f"\n>> Snapshot {date_str} (day index = {seed_offset})")

    # 1. Channels
    ch_resp = build_channels_response(rows, snapshot_date, seed_offset)
    write_json(RAW_DIR / f"channels_{date_str}.json", ch_resp)

    # 2. Playlist items
    pi_resp = build_playlist_items_response(rows, video_pool, registry, snapshot_date, deleted_ids)
    write_json(RAW_DIR / f"playlist_items_{date_str}.json", pi_resp)

    # 3. Videos
    vid_resp = build_videos_response(
        rows, video_pool, registry, snapshot_date, seed_offset,
        deleted_ids, zero_view_ids, duplicate_ids,
    )
    write_json(RAW_DIR / f"videos_{date_str}.json", vid_resp)


# ══════════════════════════════════════════════════════════════════════════════
# Entry Point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    print("-" * 60)
    print("YouTube Warehouse - Sample Data Generator")
    print("-" * 60)

    # ── 0. Clean old raw JSON files to prevent leftover dates ──────────────────
    old_files = list(RAW_DIR.glob("*.json"))
    if old_files:
        print(f"Clearing {len(old_files)} previous raw JSON files from raw/ ...")
        for f in old_files:
            try:
                f.unlink()
            except OSError:
                pass

    # ── 1. Read channel list ──────────────────────────────────────────────────
    with CHANNELS_CSV.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    print(f"\nLoaded {len(rows)} channels from {CHANNELS_CSV.name}")

    # ── 2. Fixed Snapshot Window (Exactly 7 consecutive days) ─────────────────
    # Anchoring to 2026-10-16 ensures reproducible dates (2026-10-10 to 2026-10-16)
    today = datetime(2026, 10, 16, tzinfo=timezone.utc)

    # ── 3. Build Video Registry & Pool ────────────────────────────────────────
    registry = build_video_registry(rows, anchor_date=today)
    video_pool = build_video_id_pool(rows, registry)
    all_ids = list(registry.keys())
    total_ids = len(all_ids)

    # ── 4. Pick Special-Case Video IDs ────────────────────────────────────────
    deleted_ids = set(random.sample(all_ids, DELETED_VIDEO_COUNT))
    remaining = [v for v in all_ids if v not in deleted_ids]
    zero_view_ids = set(random.sample(remaining, ZERO_VIEW_COUNT))
    normal = [v for v in remaining if v not in zero_view_ids]
    duplicate_ids = random.sample(normal, DUPLICATE_COUNT)

    print(f"Total unique video IDs  : {total_ids}")
    print(f"Deleted video IDs       : {sorted(deleted_ids)}")
    print(f"Zero-view video IDs     : {sorted(zero_view_ids)}")
    print(f"Duplicate injection IDs : {duplicate_ids}")

    # Check title distributions
    q_count = sum(1 for m in registry.values() if m["has_question"])
    num_count = sum(1 for m in registry.values() if m["has_number"])
    plain_count = sum(1 for m in registry.values() if not m["has_question"] and not m["has_number"])
    print(f"Title Distribution      : Questions={q_count} ({q_count/total_ids:.1%}), "
          f"Numbers={num_count} ({num_count/total_ids:.1%}), Plain={plain_count} ({plain_count/total_ids:.1%})")

    # ── 5. Generate SNAPSHOT_DAYS (7) daily snapshots ─────────────────────────
    for day_offset in range(SNAPSHOT_DAYS - 1, -1, -1):  # oldest (10-10) -> newest (10-16)
        snapshot_date = today - timedelta(days=day_offset)
        seed_offset = SNAPSHOT_DAYS - 1 - day_offset     # 0 for oldest, 6 for today

        generate_snapshot(
            rows=rows,
            video_pool=video_pool,
            registry=registry,
            snapshot_date=snapshot_date,
            seed_offset=seed_offset,
            deleted_ids=deleted_ids,
            zero_view_ids=zero_view_ids,
            duplicate_ids=duplicate_ids,
        )

    # ── Summary ───────────────────────────────────────────────────────────────
    raw_files = sorted(RAW_DIR.glob("*.json"))
    print(f"\n{'─' * 60}")
    print(f"Done! {len(raw_files)} files written to raw/ (Exactly {SNAPSHOT_DAYS} snapshot dates)")
    print(f"{'─' * 60}")
    for f in raw_files:
        print(f"  {f.name:45s}  {f.stat().st_size:>10,} bytes")


if __name__ == "__main__":
    main()
