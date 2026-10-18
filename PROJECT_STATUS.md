# YouTube Content & Engagement Data Warehouse – Comprehensive Project Status & Analysis

**Project Name:** Streaming Platform Content & Engagement Warehouse (Digital World YouTube Analytics)  
**Current Date:** 2026-10-19  
**Overall Status:** **ETL Pipeline, Star Schema Warehouse, and Analytical Dashboard are 100% Complete & Verified.**  

---

## 1. Why the Subscriber Growth Chart Appears Visually "Flat"

In the analytical dashboard ([`dashboard/index.html`](dashboard/index.html) and [`docs/dashboard.png`](docs/dashboard.png)), the **Subscriber Growth per Channel** line chart displays lines that look virtually horizontal. 

### (a) The Math Behind the Flatness (Linear Scale Compression)
1. **The Extreme Outlier:**  
   The channel **T-Series** has **245,000,000 subscribers**. To display T-Series on a shared linear y-axis, the chart scale must span from **0 to 250,000,000 (250.0M)**.
2. **The Growth Magnitude:**  
   Over the 7 snapshot days, channels gain between **45,000 and 690,000 subscribers**:
   * **MrBeast Gaming** grew by **+690,666** subscribers (from 42.00M to 42.69M).
   * **Mark Rober** grew by **+510,666** subscribers (from 53.50M to 54.01M).
   * **T-Series** grew by **+420,666** subscribers (from 245.00M to 245.42M).
   * **Linus Tech Tips** grew by **+51,666** subscribers (from 15.60M to 15.65M).
3. **The Visual Ratio:**  
   $$\text{Visual Displacement of Fastest Growth} = \frac{690,666}{250,000,000} \approx \mathbf{0.276\%}$$
   On a standard desktop screen where the chart canvas is roughly 350 pixels tall:
   $$\text{Pixel Movement} = 350\text{px} \times 0.00276 \approx \mathbf{0.96\text{ pixels}}$$
   **Less than 1 single display pixel of upward movement occurs over the entire 7-day period.** 
   The underlying data in `fact_channel_daily` is **strictly monotonically increasing** every day, but a 0.27% increase against a 250M baseline cannot be seen by the human eye on a linear axis anchored at zero.

### (b) Baseline Grouping Effect
Channels at 15M to 18M subscribers (Linus Tech Tips, Marques Brownlee, CrashCourse) are clustered together near the bottom of the 250M scale, while T-Series floats at the very top.

### (c) Recommended Solutions (How to Make Growth Visually Dramatic)
If you would like the growth slopes to pop out visually, we can apply one of the following standard Data Warehouse / BI techniques:
1. **Plot Net Subscriber Growth ($\Delta$ Subscribers from Day 0):**  
   Start all channels at `0` on Day 1. The y-axis then spans `0 to +700,000`, making MrBeast Gaming's steep upward trajectory (+690k) and Mark Rober's steep curve (+510k) immediately visible in contrast to Linus Tech Tips (+51k).
2. **Plot Percentage Growth Rate (% $\Delta$):**  
   Track the relative growth percentage: MrBeast Gaming grew by +1.64%, Kurzgesagt by +0.60%, T-Series by +0.17%.
3. **Logarithmic Scale ($y$-axis):**  
   Compresses orders of magnitude so 15M and 250M can co-exist while amplifying fractional changes.

---

## 2. In-Depth Summary of What Has Been Done ✅

