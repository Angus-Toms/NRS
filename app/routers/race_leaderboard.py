from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.templating import Jinja2Templates
from config import ASSET_VERSION, STATIC_BASE_URL

from ptd_data import queries
from app.routers.router_utils import format_rating

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
templates.env.globals["ASSET_VERSION"] = ASSET_VERSION


_DISC_LIST = ["overall", "swim", "bike", "run", "transition"]


def _format_race_rows(races):
    for r in races:
        for d in _DISC_LIST:
            v = r.get(f"{d}_std")
            r[f"{d}_std_fmt"] = format_rating(v) if v is not None else "—"
        r["race_date_str"] = r["race_date"].strftime("%d %b %Y") if r["race_date"] else ""
        r["year"] = r["race_date"].year if r["race_date"] else None
    return races


def _coerce_year(year: Optional[str]):
    return int(year) if (year and year.strip()) else None


def _level_options_for(course: str):
    """Levels specific to the chosen course bucket."""
    return queries.RACE_LEVEL_OPTIONS.get(course, [])


@router.get("/race-leaderboard")
async def race_leaderboard(
    request: Request,
    gender:  str = Query("female", regex="^(male|female)$"),
    course:  str = Query("short", regex="^(short|long|ag)$"),
    disc:    str = Query("overall", regex="^(overall|swim|bike|run|transition)$"),
    year:    Optional[str] = Query(None),
    country: str = Query("all"),
    level:   str = Query("all"),
):
    year_int = _coerce_year(year)
    completed = _format_race_rows(queries.get_race_leaderboard(
        gender=gender, course=course, disc=disc, year=year_int,
        country=country, level=level, offset=0,
    ))
    for r in completed:
        r["display_rank"] = r.get(f"{disc}_rank")
        r["is_upcoming"] = False

    # Mix upcoming races into the first page so they sort alongside completed
    # races by predicted standard. Only meaningful when filters could match
    # upcoming: short-course bucket, no historical year, no series level.
    if course == 'short' and not year_int and level == 'all':
        upcoming = _format_race_rows(queries.get_upcoming_race_leaderboard(
            gender=gender, course=course, country=country,
        ))
        std_key = f"{disc}_std"
        races = sorted(
            completed + upcoming,
            key=lambda r: r.get(std_key) or 0,
            reverse=True,
        )
        # Renumber the merged list so completed and upcoming share a single
        # rank universe — otherwise upcoming races above the current leader
        # collide with the precomputed #1 from race_rankings.
        for i, r in enumerate(races):
            std = r.get(std_key)
            r["display_rank"] = (i + 1) if std is not None else None
    else:
        races = completed

    years     = queries.get_race_leaderboard_years(gender, course)
    countries = queries.get_race_leaderboard_countries(gender, course)
    levels    = _level_options_for(course)

    return templates.TemplateResponse("race_leaderboard.html", {
        "request":     request,
        "active_page": "races",
        "races":       races,
        "gender":      gender,
        "course":      course,
        "disc":        disc,
        "year":        year,
        "country":     country,
        "level":       level,
        "years":       years,
        "countries":   countries,
        "levels":      levels,
    })


@router.get("/race-leaderboard/more")
async def race_leaderboard_more(
    request: Request,
    gender:  str = Query("female", regex="^(male|female)$"),
    course:  str = Query("short", regex="^(short|long|ag)$"),
    disc:    str = Query("overall", regex="^(overall|swim|bike|run|transition)$"),
    year:    Optional[str] = Query(None),
    country: str = Query("all"),
    level:   str = Query("all"),
    offset:  int = Query(0),
):
    year_int = _coerce_year(year)
    races = _format_race_rows(queries.get_race_leaderboard(
        gender=gender, course=course, disc=disc, year=year_int,
        country=country, level=level, offset=offset,
    ))
    for i, r in enumerate(races):
        r["display_rank"] = r.get(f"{disc}_rank") or (offset + i + 1)
    return templates.TemplateResponse("partials/race_leaderboard_rows.html", {
        "request": request,
        "races":   races,
        "disc":    disc,
    })
