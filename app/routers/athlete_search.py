import random

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from config import STATIC_BASE_URL

from ptd_data import queries
from app.routers.router_utils import format_rating

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL


def _fmt_change(v):
    if not v:
        return "-"
    return f"+{int(round(v))}" if v > 0 else str(int(round(v)))


def _fmt_athlete(a):
    return {
        **a,
        "overall_rating":     format_rating(a["overall_rating"]),
        "swim_rating":        format_rating(a["swim_rating"]),
        "bike_rating":        format_rating(a["bike_rating"]),
        "run_rating":         format_rating(a["run_rating"]),
        "transition_rating":  format_rating(a["transition_rating"]),
        "overall_change_fmt": _fmt_change(a.get("overall_change", 0)),
        "profile_img_exists": bool(a.get("profile_img")),
    }


@router.get("/athletes", response_class=HTMLResponse)
async def athletes_landing(request: Request, course: str = "short"):
    if course not in ("short", "long"):
        course = "short"
    counts       = queries.get_counts()
    country_list = queries.get_country_list()

    # Trending: random pick from top 25 most improved (1yr), active athletes, 1 per gender
    female_trending_rows = queries.get_leaderboard(
        "female", "overall", "hot", None, None, None, True, 0, 25, course=course)
    male_trending_rows   = queries.get_leaderboard(
        "male",   "overall", "hot", None, None, None, True, 0, 25, course=course)

    female_trending = _fmt_athlete(random.choice(female_trending_rows)) if female_trending_rows else None
    male_trending   = _fmt_athlete(random.choice(male_trending_rows))   if male_trending_rows   else None

    # All-time leaderboard top 5 per gender
    female_lb = [_fmt_athlete(a) for a in
                 queries.get_leaderboard("female", "overall", "top", None, None, None, False, 0, 5, course=course)]
    male_lb   = [_fmt_athlete(a) for a in
                 queries.get_leaderboard("male",   "overall", "top", None, None, None, False, 0, 5, course=course)]

    return templates.TemplateResponse("athlete_search.html", {
        "request":         request,
        "active_page":     "athletes",
        "total_athletes":  counts["athletes"],
        "total_countries": len(country_list),
        "country_list":    country_list,
        "female_trending": female_trending,
        "male_trending":   male_trending,
        "female_lb":       female_lb,
        "male_lb":         male_lb,
        "course":          course,
    })


@router.get("/athletes/search")
async def search_athletes(
    q: str = "",
    disc: str = "overall",
    order: str = "top",
    country: str = "",
    yob_start: str = "",
    yob_end: str = "",
    active_only: str = "",
    course: str = "short",
):
    if not q or len(q.strip()) < 2:
        return JSONResponse([])
    if disc not in {"overall", "swim", "bike", "run", "transition"}:
        disc = "overall"
    if order not in {"top", "hot"}:
        order = "top"
    if course not in ("short", "long"):
        course = "short"

    results = queries.search_athletes_full(
        q.strip(),
        disc=disc,
        order=order,
        country=country or None,
        yob_start=int(yob_start) if yob_start.isdigit() else None,
        yob_end=int(yob_end)   if yob_end.isdigit()   else None,
        active_only=active_only == "true",
        course=course,
    )
    for r in results:
        r["overall_rating"]    = format_rating(r["overall_rating"])
        r["swim_rating"]       = format_rating(r["swim_rating"])
        r["bike_rating"]       = format_rating(r["bike_rating"])
        r["run_rating"]        = format_rating(r["run_rating"])
        r["transition_rating"] = format_rating(r["transition_rating"])
        r["has_img"]           = bool(r.pop("profile_img"))
        # race_starts and wins pass through as-is
    return JSONResponse(results)
