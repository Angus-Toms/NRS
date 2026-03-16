from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import STATIC_BASE_URL

from ptd_data import queries

templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
router = APIRouter()


@router.get("/event/{event_id}", response_class=HTMLResponse)
async def get_event(request: Request, event_id: int):
    event = queries.get_event_info(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    races = queries.get_races_by_event(event_id)

    return templates.TemplateResponse("event.html", {
        "request": request,
        "active_page": "races",
        "event": event,
        "races": races,
    })
