import re

from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import STATIC_BASE_URL

from ptd_data import queries
from ptd_data.ratings import SCALE
from app.routers.router_utils import format_time, format_time_behind
from app.routers.race_page import _course_signal_for_race

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
    """Flat list ordered by (category, gender) - elite men, elite women, u23 men, u23 women…"""
    gender_rank = {"male": 0, "female": 1}
    return sorted(races, key=lambda r: (
        _category_rank(r["prog_name"]),
        gender_rank.get(r["gender"], 2),
        r["race_date"],
        r["race_id"],
    ))


def _compute_event_course_conditions(races, models):
    """Pool course condition signals from all elite races in this event.

    Returns a dict of disc -> {formatted, category}, or {} if insufficient data.
    """
    pooled = {}           # disc -> [(pct_signal, total_w)]
    pred_overall_refs = []  # (avg_pred_overall, total_w)

    for race in races:
        if queries.get_race_category(race['race_id']) != 'elite':
            continue
        out = _course_signal_for_race(race['race_id'], race['gender'], models)
        if not out:
            continue
        signal, s_total_w, s_avg_pred_overall = out
        pred_overall_refs.append((s_avg_pred_overall, s_total_w))
        for disc, pct in signal.items():
            pooled.setdefault(disc, []).append((pct, s_total_w))

    combined_ref_w = sum(w for _, w in pred_overall_refs)
    if not combined_ref_w:
        return {}
    combined_avg_pred_overall = sum(p * w for p, w in pred_overall_refs) / combined_ref_w

    conditions = {}
    for disc in ['overall', 'swim', 'bike', 'run']:
        race_signals = pooled.get(disc, [])
        if not race_signals:
            continue
        combined_w = sum(w for _, w in race_signals)
        avg_pct    = sum(p * w for p, w in race_signals) / combined_w
        if   avg_pct >  0.03: category = 'very_fast'
        elif avg_pct >  0.01: category = 'fast'
        elif avg_pct > -0.01: category = 'normal'
        elif avg_pct > -0.03: category = 'slow'
        else:                  category = 'very_slow'
        avg_diff_s = avg_pct * combined_avg_pred_overall
        sign  = '-' if avg_diff_s >= 0 else '+'
        abs_s = abs(round(avg_diff_s))
        mins, secs = divmod(abs_s, 60)
        conditions[disc] = {'formatted': f"{sign}{mins:02d}:{secs:02d}", 'category': category}
    return conditions


def _classify(val, thresholds):
    if val is None:
        return "beginner"
    t = thresholds["overall"]
    if val >= t["p95"]: return "expert"
    if val >= t["p85"]: return "advanced"
    if val >= t["p60"]: return "intermediate"
    if val >= t["p30"]: return "novice"
    return "beginner"


def _predicted_podium(top3, gender, event_spec_ids, models):
    """Compute predicted times for the top-3 rated athletes and return podium list."""
    spec = event_spec_ids or ''
    has_sprint   = '376' in spec
    has_standard = '377' in spec
    if has_sprint and not has_standard:
        distance = 'sprint'
    elif has_standard and not has_sprint:
        distance = 'standard'
    else:
        distance = None

    START_RATING = 1500
    m = models.get((gender, distance, 'overall')) if distance else None

    # Compute predicted times for all top3
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
    for i, (athlete, predicted_time) in enumerate(zip(top3, times)):
        position = i + 1
        podium.append({
            'position':    position,
            'athlete_id':  athlete['athlete_id'],
            'name':        athlete['name'],
            'emoji':       athlete['emoji'],
            'profile_img': athlete['profile_img'],
            'time':        format_time(predicted_time) if predicted_time else None,
            'gap':         format_time_behind(predicted_time - times[0])
                           if (predicted_time and times[0] and position > 1) else None,
        })
    return podium


@router.get("/event/{event_id}", response_class=HTMLResponse)
async def get_event(request: Request, event_id: int):
    event = queries.get_event_info(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    upcoming_races = queries.get_upcoming_event_races_detail(event_id)
    if upcoming_races:
        models = queries.get_prediction_models()
        thresholds_by_gender = {
            g: queries.get_race_standard_thresholds(g)
            for g in {r["gender"] for r in upcoming_races if r.get("gender")}
        }
        for race in upcoming_races:
            raw = race.pop("standards_raw", None)
            if raw:
                race["standards"]        = {d: round(raw[d]) for d in _DISCS}
                race["standard_classes"] = {
                    d: _classify(raw[d], thresholds_by_gender.get(race["gender"], {}))
                    for d in _DISCS
                }
            else:
                race["standards"]        = None
                race["standard_classes"] = None
            race["podium"] = _predicted_podium(
                race.pop("top3", []), race["gender"], race.get("event_spec_ids", ""), models
            )
        return templates.TemplateResponse("event.html", {
            "request":          request,
            "active_page":      "upcoming",
            "event":            event,
            "races":            _sort_races(upcoming_races),
            "is_upcoming":      True,
            "course_conditions": {},
        })

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

    sorted_races = _sort_races(races)
    models = queries.get_prediction_models()
    course_conditions = _compute_event_course_conditions(sorted_races, models)

    return templates.TemplateResponse("event.html", {
        "request":          request,
        "active_page":      "races",
        "event":            event,
        "races":            sorted_races,
        "is_upcoming":      False,
        "course_conditions": course_conditions,
    })
