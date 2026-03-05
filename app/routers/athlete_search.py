from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from config import STATIC_BASE_URL

from ptd_data import queries
from app.routers.router_utils import format_rating

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL


@router.get("/athletes", response_class=HTMLResponse)
async def athletes_landing(request: Request):
    counts = queries.get_counts()
    female_podium = queries.get_podium("female")
    male_podium   = queries.get_podium("male")
    for a in female_podium + male_podium:
        a["overall_rating"]    = format_rating(a["overall"])
        a["profile_img_exists"] = bool(a.get("profile_img"))
    country_list  = queries.get_country_list()
    return templates.TemplateResponse("athlete_search.html", {
        "request":         request,
        "active_page":     "athletes",
        "total_athletes":  counts["athletes"],
        "total_countries": len(country_list),
        "female_podium":   female_podium,
        "male_podium":     male_podium,
    })


@router.get("/athletes/search")
async def search_athletes(q: str = ""):
    if not q or len(q.strip()) < 3:
        return JSONResponse([])
    results = queries.search_athletes(q.strip())
    # Rename key to match old template expectations
    for r in results:
        r["country"] = r.pop("country_emoji")
    return JSONResponse(results)
