from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from config import STATIC_BASE_URL

from ptd_data import queries

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL

PAGE_SIZE = 30


@router.get("/races", response_class=HTMLResponse)
async def races_landing(request: Request):
    total = queries.get_total_events()
    initial = queries.get_recent_events(offset=0, limit=PAGE_SIZE)
    return templates.TemplateResponse("race_search.html", {
        "request":       request,
        "active_page":   "races",
        "events":        initial,
        "has_more":      total > PAGE_SIZE,
        "initial_offset": len(initial),
        "total_events":  total,
    })


@router.get("/races/more", response_class=HTMLResponse)
async def races_more(request: Request, offset: int = Query(0, ge=0)):
    chunk = queries.get_recent_events(offset=offset, limit=PAGE_SIZE)
    return templates.TemplateResponse("partials/more_races.html", {
        "request": request,
        "events":  chunk,
    })


@router.get("/races/search")
async def search_races(q: str = ""):
    if not q or len(q.strip()) < 2:
        return JSONResponse([])
    results = queries.search_events(q.strip())
    for r in results:
        d = r.pop("start_date")
        r["event_date"] = d.strftime("%d %B %Y") if hasattr(d, "strftime") else str(d)
    return JSONResponse(results)
