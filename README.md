# YouTube Content & Engagement Data Warehouse

A Python-based data warehouse built on SQLite for collecting, storing, and analysing YouTube channel content and engagement metrics via the YouTube Data API v3.

## Project Structure

```
youtube-warehouse/
├── raw/            # Raw JSON responses from the YouTube API (or generated samples)
├── staging/        # Intermediate cleaned/normalised data before loading
├── sql/            # SQL DDL and query files
├── src/            # Python source code (ETL scripts, helpers, config)
├── tests/          # Pytest test suite
├── docs/           # Project documentation
├── dashboard/      # Dashboard assets (HTML/Streamlit/etc.)
├── data/           # Reference data (e.g. channels.csv)
├── .env.example    # Template for environment variables
├── requirements.txt
└── README.md
```

## Quick Start

```bash
# 1. Clone / enter the project
cd youtube-warehouse

# 2. Create & activate a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
copy .env.example .env   # then edit .env and add your API key

# 5. Generate sample data (no API key required)
python src/make_sample_data.py
```

## Step Overview

| Step | Description |
|------|-------------|
| 1    | Project scaffold, sample data generation |
| 2    | SQLite warehouse schema (DDL) |
| 3    | Extract – pull data from YouTube API |
| 4    | Transform – clean & normalise in staging |
| 5    | Load – insert into warehouse tables |
| 6    | Analytical SQL queries |
| 7    | Dashboard / visualisation |

## Data Sources

Channel metadata is stored in `data/channels.csv`. Sample data mimics the exact JSON shape of the YouTube Data API v3 responses.
