import os
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_ROOT / "static"
STATIC_IMG_DIR = STATIC_DIR / "imgs"
RUNTIME_DATA_DIR = Path(os.getenv("DATA_ROOT", PROJECT_ROOT / "data"))
ENV = os.getenv("PTD_ENV", "local").lower()
STATIC_BASE_URL = (
    "https://www.static.protridata.com/"
    if ENV in {"prod", "production"}
    else "/static/"
)

# Runtime data (local: ./data, render: /var/data via DATA_ROOT)
RUNTIME_ATHLETES_DIR = RUNTIME_DATA_DIR / "athletes"
RUNTIME_RACES_DIR = RUNTIME_DATA_DIR / "races"
RUNTIME_ATHLETE_IMAGES_DIR = RUNTIME_DATA_DIR / "athlete_imgs"

RUNTIME_ATHLETE_LOOKUP_PATH = RUNTIME_DATA_DIR / "athlete_lookup.pkl"
RUNTIME_FEMALE_SHORT_LEADERBOARD_PATH = RUNTIME_DATA_DIR / "female_short_leaderboard.pkl"
RUNTIME_MALE_SHORT_LEADERBOARD_PATH = RUNTIME_DATA_DIR / "male_short_leaderboard.pkl"
RUNTIME_RACE_LOOKUP_PATH = RUNTIME_DATA_DIR / "race_lookup.pkl"
RUNTIME_COUNTRY_LIST_PATH = RUNTIME_DATA_DIR / "countries.pkl"

# About content
ABOUT_DIR = STATIC_DIR / "about"
ABOUT_QA_PATH = ABOUT_DIR / "qa.json"
ABOUT_BLOG_DIR = ABOUT_DIR / "blogs"

# Raw data used to learn ELO
STATS_DATA_DIR = PROJECT_ROOT / "stats" / "data"
EVENT_CATEGORIES_CSV_PATH = STATS_DATA_DIR / "event_categories.csv"
EVENT_SPECIFICATIONS_CSV_PATH = STATS_DATA_DIR / "event_specifications.csv"
EVENTS_CSV_PATH = STATS_DATA_DIR / "events.csv"
FEMALE_SHORT_EVENTS_CSV_PATH = STATS_DATA_DIR / "female_short.csv"
MALE_SHORT_EVENTS_CSV_PATH = STATS_DATA_DIR / "male_short.csv"
FEMALE_SHORT_RESULTS_DIR = STATS_DATA_DIR / "female_short"
MALE_SHORT_RESULTS_DIR = STATS_DATA_DIR / "male_short"
CORRECTIONS_CSV_PATH = STATS_DATA_DIR / "corrections.csv"
IGNORED_RACES_CSV_PATH = STATS_DATA_DIR / "ignored.csv"
WARNINGS_CSV_PATH = STATS_DATA_DIR / "warnings.csv"
DUPLICATES_CSV_PATH = STATS_DATA_DIR / "duplicates.csv"

# DuckDB
DB_PATH = PROJECT_ROOT / "ptd_data" / "ptd.duckdb"

# WorldTriathlon API
WORLD_TRIATHLON_API_KEY = "aac0df989cb613114241670ca2f5ff75"

# Constants
ELITE_START_RATING = 1750
AG_START_RATING = 0
