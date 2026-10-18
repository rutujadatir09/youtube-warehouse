-- ==============================================================================
-- Streaming Platform Content & Engagement Warehouse
-- Analytical SQL Queries (Answers to Core Business Questions)
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Which video categories and niches perform best? (tech, gaming, education, music)
-- Evaluates total reach (views), audience approval (likes), and community engagement rate.
-- ------------------------------------------------------------------------------
SELECT 
    dc.niche,
    dcat.category_name,
    COUNT(f.video_key) AS total_videos,
    SUM(f.view_count) AS total_views,
    ROUND(AVG(f.view_count), 0) AS avg_views,
    ROUND(AVG(f.like_count), 0) AS avg_likes,
    ROUND(AVG(f.comment_count), 0) AS avg_comments,
    ROUND(AVG(f.engagement_rate) * 100, 2) AS avg_engagement_rate_pct
FROM fact_video_stats f
JOIN dim_channel dc ON f.channel_key = dc.channel_key
JOIN dim_category dcat ON f.category_key = dcat.category_key
GROUP BY dc.niche, dcat.category_name
ORDER BY avg_views DESC;


-- ------------------------------------------------------------------------------
-- 2. Which channels grow fastest in subscribers and views over the snapshot period?
-- Computes absolute growth and percentage change from snapshot snapshots.
-- ------------------------------------------------------------------------------
SELECT 
    c.channel_name,
    c.niche,
    c.country,
    MIN(f.total_views) AS initial_views,
    MAX(f.total_views) AS latest_views,
    (MAX(f.total_views) - MIN(f.total_views)) AS view_growth,
    ROUND(((MAX(f.total_views) - MIN(f.total_views)) * 100.0 / NULLIF(MIN(f.total_views), 0)), 2) AS view_growth_pct,
    MIN(f.subscribers) AS initial_subscribers,
    MAX(f.subscribers) AS latest_subscribers,
    (MAX(f.subscribers) - MIN(f.subscribers)) AS subscriber_growth
FROM fact_channel_daily f
JOIN dim_channel c ON f.channel_key = c.channel_key
GROUP BY c.channel_key, c.channel_name, c.niche, c.country
ORDER BY view_growth DESC;


-- ------------------------------------------------------------------------------
-- 3. What video length, upload day, or upload hour gets the best engagement?
-- (a) By Duration Band (short, medium, long)
-- ------------------------------------------------------------------------------
SELECT 
    v.duration_band,
    COUNT(*) AS video_count,
    ROUND(AVG(f.view_count), 0) AS avg_views,
    ROUND(AVG(f.engagement_rate) * 100, 2) AS avg_engagement_rate_pct
FROM fact_video_stats f
JOIN dim_video v ON f.video_key = v.video_key
GROUP BY v.duration_band
ORDER BY avg_engagement_rate_pct DESC;

-- (b) By Upload Day of Week
SELECT 
    d.weekday_name,
    d.is_weekend,
    COUNT(*) AS video_count,
    ROUND(AVG(f.view_count), 0) AS avg_views,
    ROUND(AVG(f.engagement_rate) * 100, 2) AS avg_engagement_rate_pct
FROM fact_video_stats f
JOIN dim_date d ON f.publish_date_key = d.date_key
GROUP BY d.weekday_name, d.is_weekend, d.weekday
ORDER BY d.weekday ASC;

-- (c) By Upload Hour of Day (and Time Band)
SELECT 
    t.time_band,
    t.hour,
    COUNT(*) AS video_count,
    ROUND(AVG(f.view_count), 0) AS avg_views,
    ROUND(AVG(f.engagement_rate) * 100, 2) AS avg_engagement_rate_pct
FROM fact_video_stats f
JOIN dim_time t ON f.publish_hour_key = t.hour
GROUP BY t.time_band, t.hour
ORDER BY t.hour ASC;


-- ------------------------------------------------------------------------------
-- 4. Do titles with numbers, questions, or specific lengths get more views?
-- ------------------------------------------------------------------------------
SELECT 
    v.has_number_in_title,
    v.has_question_mark,
    COUNT(*) AS video_count,
    ROUND(AVG(f.view_count), 0) AS avg_views,
    ROUND(AVG(f.like_count), 0) AS avg_likes,
    ROUND(AVG(f.engagement_rate) * 100, 2) AS avg_engagement_rate_pct
FROM fact_video_stats f
JOIN dim_video v ON f.video_key = v.video_key
GROUP BY v.has_number_in_title, v.has_question_mark
ORDER BY avg_views DESC;


-- ------------------------------------------------------------------------------
-- 5. Top 10 Performing Videos Overall (Ranked by views with channel & category)
-- ------------------------------------------------------------------------------
SELECT 
    v.title,
    c.channel_name,
    c.niche,
    cat.category_name,
    f.view_count,
    f.like_count,
    ROUND(f.engagement_rate * 100, 2) AS engagement_rate_pct
FROM fact_video_stats f
JOIN dim_video v ON f.video_key = v.video_key
JOIN dim_channel c ON f.channel_key = c.channel_key
JOIN dim_category cat ON f.category_key = cat.category_key
ORDER BY f.view_count DESC
LIMIT 10;
