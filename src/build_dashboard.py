"""
build_dashboard.py
==================
Queries warehouse.db and generates a self-contained, responsive dashboard
in dashboard/index.html with data embedded as JSON.
Loads Chart.js v4.4.1 from cdnjs via UMD build (works on file:// protocol).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

# ── Ensure src/ is on path ───────────────────────────────────────────────────
SRC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_DIR))
from config import WAREHOUSE_DB, DASHBOARD_DIR

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def fetch_dashboard_data() -> dict:
    """Fetch all aggregated metrics and chart datasets from warehouse.db."""
    conn = sqlite3.connect(WAREHOUSE_DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. KPI Metrics
    kpi_row = cursor.execute("""
        SELECT 
            (SELECT COUNT(*) FROM dim_video) AS total_videos,
            (SELECT COALESCE(SUM(view_count), 0) FROM fact_video_stats) AS total_views,
            (SELECT COUNT(*) FROM dim_channel) AS total_channels,
            (SELECT COALESCE(ROUND(AVG(engagement_rate) * 100.0, 2), 0) FROM fact_video_stats) AS avg_engagement_rate;
    """).fetchone()

    kpis = {
        "total_videos": kpi_row["total_videos"],
        "total_views": kpi_row["total_views"],
        "total_channels": kpi_row["total_channels"],
        "avg_engagement_rate": kpi_row["avg_engagement_rate"],
    }

    # 2. Subscriber Growth per Channel (7-day snapshot trend)
    growth_rows = cursor.execute("""
        SELECT 
            c.channel_name,
            f.date_key,
            d.full_date,
            f.subscribers
        FROM fact_channel_daily f
        JOIN dim_channel c ON f.channel_key = c.channel_key
        JOIN dim_date d ON f.date_key = d.date_key
        ORDER BY f.date_key ASC, c.channel_name ASC;
    """).fetchall()

    # Group by channel
    dates_sorted = sorted(list({str(r["full_date"])[:10] for r in growth_rows}))
    channels_data = {}
    for r in growth_rows:
        c_name = r["channel_name"]
        d_str = str(r["full_date"])[:10]
        if c_name not in channels_data:
            channels_data[c_name] = {}
        channels_data[c_name][d_str] = r["subscribers"]

    # Calculate net new subscribers gained since day 1 (day 1 = 0 for every channel)
    channels_list = []
    for c_name in sorted(channels_data.keys()):
        base_subs = channels_data[c_name].get(dates_sorted[0], 0) if dates_sorted else 0
        net_values = [
            (channels_data[c_name][d] - base_subs) if d in channels_data[c_name] else None
            for d in dates_sorted
        ]
        channels_list.append({
            "name": c_name,
            "values": net_values,
        })

    subscriber_trends = {
        "labels": dates_sorted,
        "channels": channels_list,
    }

    # 3. Average Views by Category
    cat_rows = cursor.execute("""
        SELECT 
            c.category_name,
            ROUND(AVG(f.view_count), 0) AS avg_views,
            COUNT(*) AS video_count
        FROM fact_video_stats f
        JOIN dim_category c ON f.category_key = c.category_key
        GROUP BY c.category_name
        ORDER BY avg_views DESC;
    """).fetchall()

    category_stats = {
        "labels": [r["category_name"] for r in cat_rows],
        "avg_views": [int(r["avg_views"]) for r in cat_rows],
        "counts": [r["video_count"] for r in cat_rows],
    }

    # 4. Scatter: Duration (minutes) vs Engagement Rate (%)
    scatter_rows = cursor.execute("""
        SELECT 
            v.title,
            ROUND(v.duration_seconds / 60.0, 1) AS duration_minutes,
            ROUND(f.engagement_rate * 100.0, 2) AS engagement_rate,
            f.view_count,
            c.category_name
        FROM fact_video_stats f
        JOIN dim_video v ON f.video_key = v.video_key
        JOIN dim_category c ON f.category_key = c.category_key
        WHERE v.duration_seconds IS NOT NULL 
          AND f.engagement_rate IS NOT NULL
        ORDER BY v.duration_seconds ASC;
    """).fetchall()

    scatter_data = [
        {
            "x": r["duration_minutes"],
            "y": r["engagement_rate"],
            "title": r["title"][:40] + ("..." if len(r["title"]) > 40 else ""),
            "category": r["category_name"],
            "views": r["view_count"],
        }
        for r in scatter_rows
    ]

    # 5. Title Analysis: Numbers and Question Marks
    num_rows = cursor.execute("""
        SELECT 
            CASE WHEN has_number_in_title = 1 THEN 'Has Number' ELSE 'No Number' END AS variant,
            ROUND(AVG(f.view_count), 0) AS avg_views,
            COUNT(*) AS cnt
        FROM fact_video_stats f
        JOIN dim_video v ON f.video_key = v.video_key
        GROUP BY v.has_number_in_title
        ORDER BY v.has_number_in_title DESC;
    """).fetchall()

    q_rows = cursor.execute("""
        SELECT 
            CASE WHEN has_question_mark = 1 THEN 'Has Question (?)' ELSE 'No Question' END AS variant,
            ROUND(AVG(f.view_count), 0) AS avg_views,
            COUNT(*) AS cnt
        FROM fact_video_stats f
        JOIN dim_video v ON f.video_key = v.video_key
        GROUP BY v.has_question_mark
        ORDER BY v.has_question_mark DESC;
    """).fetchall()

    title_analysis = {
        "numbers": {
            "labels": [r["variant"] for r in num_rows],
            "avg_views": [int(r["avg_views"]) if r["avg_views"] else 0 for r in num_rows],
            "counts": [r["cnt"] for r in num_rows],
        },
        "questions": {
            "labels": [r["variant"] for r in q_rows],
            "avg_views": [int(r["avg_views"]) if r["avg_views"] else 0 for r in q_rows],
            "counts": [r["cnt"] for r in q_rows],
        },
    }

    conn.close()

    return {
        "kpis": kpis,
        "subscriber_trends": subscriber_trends,
        "category_stats": category_stats,
        "scatter_data": scatter_data,
        "title_analysis": title_analysis,
    }


def generate_html(data: dict) -> str:
    """Generate self-contained HTML with embedded JSON and Chart.js."""
    json_data = json.dumps(data)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>YouTube Content & Engagement Data Warehouse</title>
  <!-- Chart.js UMD pinned version for file:// compatibility -->
  <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg-base: #0b0f19;
      --bg-surface: #151e32;
      --bg-surface-hover: #1b2640;
      --border-subtle: #222f4c;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --accent-primary: #38bdf8;
      --accent-secondary: #818cf8;
      --accent-emerald: #34d399;
      --accent-amber: #fbbf24;
      --accent-rose: #fb7185;
      --accent-purple: #c084fc;
    }}

    * {{
      margin: 0;
      padding: 0;
      box-sizing: border-box;
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }}

    body {{
      background-color: var(--bg-base);
      color: var(--text-main);
      padding: 24px;
      min-height: 100vh;
    }}

    .container {{
      max-width: 1360px;
      margin: 0 auto;
    }}

    /* Header */
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border-subtle);
      flex-wrap: wrap;
      gap: 12px;
    }}

    .title-group h1 {{
      font-size: 1.65rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      color: var(--text-main);
      display: flex;
      align-items: center;
      gap: 10px;
    }}

    .title-group p {{
      color: var(--text-muted);
      font-size: 0.88rem;
      margin-top: 4px;
    }}

    .badge-digital {{
      background: linear-gradient(135deg, rgba(56, 189, 248, 0.15), rgba(129, 140, 248, 0.15));
      border: 1px solid rgba(56, 189, 248, 0.35);
      color: var(--accent-primary);
      padding: 4px 10px;
      border-radius: 9999px;
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}

    /* KPI Cards */
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}

    .kpi-card {{
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 12px;
      padding: 20px;
      display: flex;
      flex-direction: column;
      box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
      transition: transform 0.15s ease, border-color 0.15s ease;
    }}

    .kpi-card:hover {{
      transform: translateY(-2px);
      border-color: var(--accent-primary);
    }}

    .kpi-label {{
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-muted);
      font-weight: 600;
      margin-bottom: 8px;
    }}

    .kpi-value {{
      font-size: 1.85rem;
      font-weight: 700;
      color: var(--text-main);
      letter-spacing: -0.02em;
      margin-bottom: 4px;
    }}

    .kpi-subtext {{
      font-size: 0.75rem;
      color: var(--accent-emerald);
      display: flex;
      align-items: center;
      gap: 4px;
    }}

    /* Charts Grid */
    .charts-grid {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 20px;
    }}

    .chart-card {{
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 14px;
      padding: 20px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
      display: flex;
      flex-direction: column;
    }}

    .chart-card.full-width {{
      grid-column: span 2;
    }}

    .chart-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 16px;
    }}

    .chart-title {{
      font-size: 1.02rem;
      font-weight: 600;
      color: var(--text-main);
      letter-spacing: -0.01em;
    }}

    .chart-subtitle {{
      font-size: 0.75rem;
      color: var(--text-muted);
      margin-top: 2px;
    }}

    .canvas-container {{
      position: relative;
      height: 320px;
      width: 100%;
    }}

    .chart-card.full-width .canvas-container {{
      height: 350px;
    }}

    /* Responsive */
    @media (max-width: 992px) {{
      .charts-grid {{
        grid-template-columns: 1fr;
      }}
      .chart-card.full-width {{
        grid-column: span 1;
      }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="title-group">
        <h1>
          <span>YouTube Warehouse Analytics</span>
          <span class="badge-digital">Digital World DWBI</span>
        </h1>
        <p>Streaming Platform Content & Engagement Warehouse • Daily Snapshots & Metadata NLP</p>
      </div>
      <div>
        <span style="font-size:0.75rem; color:var(--text-muted);">Source: SQLite warehouse.db (Star Schema)</span>
      </div>
    </header>

    <!-- KPI Cards -->
    <section class="kpi-grid">
      <div class="kpi-card">
        <span class="kpi-label">Total Videos</span>
        <span class="kpi-value" id="kpi-videos">0</span>
        <span class="kpi-subtext">Across 4 major digital niches</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Total Views</span>
        <span class="kpi-value" id="kpi-views">0</span>
        <span class="kpi-subtext">Cumulative audience reach</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Total Channels</span>
        <span class="kpi-value" id="kpi-channels">0</span>
        <span class="kpi-subtext">Tech, Gaming, Education, Music</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Avg Engagement Rate</span>
        <span class="kpi-value" id="kpi-engagement">0.0%</span>
        <span class="kpi-subtext">(Likes + Comments) / Views</span>
      </div>
    </section>

    <!-- Charts Grid -->
    <main class="charts-grid">
      <!-- 1. Subscriber Growth (Line Chart) -->
      <article class="chart-card full-width">
        <div class="chart-header">
          <div>
            <h2 class="chart-title">Subscriber Growth per Channel</h2>
            <p class="chart-subtitle">Net new subscribers since day 1</p>
          </div>
        </div>
        <div class="canvas-container">
          <canvas id="chart-growth"></canvas>
        </div>
      </article>

      <!-- 2. Category Performance (Bar Chart) -->
      <article class="chart-card">
        <div class="chart-header">
          <div>
            <h2 class="chart-title">Average Views by Video Category</h2>
            <p class="chart-subtitle">Comparing average view counts across official YouTube categories</p>
          </div>
        </div>
        <div class="canvas-container">
          <canvas id="chart-category"></canvas>
        </div>
      </article>

      <!-- 3. Duration vs Engagement (Scatter Plot) -->
      <article class="chart-card">
        <div class="chart-header">
          <div>
            <h2 class="chart-title">Video Duration (Minutes) vs Engagement Rate</h2>
            <p class="chart-subtitle">Analyzing whether short, medium, or long form content drives higher interaction ratio</p>
          </div>
        </div>
        <div class="canvas-container">
          <canvas id="chart-scatter"></canvas>
        </div>
      </article>

      <!-- 4. Title Analysis: Numbers vs Questions (Bar Chart) -->
      <article class="chart-card full-width">
        <div class="chart-header">
          <div>
            <h2 class="chart-title">Title Pattern Impact on Average Views</h2>
            <p class="chart-subtitle">Average view comparison for titles with digits/numbers vs without, and questions (?) vs without</p>
          </div>
        </div>
        <div class="canvas-container">
          <canvas id="chart-titles"></canvas>
        </div>
      </article>
    </main>
  </div>

  <!-- Embedded Data & Chart Rendering -->
  <script>
    const DATA = {json_data};

    // Formatters
    const fmtCompact = (num) => {{
      if (num === 0) return '0';
      if (Math.abs(num) >= 1e9) return (num / 1e9).toFixed(2) + 'B';
      if (Math.abs(num) >= 1e6) return (num / 1e6).toFixed(1) + 'M';
      if (Math.abs(num) >= 1e3) return (num / 1e3).toFixed(0) + 'K';
      return num.toLocaleString();
    }};

    // Populate KPIs
    document.getElementById('kpi-videos').textContent = DATA.kpis.total_videos.toLocaleString();
    document.getElementById('kpi-views').textContent = fmtCompact(DATA.kpis.total_views);
    document.getElementById('kpi-channels').textContent = DATA.kpis.total_channels;
    document.getElementById('kpi-engagement').textContent = DATA.kpis.avg_engagement_rate.toFixed(2) + '%';

    // Chart Global Defaults
    Chart.defaults.color = '#94a3b8';
    Chart.defaults.font.family = "'Inter', sans-serif";
    Chart.defaults.font.size = 11;
    Chart.defaults.plugins.tooltip.backgroundColor = '#1e293b';
    Chart.defaults.plugins.tooltip.titleColor = '#f8fafc';
    Chart.defaults.plugins.tooltip.bodyColor = '#e2e8f0';
    Chart.defaults.plugins.tooltip.borderColor = '#334155';
    Chart.defaults.plugins.tooltip.borderWidth = 1;
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.cornerRadius = 8;

    const gridStyle = {{
      color: 'rgba(255, 255, 255, 0.05)',
      drawBorder: false,
    }};

    // ── Chart 1: Subscriber Growth (Line Chart) ──────────────────────────────
    const palette = [
      '#38bdf8', '#818cf8', '#c084fc', '#f472b6',
      '#fb7185', '#fbbf24', '#34d399', '#2dd4bf',
      '#a78bfa', '#60a5fa', '#4ade80', '#f87171'
    ];

    const growthCtx = document.getElementById('chart-growth').getContext('2d');
    new Chart(growthCtx, {{
      type: 'line',
      data: {{
        labels: DATA.subscriber_trends.labels.map(d => d.slice(5)), // MM-DD
        datasets: DATA.subscriber_trends.channels.map((ch, idx) => ({{
          label: ch.name,
          data: ch.values,
          borderColor: palette[idx % palette.length],
          backgroundColor: palette[idx % palette.length],
          borderWidth: 2,
          pointRadius: 3,
          pointHoverRadius: 6,
          tension: 0.25,
          fill: false,
        }}))
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        interaction: {{ mode: 'nearest', intersect: false }},
        plugins: {{
          legend: {{
            position: 'top',
            labels: {{ boxWidth: 12, padding: 12, usePointStyle: true }}
          }},
          tooltip: {{
            callbacks: {{
              label: (ctx) => `${{ctx.dataset.label}}: +${{ctx.parsed.y !== null ? ctx.parsed.y.toLocaleString() : '0'}} net subs`
            }}
          }}
        }},
        scales: {{
          x: {{ grid: gridStyle, title: {{ display: true, text: 'Snapshot Date (Month-Day)' }} }},
          y: {{
            grid: gridStyle,
            title: {{ display: true, text: 'Net New Subscribers Since Day 1' }},
            ticks: {{ callback: (v) => fmtCompact(v) }}
          }}
        }}
      }}
    }});

    // ── Chart 2: Category Views (Bar Chart) ──────────────────────────────────
    const catCtx = document.getElementById('chart-category').getContext('2d');
    new Chart(catCtx, {{
      type: 'bar',
      data: {{
        labels: DATA.category_stats.labels,
        datasets: [{{
          label: 'Average Views',
          data: DATA.category_stats.avg_views,
          backgroundColor: [
            '#38bdf8', '#818cf8', '#c084fc', '#34d399', '#fbbf24', '#fb7185'
          ],
          borderRadius: 6,
          borderSkipped: false,
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            callbacks: {{
              label: (ctx) => `Avg Views: ${{ctx.parsed.y.toLocaleString()}}`
            }}
          }}
        }},
        scales: {{
          x: {{ grid: gridStyle, title: {{ display: true, text: 'Video Category' }} }},
          y: {{
            grid: gridStyle,
            title: {{ display: true, text: 'Average Views per Video' }},
            ticks: {{ callback: (v) => fmtCompact(v) }}
          }}
        }}
      }}
    }});

    // ── Chart 3: Duration vs Engagement (Scatter Plot) ───────────────────────
    const scatterCtx = document.getElementById('chart-scatter').getContext('2d');
    new Chart(scatterCtx, {{
      type: 'scatter',
      data: {{
        datasets: [{{
          label: 'Videos',
          data: DATA.scatter_data,
          backgroundColor: 'rgba(56, 189, 248, 0.65)',
          borderColor: '#38bdf8',
          borderWidth: 1,
          pointRadius: 4,
          pointHoverRadius: 7,
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            callbacks: {{
              label: (ctx) => [
                `Title: ${{ctx.raw.title}}`,
                `Category: ${{ctx.raw.category}}`,
                `Duration: ${{ctx.parsed.x}} min`,
                `Engagement: ${{ctx.parsed.y.toFixed(2)}}%`,
                `Views: ${{ctx.raw.views.toLocaleString()}}`
              ]
            }}
          }}
        }},
        scales: {{
          x: {{
            grid: gridStyle,
            title: {{ display: true, text: 'Duration (Minutes)' }}
          }},
          y: {{
            grid: gridStyle,
            title: {{ display: true, text: 'Engagement Rate (%)' }},
            ticks: {{ callback: (v) => v + '%' }}
          }}
        }}
      }}
    }});

    // ── Chart 4: Title Features (Bar Chart) ──────────────────────────────────
    const titleCtx = document.getElementById('chart-titles').getContext('2d');
    
    // Combine Number and Question analysis
    const titleLabels = ['Has Number in Title', 'No Number in Title', 'Has Question (?)', 'No Question in Title'];
    
    // Find views for each variant
    const numIdxHas = DATA.title_analysis.numbers.labels.indexOf('Has Number');
    const numIdxNo = DATA.title_analysis.numbers.labels.indexOf('No Number');
    const qIdxHas = DATA.title_analysis.questions.labels.indexOf('Has Question (?)');
    const qIdxNo = DATA.title_analysis.questions.labels.indexOf('No Question');

    const titleViews = [
      numIdxHas >= 0 ? DATA.title_analysis.numbers.avg_views[numIdxHas] : 0,
      numIdxNo >= 0 ? DATA.title_analysis.numbers.avg_views[numIdxNo] : 0,
      qIdxHas >= 0 ? DATA.title_analysis.questions.avg_views[qIdxHas] : 0,
      qIdxNo >= 0 ? DATA.title_analysis.questions.avg_views[qIdxNo] : 0,
    ];

    const titleCounts = [
      numIdxHas >= 0 ? DATA.title_analysis.numbers.counts[numIdxHas] : 0,
      numIdxNo >= 0 ? DATA.title_analysis.numbers.counts[numIdxNo] : 0,
      qIdxHas >= 0 ? DATA.title_analysis.questions.counts[qIdxHas] : 0,
      qIdxNo >= 0 ? DATA.title_analysis.questions.counts[qIdxNo] : 0,
    ];

    new Chart(titleCtx, {{
      type: 'bar',
      data: {{
        labels: titleLabels,
        datasets: [{{
          label: 'Average Views',
          data: titleViews,
          backgroundColor: [
            '#818cf8', '#6366f1',
            '#34d399', '#059669'
          ],
          borderRadius: 6,
          borderSkipped: false,
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            callbacks: {{
              label: (ctx) => [
                `Avg Views: ${{ctx.parsed.y.toLocaleString()}}`,
                `Video Count: ${{titleCounts[ctx.dataIndex].toLocaleString()}} videos`
              ]
            }}
          }}
        }},
        scales: {{
          x: {{ grid: gridStyle, title: {{ display: true, text: 'Title Syntax Pattern' }} }},
          y: {{
            grid: gridStyle,
            title: {{ display: true, text: 'Average Views' }},
            ticks: {{ callback: (v) => fmtCompact(v) }}
          }}
        }}
      }}
    }});
  </script>
</body>
</html>
"""
    return html


def build():
    """Query warehouse and write dashboard/index.html."""
    print("Querying warehouse.db for dashboard metrics...")
    data = fetch_dashboard_data()

    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    out_file = DASHBOARD_DIR / "index.html"

    print("Generating self-contained HTML...")
    html_content = generate_html(data)

    out_file.write_text(html_content, encoding="utf-8")
    print(f"✅ Dashboard generated successfully: {out_file.resolve()}")
    return out_file


if __name__ == "__main__":
    build()
