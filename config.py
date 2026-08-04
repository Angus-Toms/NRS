import os
import time
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


def _compute_asset_version() -> str:
    """Max mtime across static/css + static/js. Appended as ?v=... so
    browsers and CDNs refetch when any css/js changes."""
    files = list((STATIC_DIR / "css").glob("*.css")) + list((STATIC_DIR / "js").glob("*.js"))
    latest = max((p.stat().st_mtime for p in files), default=time.time())
    return str(int(latest))


# Computed once at import; pinned for the process lifetime so all routers
# share the same value.
ASSET_VERSION = _compute_asset_version()

# Runtime data (local: ./data, render: /var/data via DATA_ROOT)
RUNTIME_ATHLETE_IMAGES_DIR = RUNTIME_DATA_DIR / "athlete_imgs"

# DuckDB
DB_PATH = RUNTIME_DATA_DIR / "ptd.duckdb"

# WorldTriathlon API
WORLD_TRIATHLON_API_KEY = "aac0df989cb613114241670ca2f5ff75"

# IndexNow. Bing verifies ownership by fetching this key back from the domain
# root, so /<key>.txt must stay publicly reachable (served by routers/robots.py)
# and must match the key sent when pinging the IndexNow API.
INDEXNOW_KEY = "41a7559f57e14a1a8e3cbf17dc8146c5"

# Deployment
CF_BUCKET    = "ptd-static-assets"
RENDER_SSH   = "srv-d58kqtemcj7s73ciqqjg@ssh.frankfurt.render.com"
RENDER_DB    = "/var/data/ptd.duckdb"