### Architectural Foundation
* **Star Schema Warehouse ([`warehouse.db`](warehouse.db), [`sql/schema_ddl.sql`](sql/schema_ddl.sql)):**
  * **Dimensions:** [`dim_channel`](sql/schema_ddl.sql#L12) (12 channels), [`dim_video`](sql/schema_ddl.sql#L33) (478 clean videos), [`dim_category`](sql/schema_ddl.sql#L23) (6 categories), [`dim_date`](sql/schema_ddl.sql#L54) (2,197 days), [`dim_time`](sql/schema_ddl.sql#L69) (24 hours).
  * **Facts:** [`fact_video_stats`](sql/schema_ddl.sql#L78) (478 video snapshot facts), [`fact_channel_daily`](sql/schema_ddl.sql#L94) (84 channel-day facts).
  * **Operational & Audit Tables:** `etl_log` (audit trail), `dq_results` (test validation results), `rejects` (quarantined data).

### Data Simulation & Ingestion
* **Sample Data Generator ([`src/make_sample_data.py`](src/make_sample_data.py)):**
  * Generates 7 consecutive daily snapshots (`2026-10-10` through `2026-10-16`) across 12 channels.
  * **Monotonic Channel Trajectories:** Pre-configured channel baselines with strictly positive daily increments.
  * **Monotonic Video Metrics:** Views, likes, and comments are non-decreasing across snapshots, with accelerated gains in the first 7 days post-publication.
  * **Realistic Titles (No `{channel} - Video {id}` templates):** 
    * **25.0% Question Titles** (`?`)
    * **45.6% Number Titles** (digits `\d`)
    * **35.0% Plain Titles**
  * **Plausible Correlation:** Medium-length videos (4–20 min) and titles with numbers yield higher average reach; question titles produce +20% higher comment and engagement rates.
  * **Messy Edge Cases Preserved:** 2 zero-view videos, 2 tombstoned deleted videos, 3 duplicate video IDs, and hidden likes/comments.
* **Extraction Pipeline ([`src/extract.py`](src/extract.py)):**
  * Supports YouTube Data API v3 and sample mode.
  * **Fixed Bug:** Removed redundant today-dated file creation in sample mode that previously added an 8th snapshot date (`2026-10-18`) and caused row-count inflation.

### Staging & Transformation Layer
* **Staging ([`src/stage.py`](src/stage.py)):**
  * Normalizes JSON into SQLite tables: `stg_channels` (84 rows), `stg_playlist_items` (480 rows), `stg_videos` (483 rows), `stg_video_snapshots` (3,381 rows).
  * Preserves metadata: `load_timestamp`, `source_name`, `source_file`, `snapshot_date`.
* **Transformation & Quality Cleaning ([`src/transform.py`](src/transform.py)):**
  * Parses ISO 8601 durations (`PT...`) into seconds and bins into `short`, `medium`, `long` duration bands.
  * Converts UTC timestamps to Indian Standard Time (IST).
  * NLP feature extraction: `title_length`, `word_count`, `has_number_in_title`, `has_question_mark`.
  * **Fixed Regex Bug:** Fixed `str.contains(r"\?", regex=False)` so question titles are correctly recognized as `1`.
  * Quarantined 5 rejected rows into `rejects` (3 duplicates deduplicated on latest timestamp + 2 private/deleted videos).

### Pipeline Orchestration & Testing
* **Pipeline Runner ([`src/run_pipeline.py`](src/run_pipeline.py)):**
  * End-to-end execution: `Extract -> Stage -> Transform -> Load -> Quality Checks`.
  * Comprehensive execution summary printed to terminal and logged into `etl_log`.
* **Data Quality Suite ([`src/quality_checks.py`](src/quality_checks.py)):**
  * **7 of 7 DQ Checks Passing**:
    1. `no_duplicate_video_id` (PASS)
    2. `no_null_surrogate_keys` (PASS)
    3. `no_negative_metrics` (PASS)
    4. `no_orphan_foreign_keys` (PASS)
    5. `row_count_reconciliation` (483 staged - 5 rejected = 478 in dim_video) (PASS)
    6. `engagement_rate_range` (PASS)
    7. `dim_date_coverage` (PASS)
* **Pytest Suite ([`tests/test_stage.py`](tests/test_stage.py)):**
  * **19 of 19 tests PASSING**.

### Analytical Business Queries
* **SQL Queries ([`sql/analysis_queries.sql`](sql/analysis_queries.sql)):**
  1. *Category & Niche Performance:* Music leads reach (~28M avg views); Education leads audience loyalty.
  2. *Fastest Growing Channels:* MrBeast Gaming (+690k subs) and Mark Rober (+510k subs) lead growth.
  3. *Duration & Upload Timing:* Medium duration (4–20 min) and evening upload bands perform best.
  4. *Title Patterns:* Question titles achieve higher engagement (7.59%–9.11%); number titles drive +10% higher view counts.
  5. *Top Performing Videos:* Ranked top 10 videos by engagement and views.

### Dashboard & Visual Delivery
* **Dashboard Generator ([`src/build_dashboard.py`](src/build_dashboard.py)):**
  * Builds self-contained [`dashboard/index.html`](dashboard/index.html) with embedded JSON and Chart.js v4.4.1 pinned.
  * Works locally via direct double-click (`file://` protocol).
  * Responsive, modern dark-slate interface.
  * **4 KPI Cards:** Total Videos (478), Total Views (8.00B), Total Channels (12), Avg Engagement Rate (6.84%).
  * **4 Charts (Zero Empty Charts):**
    1. *Subscriber Growth per Channel* (12 channels $\times$ 7 days).
    2. *Average Views by Video Category* (6 official categories).
    3. *Video Duration vs Engagement Rate* (scatter distribution).
    4. *Title Syntax Pattern* (all 4 bars populated: numbers, no numbers, questions, no questions).
* **Automated Screenshot:**
  * Captured using headless Chromium via Playwright: [`docs/dashboard.png`](docs/dashboard.png).

---

## 3. What is Left to Do ⏳

As per user instructions ("Do not write the README or report yet"), the remaining items for future steps are:

| Task / Milestone | Scope & Deliverables | Current Status |
| :--- | :--- | :---: |
| **Comprehensive `README.md`** | Complete repository guide: project overview, prerequisites, directory structure, step-by-step reproduction instructions, pipeline commands, star schema ER diagram, and query cookbook. | **Pending** (Held per instructions) |
| **Final Analytics Project Report** | Executive report covering: digital world warehouse design, answers to the 5 core business questions, data cleaning & reject reconciliation analysis, and strategic recommendations for digital creators. | **Pending** (Held per instructions) |
| *(Optional Dashboard Polish)* | Add a toggle or supplementary view for **Net Subscriber Gain ($\Delta$ Subscribers)** so channel growth curves appear steep and dynamic rather than visually compressed by T-Series' 245M baseline. | **Ready on request** |

---

## 4. Current File Map

```
DWBI/
├── data/
│   └── channels.csv                 # 12 channels across 4 niches
├── raw/                             # Exactly 21 raw JSON files (7 snapshot dates)
├── staging/                         # Normalised intermediate artifacts
├── sql/
│   ├── schema_ddl.sql               # Star schema DDL + ETL logging + DQ tables
│   └── analysis_queries.sql         # 5 core business questions
├── src/
│   ├── config.py                    # Central configuration paths
│   ├── make_sample_data.py          # Sample data generator (monotonic, NLP titles)
│   ├── extract.py                   # API / sample extractor (fixed duplicate date bug)
│   ├── stage.py                     # Raw JSON flattener & SQLite stager
│   ├── transform.py                 # Star schema builders, NLP regex, reject isolation
│   ├── load.py                      # Idempotent warehouse loader
│   ├── quality_checks.py            # Automated 7-point DQ test suite
│   ├── run_pipeline.py              # Master pipeline runner
│   └── build_dashboard.py           # Self-contained HTML dashboard builder
├── dashboard/
│   └── index.html                   # Interactive Chart.js analytics dashboard
├── docs/
│   └── dashboard.png                # Headless browser screenshot
├── tests/
│   └── test_stage.py                # 19 Pytest verification tests
├── warehouse.db                     # Populated SQLite Star Schema Data Warehouse
└── PROJECT_STATUS.md                # Comprehensive project status & analysis
```
