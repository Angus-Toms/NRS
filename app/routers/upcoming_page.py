from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import ASSET_VERSION, STATIC_BASE_URL, flag

from ptd_data import queries
from ptd_data.ratings import SCALE
from app.routers.router_utils import format_time, format_time_behind

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
templates.env.globals["ASSET_VERSION"] = ASSET_VERSION
templates.env.globals["flag"]          = flag

START_RATING = 1500


def _build_podium(top3, gender, event_spec_ids, models):
    spec = event_spec_ids or ''
    if '376' in spec and '377' not in spec:
        distance = 'sprint'
    elif '377' in spec and '376' not in spec:
        distance = 'standard'
    else:
        distance = None

    m = models.get((gender, distance, 'overall')) if distance else None

    times = []
    if m and top3:
        leader_rating = top3[0]['overall_rating'] or START_RATING
        leader_time   = m['slope'] * leader_rating + m['intercept']
        for athlete in top3:
            rating = athlete['overall_rating'] or START_RATING
            times.append(max(0, round(leader_time * (10 ** ((leader_rating - rating) / SCALE)))))
    else:
        times = [None] * len(top3)

    podium = []
    for i, (athlete, t) in enumerate(zip(top3, times)):
        position = i + 1
        podium.append({
            'position':    position,
            'athlete_id':  athlete['athlete_id'],
            'name':        athlete['name'],
            'country_alpha3': athlete['country_alpha3'],
            'profile_img': athlete['profile_img'],
            'time':        format_time(t) if t else None,
            'gap':         format_time_behind(t - times[0]) if (t and times[0] and position > 1) else None,
        })
    return podium


@router.get("/upcoming", response_class=HTMLResponse)
async def upcoming(request: Request):
    events = queries.get_upcoming_events()
    models = queries.get_prediction_models()

    for event in events:
        for race in event["races"]:
            race["podium"] = _build_podium(
                race.pop("top3"), race["gender"], race["event_spec_ids"], models
            )

    return templates.TemplateResponse("upcoming.html", {
        "request":     request,
        "active_page": "upcoming",
        "events":      events,
    })
