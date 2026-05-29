from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from config import ASSET_VERSION, STATIC_BASE_URL, flag

from ptd_data import queries
from app.routers.about import load_blogs
from app.routers.upcoming_page import _build_podium

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
templates.env.globals["ASSET_VERSION"] = ASSET_VERSION
templates.env.globals["flag"]          = flag

MEN_CHAMP_ID      = 80795
WOMEN_CHAMP_ID    = 79065
MEN_IM_CHAMP_ID   = 76434     # Casper Stornes - 2025 Ironman World Champion (Nice)
WOMEN_IM_CHAMP_ID = 94515     # Solveig Løvseth - 2025 Ironman World Champion (Kona)


@router.get("/")
async def index(request: Request):
    counts = queries.get_counts()

    def champ_card(athlete_id, course='short'):
        info         = queries.get_athlete_info(athlete_id)
        ratings      = queries.get_athlete_current_ratings(athlete_id, course=course)
        stats        = queries.get_athlete_stats(athlete_id, course=course)
        active_ranks = queries.get_athlete_active_rankings(athlete_id, course=course)
        # active_ranks overrides the stored world_overall from ratings so the
        # card shows rank among currently racing athletes, not all-time
        return {**info, **(ratings or {}), **stats, **(active_ranks or {})}

    upcoming_events = queries.get_upcoming_events()[:3]
    models = queries.get_prediction_models()
    for event in upcoming_events:
        for race in event["races"]:
            race["podium"] = _build_podium(
                race.pop("top3"), race["gender"], race["event_spec_ids"], models
            )

    blogs = load_blogs()
    return templates.TemplateResponse("index.html", {
        "request":       request,
        "active_page":   "home",
        "total_athletes": counts["athletes"],
        "total_races":    counts["races"],
        "total_results":  counts["results"],
        "men_champ":         champ_card(MEN_CHAMP_ID),
        "women_champ":       champ_card(WOMEN_CHAMP_ID),
        "men_im_champ":      champ_card(MEN_IM_CHAMP_ID,   course='long'),
        "women_im_champ":    champ_card(WOMEN_IM_CHAMP_ID, course='long'),
        "recent_events":  queries.get_recent_events(0, 3),
        "upcoming_events": upcoming_events,
        "men_podium":     queries.get_podium("male"),
        "women_podium":   queries.get_podium("female"),
        "men_ag_podium":  queries.get_podium("male",   "ag"),
        "women_ag_podium": queries.get_podium("female", "ag"),
        "men_long_podium":   queries.get_podium("male",   "elite", course='long'),
        "women_long_podium": queries.get_podium("female", "elite", course='long'),
        "latest_blog":    blogs[0] if blogs else None,
    })
