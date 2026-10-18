-- ==============================================================================
-- Streaming Platform Content & Engagement Warehouse
-- Schema DDL (Star Schema Definition)
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- Dimension Tables
-- ------------------------------------------------------------------------------

-- Date Dimension: Full calendar spine
CREATE TABLE IF NOT EXISTS dim_date (
    date_key        INTEGER PRIMARY KEY, -- YYYYMMDD integer
    full_date       TEXT NOT NULL,       -- ISO Date YYYY-MM-DD
    day             INTEGER,
    month           INTEGER,
    month_name      TEXT,
    quarter         INTEGER,
    year            INTEGER,
    weekday         INTEGER,             -- 0 = Monday, 6 = Sunday
    weekday_name    TEXT,
    is_weekend      BOOLEAN
);

-- Time Dimension: 24-hour lookup
CREATE TABLE IF NOT EXISTS dim_time (
    time_key        INTEGER PRIMARY KEY, -- 0-23
    hour            INTEGER NOT NULL,
    time_band       TEXT                 -- Morning, Afternoon, Evening, Night
);

-- Channel Dimension: Creator / channel metadata
CREATE TABLE IF NOT EXISTS dim_channel (
    channel_key     INTEGER PRIMARY KEY, -- Surrogate Key
    channel_id      TEXT UNIQUE NOT NULL, -- YouTube channel ID (UC...)
    channel_name    TEXT NOT NULL,
    country         TEXT,
    created_date    TEXT,
    niche           TEXT                 -- tech, education, gaming, music
);

-- Category Dimension: Official YouTube category mapping
CREATE TABLE IF NOT EXISTS dim_category (
    category_key    INTEGER PRIMARY KEY, -- Surrogate Key
    category_id     TEXT UNIQUE NOT NULL, -- YouTube numeric string ID
    category_name   TEXT NOT NULL
);

-- Video Dimension: Content metadata & derived text features
CREATE TABLE IF NOT EXISTS dim_video (
    video_key           INTEGER PRIMARY KEY, -- Surrogate Key
    video_id            TEXT UNIQUE NOT NULL, -- 11-char YouTube video ID
    title               TEXT,
    duration_seconds    INTEGER,
    duration_band       TEXT,                 -- short (<5m), medium (5-20m), long (>20m)
    title_length        INTEGER,
    word_count          INTEGER,
    has_number_in_title BOOLEAN,
    has_question_mark   BOOLEAN,
    tags                TEXT
);

-- ------------------------------------------------------------------------------
-- Fact Tables
-- ------------------------------------------------------------------------------

-- Video Performance Fact: Grain is one row per clean video (latest snapshot)
CREATE TABLE IF NOT EXISTS fact_video_stats (
    video_key           INTEGER NOT NULL,
    channel_key         INTEGER NOT NULL,
    category_key        INTEGER NOT NULL,
    publish_date_key    INTEGER NOT NULL,
    publish_hour_key    INTEGER NOT NULL,
    view_count          INTEGER,
    like_count          INTEGER,
    comment_count       INTEGER,
    engagement_rate     REAL, -- (likes + comments) / views
    FOREIGN KEY (video_key) REFERENCES dim_video(video_key),
    FOREIGN KEY (channel_key) REFERENCES dim_channel(channel_key),
    FOREIGN KEY (category_key) REFERENCES dim_category(category_key),
    FOREIGN KEY (publish_date_key) REFERENCES dim_date(date_key),
    FOREIGN KEY (publish_hour_key) REFERENCES dim_time(time_key)
);

-- Channel Daily Snapshot Fact: Grain is one row per channel per snapshot date
CREATE TABLE IF NOT EXISTS fact_channel_daily (
    channel_key         INTEGER NOT NULL,
    date_key            INTEGER NOT NULL,
    subscribers         INTEGER,
    total_views         INTEGER,
    video_count         INTEGER,
    PRIMARY KEY (channel_key, date_key),
    FOREIGN KEY (channel_key) REFERENCES dim_channel(channel_key),
    FOREIGN KEY (date_key) REFERENCES dim_date(date_key)
);

-- ------------------------------------------------------------------------------
-- Audit & Lineage Tables
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS etl_log (
    run_id              TEXT PRIMARY KEY,
    start_time          TEXT NOT NULL,
    end_time            TEXT,
    status              TEXT NOT NULL, -- RUNNING, SUCCESS, or FAILED
    source_name         TEXT,
    rows_extracted      INTEGER,
    rows_staged         INTEGER,
    rows_transformed    INTEGER,
    rows_loaded         INTEGER,
    rows_rejected       INTEGER,
    api_units_used      INTEGER,
    error_message       TEXT
);

CREATE TABLE IF NOT EXISTS dq_results (
    run_id              TEXT NOT NULL,
    check_name          TEXT NOT NULL,
    status              TEXT NOT NULL, -- PASS, FAIL or WARN
    details             TEXT,
    PRIMARY KEY (run_id, check_name)
);
