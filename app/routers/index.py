from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from config import STATIC_BASE_URL

from ptd_data import queries
from app.routers.about import load_blogs

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL

MEN_CHAMP_ID   = 80795
WOMEN_CHAMP_ID = 79065


@router.get("/")
async def index(request: Request):
    counts = queries.get_counts()

    def champ_card(athlete_id):
        info         = queries.get_athlete_info(athlete_id)
        ratings      = queries.get_athlete_current_ratings(athlete_id)
        stats        = queries.get_athlete_stats(athlete_id)
        active_ranks = queries.get_athlete_active_rankings(athlete_id)
        # active_ranks overrides the stored world_overall from ratings so the
        # card shows rank among currently racing athletes, not all-time
        return {**info, **(ratings or {}), **stats, **(active_ranks or {})}

    blogs = load_blogs()
    return templates.TemplateResponse("index.html", {
        "request":       request,
        "active_page":   "home",
        "total_athletes": counts["athletes"],
        "total_races":    counts["races"],
        "total_results":  counts["results"],
        "men_champ":      champ_card(MEN_CHAMP_ID),
        "women_champ":    champ_card(WOMEN_CHAMP_ID),
        "recent_events":  queries.get_recent_events(0, 4),
        "men_podium":     queries.get_podium("male"),
        "women_podium":   queries.get_podium("female"),
        "latest_blog":    blogs[0] if blogs else None,
    })
