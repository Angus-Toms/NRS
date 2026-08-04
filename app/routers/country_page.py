from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import ASSET_VERSION, STATIC_BASE_URL
from app.display_helpers import flag
from ptd_data import queries
from app.routers.router_utils import format_rating, format_time

templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
templates.env.globals["ASSET_VERSION"] = ASSET_VERSION
templates.env.globals["flag"]          = flag
router = APIRouter()

_DISCS         = ["overall", "swim", "bike", "run", "transition"]
_DISC_LABELS   = {"overall": "Overall", "swim": "Swim", "bike": "Bike",
                  "run": "Run", "transition": "Transition"}
_GENDER_LABELS = {"male": "Men", "female": "Women"}
_LB_PAGE_SIZE  = 5

# Continent display order for the index page.
_CONTINENT_ORDER = ["Europe", "Americas", "Asia", "Oceania", "Africa", "Other"]


@router.get("/countries", response_class=HTMLResponse)
def countries_index(request: Request):
    countries = queries.get_countries_with_counts()

    # Group by continent, preserving athlete-count-desc order within each bucket.
    groups = {c: [] for c in _CONTINENT_ORDER}
    for c in countries:
        bucket = c.get("continent") or "Other"
        groups.setdefault(bucket, []).append(c)

    continent_blocks = [
        {"continent": cont, "countries": groups[cont]}
        for cont in _CONTINENT_ORDER if groups.get(cont)
    ]

    totals = {
        "countries":  sum(1 for c in countries if c["athlete_count"] > 0),
        "athletes":   sum(c["athlete_count"]    for c in countries),
        "host_races": sum(c["race_host_count"]  for c in countries),
    }

    return templates.TemplateResponse("countries_index.html", {
        "request":          request,
        "active_page":      "countries",
        "continent_blocks": continent_blocks,
        "totals":           totals,
    })


