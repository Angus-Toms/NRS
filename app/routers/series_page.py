from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import STATIC_BASE_URL
from ptd_data import queries
from app.routers.router_utils import format_time, format_time_behind

templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
router = APIRouter()


@router.get("/series", response_class=HTMLResponse)
async def series_index(request: Request):
    series_list  = queries.get_all_series()
    world_champs = [s for s in series_list if s["slug"].startswith("world-")]
    olympics     = [s for s in series_list if s["slug"].startswith("olympic-")]
    return templates.TemplateResponse("series_index.html", {
        "request":     request,
        "active_page": "races",
        "world_champs": world_champs,
        "olympics":     olympics,
    })


@router.get("/series/{slug}", response_class=HTMLResponse)
async def series_detail(request: Request, slug: str):
    series = queries.get_series_by_slug(slug)
    if not series:
        raise HTTPException(status_code=404)

    races        = queries.get_series_races(series["series_id"])
    leaders      = queries.get_series_all_time_leaders(series["series_id"])
    perf_history = queries.get_series_performance_history(series["series_id"])

    # Format podium times for display
    for race in races:
        for p in race["podium"]:
            p["time_fmt"] = format_time(p["overall_s"])
            p["gap_fmt"]  = format_time_behind(p["gap"]) if p.get("gap") else ""

    # Annotate performance history rows for chart; convert date to string for JSON
    for row in perf_history:
        row["year"] = row["race_date"].year
        row["race_date"] = row["race_date"].isoformat()

    # Hero subtitle: "36 editions · 1989–2025"
    if series["race_count"] and series["earliest_date"] and series["latest_date"]:
        first_year = series["earliest_date"].year
        last_year  = series["latest_date"].year
        series["subtitle"] = f"{series['race_count']} editions · {first_year}–{last_year}"
    else:
        series["subtitle"] = ""

    return templates.TemplateResponse("series.html", {
        "request":      request,
        "active_page":  "races",
        "series":       series,
        "races":        races,
        "leaders":      leaders,
        "perf_history": perf_history,
    })
