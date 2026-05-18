from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.templating import Jinja2Templates
from config import STATIC_BASE_URL

from ptd_data import queries
from app.routers.router_utils import format_rating

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL


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
    races = _format_race_rows(queries.get_race_leaderboard(
        gender=gender, course=course, disc=disc, year=year_int,
        country=country, level=level, offset=0,
    ))
    for i, r in enumerate(races):
        r["display_rank"] = r.get(f"{disc}_rank") or (i + 1)

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