def _build_leaderboard(alpha3, gender, discipline, limit=_LB_PAGE_SIZE, offset=0, course='short', active_only=False):
    """Fetch a page of leaderboard rows plus a has_more flag.

    Asks the DB for one extra row; if it came back, there are more rows available
    and the trailing extra is trimmed before decorating.
    """
    rows = queries.get_country_leaderboard(
        alpha3, gender, discipline,
        limit=limit + 1, offset=offset, course=course, active_only=active_only,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    for i, row in enumerate(rows, start=offset + 1):
        row["rank"]               = i
        row["profile_img_exists"] = bool(row.get("profile_img"))
        row["win_count"]          = row.get("wins", 0)
        for d in _DISCS:
            row[f"{d}_rating"] = format_rating(row[f"{d}_rating"])
    return rows, has_more


def _filter_map_outliers(locs, lat_thresh=15.0, lng_thresh=25.0):
    """Drop locations far from the median - catches obvious geocoding mistakes.

    Uses the median as a robust centre (unaffected by the outliers we're trying
    to remove). Thresholds are generous enough to retain legitimate spread for
    mid-to-large countries while clearly rejecting points on the wrong continent.
    """
    if len(locs) < 4:
        return locs
    lats = sorted(l["latitude"] for l in locs)
    lngs = sorted(l["longitude"] for l in locs)
    mid_lat = lats[len(lats) // 2]
    mid_lng = lngs[len(lngs) // 2]
    return [l for l in locs
            if abs(l["latitude"]  - mid_lat) <= lat_thresh
            and abs(l["longitude"] - mid_lng) <= lng_thresh]


def _resolve_defaults(alpha3, discipline, gender, course='short'):
    """Clamp discipline, auto-pick gender if unspecified."""
    if discipline not in _DISCS:
        discipline = "overall"
    if gender not in ("male", "female"):
        m = queries.get_country_leaderboard(alpha3, "male",   discipline, limit=1, course=course)
        f = queries.get_country_leaderboard(alpha3, "female", discipline, limit=1, course=course)
        m_top = m[0]["overall_rating"] if m else -1
        f_top = f[0]["overall_rating"] if f else -1
        gender = "female" if f_top > m_top else "male"
    return discipline, gender


@router.get("/country/{alpha3}", response_class=HTMLResponse)
def country_detail(
    request: Request,
    alpha3: str,
    discipline: str = "overall",
    gender: str | None = None,
    course: str = "short",
    active_only: bool = False,
):
    if course not in ("short", "long"):
        course = "short"
    country = queries.get_country_by_alpha3(alpha3.upper())
    if not country:
        raise HTTPException(status_code=404, detail="Country not found")

    discipline, gender = _resolve_defaults(country["alpha3"], discipline, gender, course=course)

    leaderboard, has_more = _build_leaderboard(country["alpha3"], gender, discipline, course=course, active_only=active_only)
    hosted_locations = _filter_map_outliers(queries.get_country_hosted_race_locations(country["country_full"]))
    medals           = queries.get_country_championship_medals(country["country_full"])
    recent_events    = queries.get_recent_events(offset=0, limit=6, country=country["country_full"])
    upcoming_events  = queries.get_upcoming_events(country=country["country_full"])

    map_locations = [
        {
            "lat":   loc["latitude"],
            "lng":   loc["longitude"],
            "label": loc["event_name"],
            "year":  loc["start_date"].year if loc["start_date"] else None,
            "event_id": loc["event_id"],
        }
        for loc in hosted_locations
    ]

    # Mixed team relay: country rating snapshot, recent team results, and a
    # suggested lineup from the top-rated active athletes (current cycle
    # order: female, male, female, male).
    relay_summary = queries.get_country_relay_summary(country["country_full"])
    relay_results = []
    suggested_team = []
    if relay_summary:
        relay_summary["overall_rating_fmt"] = format_rating(relay_summary["overall_rating"])
        for r in queries.get_country_relay_results(country["country_full"]):
            r["total"] = format_time(r["total_s"]) if r["total_s"] else ""
            relay_results.append(r)
        top_m = queries.get_country_leaderboard(country["alpha3"], "male",   "overall", limit=2, active_only=True)
        top_f = queries.get_country_leaderboard(country["alpha3"], "female", "overall", limit=2, active_only=True)
        if len(top_m) >= 2 and len(top_f) >= 2:
            suggested_team = [
                {"leg": i + 1, **a}
                for i, a in enumerate([top_f[0], top_m[0], top_f[1], top_m[1]])
            ]

    # Hero stats: athletes, events, championship medal total
    medals_total = sum(m["total"] for m in medals)
    hero_stats = [
        {"num": country["athlete_count"],   "label": "athletes"},
        {"num": country["race_host_count"], "label": "events"},
        {"num": medals_total,               "label": "championship medals"},
    ]

    leader_name = leaderboard[0]["name"] if leaderboard else None
    meta_description = (
        f"Rankings, hosted races and championship medals for {country['country_full']} on Pro Tri Data. "
        f"{leader_name} leads the national rankings."
        if leader_name else
        f"Rankings and race data for {country['country_full']} on Pro Tri Data."
    )

    return templates.TemplateResponse("country.html", {
        "request":          request,
        "active_page":      "countries",
        "country":          country,
        "discipline":       discipline,
        "discipline_label": _DISC_LABELS[discipline],
        "gender":           gender,
        "gender_label":     _GENDER_LABELS[gender],
        "discs":            _DISCS,
        "disc_labels":      _DISC_LABELS,
        "gender_options":   [("male", "Men"), ("female", "Women")],
        "leaderboard":      leaderboard,
        "has_more":         has_more,
        "map_locations":    map_locations,
        "medals":           medals,
        "recent_events":    recent_events,
        "upcoming_events":  upcoming_events,
        "hero_stats":       hero_stats,
        "meta_description": meta_description,
        "course":           course,
        "active_only":      active_only,
        "relay_summary":    relay_summary,
        "relay_results":    relay_results,
        "suggested_team":   suggested_team,
    })


@router.get("/country/{alpha3}/leaderboard", response_class=HTMLResponse)
def country_leaderboard_partial(
    request: Request,
    alpha3: str,
    discipline: str = "overall",
    gender: str | None = None,
    course: str = "short",
    active_only: bool = False,
):
    """HTML partial: the full leaderboard grid (first page). Used for filter swaps."""
    if course not in ("short", "long"):
        course = "short"
    country = queries.get_country_by_alpha3(alpha3.upper())
    if not country:
        raise HTTPException(status_code=404, detail="Country not found")

    discipline, gender = _resolve_defaults(country["alpha3"], discipline, gender, course=course)
    leaderboard, has_more = _build_leaderboard(country["alpha3"], gender, discipline, course=course, active_only=active_only)

    return templates.TemplateResponse("partials/country_leaderboard.html", {
        "request":     request,
        "athletes":    leaderboard,
        "disc":        discipline,
        "has_more":    has_more,
    })


@router.get("/country/{alpha3}/leaderboard/more", response_class=HTMLResponse)
def country_leaderboard_more(
    request: Request,
    alpha3: str,
    discipline: str = "overall",
    gender: str | None = None,
    course: str = "short",
    offset: int = 0,
    active_only: bool = False,
):
    """HTML partial: just extra athlete-card rows, appended by the See more button."""
    if course not in ("short", "long"):
        course = "short"
    country = queries.get_country_by_alpha3(alpha3.upper())
    if not country:
        raise HTTPException(status_code=404, detail="Country not found")

    discipline, gender = _resolve_defaults(country["alpha3"], discipline, gender, course=course)
    leaderboard, has_more = _build_leaderboard(
        country["alpha3"], gender, discipline, offset=offset, course=course, active_only=active_only,
    )

    response = templates.TemplateResponse("partials/country_leaderboard_rows.html", {
        "request":  request,
        "athletes": leaderboard,
        "disc":     discipline,
    })
    response.headers["X-Has-More"] = "1" if has_more else "0"
    return response
