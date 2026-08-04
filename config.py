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


# Sanctioning-body acronyms that WT puts in every race title. A word-frequency
# pass over GSC queries scores all of them at 0-3 impressions, so in a <title>
# they only eat into the ~60 characters Google actually displays.
_TITLE_NOISE = frozenset({"itu", "etu", "atu", "astc", "otu", "patco", "camtri", "cism"})


def title_words(race_title):
    """Race title with the sanctioning-body acronyms dropped, for <title> use only.

    The visible H1 keeps the official name; this is purely to buy back characters
    in the search snippet. No attempt is made to shorten the names themselves:
    the words left in it (venue, year, "triathlon", the level) are the ones that
    get searched, and the ordering is WT's, which puts the venue last often enough
    that trimming from either end loses something that matters.
    """
    return " ".join(w for w in str(race_title).split() if w.lower() not in _TITLE_NOISE)


_SUB_LABEL = {"u23": "U23", "junior": "Junior", "youth": "Youth", "ag": "Age Group"}

# The prog_names common enough to be worth rephrasing gender-first, to match how
# people search ("men's results", not "elite men"). Elite is dropped: it matches
# no search query and costs 6 of the ~60 characters Google shows.
_CANONICAL_PROGS = {
    "elite men": "Men's",        "elite women": "Women's",
    "pro men": "Men's",          "pro women": "Women's",
    "u23 men": "Men's U23",      "u23 women": "Women's U23",
    "junior men": "Men's Junior", "junior women": "Women's Junior",
    "youth men": "Men's Youth",  "youth women": "Women's Youth",
}


def program_label(race):
    """Which program of an event this page is, e.g. "Men's U23" or "18-19 Male AG Sprint".

    Every program of an event needs a distinguishable label or their titles collide
    and compete for the same query. Deriving one from gender and category is not
    enough on two counts: category only ever holds elite or ag, so junior/U23/youth
    programs all look elite through it, and an event can run several programs that
    share a gender and category anyway (age-group brackets, heats and finals).

    So prefer prog_name, which is unique within an event and already readable, and
    only rephrase the handful of forms that recur across every event. Falls back to
    gender plus category for the rare row with no prog_name.
    """
    prog = str(race.get("prog_name") or "").strip()
    if prog:
        return _CANONICAL_PROGS.get(prog.lower(), prog)

    gender = {"male": "Men's", "female": "Women's"}.get(race["gender"], "Mixed")
    label = _SUB_LABEL.get(race.get("sub_category") or race.get("category"))
    return f"{gender} {label}" if label else gender


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

