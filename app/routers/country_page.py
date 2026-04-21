from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import STATIC_BASE_URL
from ptd_data import queries
from app.routers.router_utils import format_rating

templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
router = APIRouter()

_DISCS         = ["overall", "swim", "bike", "run", "transition"]
_DISC_LABELS   = {"overall": "Overall", "swim": "Swim", "bike": "Bike",
                  "run": "Run", "transition": "Transition"}
_GENDER_LABELS = {"male": "Men", "female": "Women"}

# Continent display order for the index page.
_CONTINENT_ORDER = ["Europe", "Americas", "Asia", "Oceania", "Africa", "Other"]


@router.get("/countries", response_class=HTMLResponse)
async def countries_index(request: Request, course: str = "short"):
    if course not in ("short", "long"):
        course = "short"
    countries = queries.get_countries_with_counts(course=course)

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
        "course":           course,
    })


def _build_leaderboard(country_full, gender, discipline, limit=25, course='short'):
    """Fetch + decorate leaderboard rows for both full-page and partial renders."""
    rows = queries.get_country_leaderboard(country_full, gender, discipline, limit=limit, course=course)
    for i, row in enumerate(rows, start=1):
        row["rank"]               = i
        row["profile_img_exists"] = bool(row.get("profile_img"))
        row["win_count"]          = row.get("wins", 0)
        for d in _DISCS:
            row[f"{d}_rating"] = format_rating(row[f"{d}_rating"])
    return rows


def _resolve_defaults(country_full, discipline, gender, course='short'):
    """Clamp discipline, auto-pick gender if unspecified."""
    if discipline not in _DISCS:
        discipline = "overall"
    if gender not in ("male", "female"):
        m = queries.get_country_leaderboard(country_full, "male",   discipline, limit=1, course=course)
        f = queries.get_country_leaderboard(country_full, "female", discipline, limit=1, course=course)
        m_top = m[0]["overall_rating"] if m else -1
        f_top = f[0]["overall_rating"] if f else -1
        gender = "female" if f_top > m_top else "male"
    return discipline, gender


@router.get("/country/{alpha3}", response_class=HTMLResponse)
async def country_detail(
    request: Request,
    alpha3: str,
    discipline: str = "overall",
    gender: str | None = None,
    course: str = "short",
):
    if course not in ("short", "long"):
        course = "short"
    country = queries.get_country_by_alpha3(alpha3.upper())
    if not country:
        raise HTTPException(status_code=404, detail="Country not found")

    discipline, gender = _resolve_defaults(country["country_full"], discipline, gender, course=course)

    leaderboard      = _build_leaderboard(country["country_full"], gender, discipline, course=course)
    hosted_locations = queries.get_country_hosted_race_locations(country["country_full"])
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
        "map_locations":    map_locations,
        "medals":           medals,
        "recent_events":    recent_events,
        "upcoming_events":  upcoming_events,
        "hero_stats":       hero_stats,
        "meta_description": meta_description,
        "course":           course,
    })


@router.get("/country/{alpha3}/leaderboard", response_class=HTMLResponse)
async def country_leaderboard_partial(
    request: Request,
    alpha3: str,
    discipline: str = "overall",
    gender: str | None = None,
    course: str = "short",
):
    """HTML partial: just the leaderboard table. Used for AJAX filter swaps."""
    if course not in ("short", "long"):
        course = "short"
    country = queries.get_country_by_alpha3(alpha3.upper())
    if not country:
        raise HTTPException(status_code=404, detail="Country not found")

    discipline, gender = _resolve_defaults(country["country_full"], discipline, gender, course=course)
    leaderboard = _build_leaderboard(country["country_full"], gender, discipline, course=course)

    return templates.TemplateResponse("partials/country_leaderboard.html", {
        "request":     request,
        "athletes":    leaderboard,
        "disc":        discipline,
    })
