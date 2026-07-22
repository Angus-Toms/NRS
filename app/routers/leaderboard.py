from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.templating import Jinja2Templates
from config import ASSET_VERSION, STATIC_BASE_URL, flag

from ptd_data import queries
from app.routers.router_utils import format_rating, format_rating_change

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
templates.env.globals["ASSET_VERSION"] = ASSET_VERSION
templates.env.globals["flag"]          = flag


def _get_page(gender, disc, order, country, yob_start, yob_end, active_only, offset, category, course):
    if course == "relay":
        # Relay is a country-level entity: gender / YOB / country don't apply.
        athletes = queries.get_relay_leaderboard(
            disc=disc, order=order, active_only=active_only, offset=offset,
        )
    else:
        athletes = queries.get_leaderboard(
            gender=gender, disc=disc, order=order,
            country=country, yob_start=yob_start, yob_end=yob_end,
            active_only=active_only, offset=offset, category=category, course=course,
        )
    # Assign display rank and compute template fields
    for i, a in enumerate(athletes):
        a["rank"]               = offset + i + 1
        a["win_count"]          = a["wins"]
        a["profile_img_exists"] = bool(a.get("profile_img"))
        for d in ["overall", "swim", "bike", "run", "transition"]:
            a[f"{d}_rating"] = format_rating(a[f"{d}_rating"])

    if order == "hot":
        for a in athletes:
            for d in ["overall", "swim", "bike", "run", "transition"]:
                a[f"{d}_change"] = format_rating_change(a[f"{d}_change"])

    return athletes


@router.get("/athlete-leaderboard")
async def leaderboard(
    request: Request,
    gender:      str           = Query("female", regex="^(male|female)$"),
    disc:        str           = Query("overall", regex="^(overall|swim|bike|run|transition)$"),
    order:       str           = Query("top", regex="^(top|hot)$"),
    country:     str           = Query("all"),
    yob_start:   Optional[int] = Query(1930, ge=1930, le=2010),
    yob_end:     Optional[int] = Query(2010, ge=1930, le=2010),
    active_only: bool          = Query(True),
    category:    str           = Query("elite", regex="^(elite|ag)$"),
    course:      str           = Query("short", regex="^(short|long|relay)$"),
):
    athletes = _get_page(gender, disc, order, country, yob_start, yob_end, active_only, offset=0, category=category, course=course)
    country_alpha3 = queries.get_alpha3_for_country(country) if country and country != "all" else None
    return templates.TemplateResponse("leaderboard.html", {
        "request":      request,
        "active_page":  "athletes",
        "athletes":     athletes,
        "all_countries": queries.get_country_list(),
        "gender":       gender,
        "disc":         disc,
        "order":        order,
        "country":      country,
        "country_alpha3": country_alpha3,
        "yob_start":    yob_start,
        "yob_end":      yob_end,
        "active_only":  active_only,
        "category":     category,
        "course":       course,
        "is_relay":     course == "relay",
    })


@router.get("/athlete-leaderboard/more")
async def leaderboard_more(
    request: Request,
    gender:      str           = Query("female", regex="^(male|female)$"),
    disc:        str           = Query("overall", regex="^(overall|swim|bike|run|transition)$"),
    order:       str           = Query("top", regex="^(top|hot)$"),
    country:     str           = Query("all"),
    yob_start:   int           = Query(1930),
    yob_end:     int           = Query(2010),
    active_only: bool          = Query(True),
    offset:      int           = Query(0),
    category:    str           = Query("elite", regex="^(elite|ag)$"),
    course:      str           = Query("short", regex="^(short|long|relay)$"),
):
    athletes = _get_page(gender, disc, order, country, yob_start, yob_end, active_only, offset, category, course)
    return templates.TemplateResponse("partials/more_athlete_leaderboard.html", {
        "request":  request,
        "athletes": athletes,
        "disc":     disc,
        "order":    order,
        "is_relay": course == "relay",
    })
