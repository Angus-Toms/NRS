import os
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_ROOT / "static"
STATIC_IMG_DIR = STATIC_DIR / "imgs"
RUNTIME_DATA_DIR = Path(os.getenv("DATA_ROOT", PROJECT_ROOT / "ptd_data"))
ENV = os.getenv("PTD_ENV", "local").lower()
STATIC_BASE_URL = (
    "https://www.static.protridata.com/"
    if ENV in {"prod", "production"}
    else "/static/"
)

# Runtime data (local: ./data, render: /var/data via DATA_ROOT)
RUNTIME_ATHLETE_IMAGES_DIR = RUNTIME_DATA_DIR / "athlete_imgs"

# DuckDB
DB_PATH = RUNTIME_DATA_DIR / "ptd.duckdb"

# WorldTriathlon API
WORLD_TRIATHLON_API_KEY = "aac0df989cb613114241670ca2f5ff75"

