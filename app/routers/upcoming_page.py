from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import ASSET_VERSION, STATIC_BASE_URL
from app.display_helpers import flag

from ptd_data import queries
from app.routers.event_page import _predicted_podium

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
templates.env.globals["ASSET_VERSION"] = ASSET_VERSION
templates.env.globals["flag"]          = flag


@router.get("/upcoming", response_class=HTMLResponse)
def upcoming(request: Request):
    events = queries.get_upcoming_events()
    # Same stored predictions as the race/event pages, so every upcoming
    # podium across the site agrees.
    entries_by_race = queries.get_upcoming_race_entries_bulk(
        [r["race_id"] for e in events for r in e["races"]])
    for event in events:
        for race in event["races"]:
            race["podium"] = _predicted_podium(
                entries_by_race.get(race["race_id"], []),
                {"race_id": race["race_id"], "category": race["category"]},
            )

    return templates.TemplateResponse("upcoming.html", {
        "request":     request,
        "active_page": "upcoming",
        "events":      events,
    })
