from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from config import STATIC_BASE_URL

from ptd_data import queries

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL


@router.get("/")
async def index(request: Request):
    counts = queries.get_counts()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "active_page": "home",
        "total_athletes": counts["athletes"],
        "total_races":    counts["races"],
        "total_results":  counts["results"],
    })
