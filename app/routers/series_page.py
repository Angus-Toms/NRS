from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import STATIC_BASE_URL
from ptd_data import queries
from app.routers.router_utils import format_time, format_time_behind

templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
router = APIRouter()

_DISCS = ["overall", "swim", "bike", "run", "transition"]


def _classify(val, thresholds):
    if val is None:
        return "beginner"
    t = thresholds["overall"]
    if val >= t["p95"]: return "expert"
    if val >= t["p85"]: return "advanced"
    if val >= t["p60"]: return "intermediate"
    if val >= t["p30"]: return "novice"
    return "beginner"


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

    # Thresholds keyed by gender
    thresholds_by_gender = {
        g: queries.get_race_standard_thresholds(g)
        for g in {r["gender"] for r in races if r.get("gender")}
    }

    for race in races:
        for p in race["podium"]:
            p["time_fmt"] = format_time(p["overall_s"])
            p["gap_fmt"]  = format_time_behind(p["gap"]) if p.get("gap") else ""

        raw = race.pop("standards_raw", None)
        thresh = thresholds_by_gender.get(race.get("gender"))
        if raw and thresh:
            race["standards"]        = {d: round(raw[d]) for d in _DISCS}
            race["standard_classes"] = {d: _classify(raw[d], thresh) for d in _DISCS}
        else:
            race["standards"]        = None
            race["standard_classes"] = None

    # Map pins — races with non-zero coordinates
    map_locations = [
        {
            "lat":     r["latitude"],
            "lng":     r["longitude"],
            "label":   r["event_name"],
            "year":    r["race_date"].year,
            "race_id": r["race_id"],
        }
        for r in races
        if r.get("latitude") and r.get("longitude")
           and abs(r["latitude"]) > 0.001 and abs(r["longitude"]) > 0.001
    ]

    for row in perf_history:
        row["year"] = row["race_date"].year
        row["race_date"] = row["race_date"].isoformat()

    if series["race_count"] and series["earliest_date"] and series["latest_date"]:
        first_year = series["earliest_date"].year
        last_year  = series["latest_date"].year
        series["subtitle"] = f"{series['race_count']} editions · {first_year}–{last_year}"
    else:
        series["subtitle"] = ""

    return templates.TemplateResponse("series.html", {
        "request":       request,
        "active_page":   "races",
        "series":        series,
        "races":         races,
        "leaders":       leaders,
        "perf_history":  perf_history,
        "map_locations": map_locations,
    })
