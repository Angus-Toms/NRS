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

# Country flag SVG helper, exposed as a Jinja global by each router.
def flag(code, country="", cls=""):
    if not code:
        return ""
    from markupsafe import Markup
    cls_attr = "flag" + (f" {cls}" if cls else "")
    return Markup(
        f'<img src="{STATIC_BASE_URL}flags/{code}.svg" alt="{country or code}" '
        f'class="{cls_attr}" loading="lazy">'
    )


# Runtime data (local: ./data, render: /var/data via DATA_ROOT)
RUNTIME_ATHLETE_IMAGES_DIR = RUNTIME_DATA_DIR / "athlete_imgs"

# DuckDB
DB_PATH = RUNTIME_DATA_DIR / "ptd.duckdb"

# WorldTriathlon API
WORLD_TRIATHLON_API_KEY = "aac0df989cb613114241670ca2f5ff75"

# Deployment
CF_BUCKET    = "ptd-static-assets"
RENDER_SSH   = "srv-d58kqtemcj7s73ciqqjg@ssh.frankfurt.render.com"
RENDER_DB    = "/var/data/ptd.duckdb"

