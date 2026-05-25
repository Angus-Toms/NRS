from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from config import ASSET_VERSION, STATIC_BASE_URL

from ptd_data import queries

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
templates.env.globals["ASSET_VERSION"] = ASSET_VERSION


@router.get("/races", response_class=HTMLResponse)
async def races_landing(request: Request):
    recent_events = queries.get_recent_events(offset=0, limit=5)
    total_events  = queries.get_total_events()
    country_list  = queries.get_event_countries()
    return templates.TemplateResponse("race_search.html", {
        "request":       request,
        "active_page":   "races",
        "recent_events": recent_events,
        "total_events":  total_events,
        "country_list":  country_list,
    })


_RECENT_PAGE = 10


@router.get("/recent", response_class=HTMLResponse)
async def recent_results(request: Request):
    events = queries.get_recent_events(offset=0, limit=_RECENT_PAGE)
    return templates.TemplateResponse("recent.html", {
        "request":     request,
        "active_page": "recent",
        "events":      events,
        "has_more":    len(events) == _RECENT_PAGE,
    })


@router.get("/recent/more", response_class=HTMLResponse)
async def recent_results_more(request: Request, offset: int = Query(0, ge=0)):
    events = queries.get_recent_events(offset=offset, limit=_RECENT_PAGE)
    return templates.TemplateResponse("partials/recent_events.html", {
        "request": request,
        "events":  events,
    })


@router.get("/races/search")
async def search_races(
    q:          str = Query(""),
    country:    str = Query(""),
    year_start: str = Query(""),
    year_end:   str = Query(""),
    sort:       str = Query("desc"),
):
    if not q or len(q.strip()) < 2:
        return JSONResponse([])
    results = queries.search_events_full(
        query=q.strip(),
        country=country or None,
        year_start=year_start or None,
        year_end=year_end or None,
        sort=sort if sort in ("asc", "desc") else "desc",
        limit=20,
    )
    for r in results:
        d = r["start_date"]
        r["event_date"] = d.strftime("%-d %b %Y") if hasattr(d, "strftime") else str(d)
        del r["start_date"]
    return JSONResponse(results)
