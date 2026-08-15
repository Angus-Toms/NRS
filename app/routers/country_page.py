from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import ASSET_VERSION, STATIC_BASE_URL
from app.display_helpers import flag
from ptd_data import queries
from app.routers.router_utils import format_rating, format_rating_change, format_time, format_time_behind

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

# Relay program labels, keyed on races.sub_category.
_RELAY_PROGRAM_LABELS = {
    "elite":  "Elite MTR",
    "u23":    "U23 MTR",
    "junior": "Junior MTR",
    "youth":  "Youth MTR",
    "ag":     "Age Group MTR",
}

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
    active_only: bool = True,
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

    # Mixed team relay: country rating snapshot and full team result history.
    relay_summary = queries.get_country_relay_summary(country["country_full"])
    relay_results = []
    relay_rating_history = []
    relay_ratings = []
    if relay_summary:
        relay_summary["overall_rating_fmt"] = format_rating(relay_summary["overall_rating"])
        n = relay_summary["race_count"]
        relay_summary["win_pct"]    = relay_summary["wins"]    / n if n else 0
        relay_summary["podium_pct"] = relay_summary["podiums"] / n if n else 0

        # Per-discipline ratings widget rows, shaped like the athlete page's
        # ratings table: current rating + world rank, peak rating and the
        # biggest single-race gain, each with its race.
        extremes = queries.get_country_relay_rating_extremes(country["country_full"])
        for disc, label in [("overall", "Overall"), ("swim", "Swim"), ("bike", "Bike"),
                            ("run", "Run"), ("transition", "Transition")]:
            rank = (relay_summary["active_world_overall"] if disc == "overall"
                    else relay_summary[f"active_world_{disc}"])
            if rank:
                rank = int(rank)
                suffix = "th" if 10 <= rank % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")
                rank = {"n": rank, "suffix": suffix}
            relay_ratings.append({
                "disc":         disc,
                "label":        label,
                "rating":       format_rating(relay_summary[f"{disc}_rating"]),
                "rank":         rank,
                "peak":         format_rating(extremes[f"{disc}_peak"]),
                "peak_race":    extremes[f"{disc}_peak_race"],
                "peak_race_id": extremes[f"{disc}_peak_race_id"],
                "best_race":    extremes[f"{disc}_best_race"],
                "best_race_id": extremes[f"{disc}_best_race_id"],
                "best_change":  format_rating_change(extremes[f"{disc}_best_change"]),
            })
        legs_by_team = queries.get_country_relay_legs(country["country_full"])
        for r in queries.get_country_relay_results(country["country_full"]):
            r["total"] = format_time(r["total_s"]) if r["total_s"] else ""
            r["behind"] = (format_time_behind(r["total_s"] - r["winner_total_s"])
                           if r["total_s"] and r["winner_total_s"] else "")
            r["program"] = _RELAY_PROGRAM_LABELS[r["sub_category"]]
            r["legs"] = legs_by_team.get((r["race_id"], r["team_id"]), [])
            for l in r["legs"]:
                l["leg"] = format_time(l["leg_s"]) if l["leg_s"] else ""
                l["leg_behind"] = (format_time_behind(l["leg_s"] - l["leg_best_s"])
                                   if l["leg_best_s"] and (l["leg_s"] or 0) > l["leg_best_s"]
                                   else "")
                l["leg_fastest"] = bool(l["leg_best_s"] and l["leg_s"] == l["leg_best_s"])
                # Gaps are to the fastest split on the same leg of the same
                # race, matching the relay race page's panel.
                for d in ("swim", "t1", "bike", "t2", "run"):
                    l[d] = format_time(l[f"{d}_s"]) if l[f"{d}_s"] else ""
                    raw, fast = l[f"{d}_s"] or 0, l[f"{d}_best_s"]
                    l[f"{d}_fastest"] = bool(fast and raw == fast)
                    l[f"{d}_behind"] = (format_time_behind(raw - fast)
                                        if fast and raw > fast else "")
            relay_results.append(r)

        for h in queries.get_country_relay_rating_history(country["country_full"]):
            h["program"] = _RELAY_PROGRAM_LABELS[h["sub_category"]]
            for d in ("overall", "swim", "bike", "run", "transition"):
                h[f"{d}_change"] = format_rating_change(h[f"{d}_change"])
                h[f"{d}_rating"] = format_rating(h[f"{d}_rating"])
            relay_rating_history.append(h)

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
        "relay_rating_history": relay_rating_history,
        "relay_ratings":    relay_ratings,
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
