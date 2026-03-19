import re

from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import STATIC_BASE_URL

from ptd_data import queries

templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
router = APIRouter()

_DISCS = ["overall", "swim", "bike", "run", "transition"]


def _category_rank(prog_name):
    n = prog_name.lower()
    if "elite" in n:  return 0
    if "u23"   in n or "under 23" in n: return 1
    if "junior" in n or "u18" in n:     return 2
    if "para"  in n:  return 3
    return 4


def _sort_races(races):
    """Flat list ordered by (category, gender) — elite men, elite women, u23 men, u23 women…"""
    gender_rank = {"male": 0, "female": 1}
    return sorted(races, key=lambda r: (
        _category_rank(r["prog_name"]),
        gender_rank.get(r["gender"], 2),
        r["race_date"],
        r["race_id"],
    ))


def _classify(val, thresholds):
    if val is None:
        return "beginner"
    t = thresholds["overall"]
    if val >= t["p95"]: return "expert"
    if val >= t["p85"]: return "advanced"
    if val >= t["p60"]: return "intermediate"
    if val >= t["p30"]: return "novice"
    return "beginner"


@router.get("/event/{event_id}", response_class=HTMLResponse)
async def get_event(request: Request, event_id: int):
    event = queries.get_event_info(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    races = queries.get_event_races_detail(event_id)

    # Fetch thresholds once per gender present (cached after first call).
    thresholds_by_gender = {
        g: queries.get_race_standard_thresholds(g)
        for g in {r["gender"] for r in races if r.get("gender")}
    }

    for race in races:
        t = thresholds_by_gender.get(race.get("gender"), {})
        raw = race.pop("standards_raw", None)   # remove raw, attach formatted + classes
        if raw:
            race["standards"]        = {d: round(raw[d]) for d in _DISCS}
            race["standard_classes"] = {
                d: _classify(raw[d], queries.get_race_standard_thresholds(race["gender"]))
                for d in _DISCS
            }
        else:
            race["standards"]        = None
            race["standard_classes"] = None

    return templates.TemplateResponse("event.html", {
        "request":     request,
        "active_page": "races",
        "event":       event,
        "races":       _sort_races(races),
    })
