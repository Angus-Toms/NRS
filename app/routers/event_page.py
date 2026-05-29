import re

from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import ASSET_VERSION, STATIC_BASE_URL, flag

from ptd_data import queries
from ptd_data.ratings import SCALE
from app.routers.router_utils import format_time, format_time_behind
from app.routers.race_page import _course_signal_for_race

templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
templates.env.globals["ASSET_VERSION"] = ASSET_VERSION
templates.env.globals["flag"]          = flag
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
    """Predicted overall + per-discipline splits for the top-3 rated athletes.

    Each leg (swim/bike/run) is predicted off the leader's anchor time for
    that discipline, scaled by 10^((leader_rating - athlete_rating)/SCALE).
    Transitions don't have their own population model, so we estimate them
    by subtracting the predicted leg sum from the predicted overall.
    """
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

    # Predict one leg (or overall) for every athlete in top3, returning a
    # list of integer seconds aligned with `top3`. Each athlete's time is
    # the leader's anchor time scaled by their rating ratio.
    def _predict(disc):
        m = models.get((gender, distance, disc)) if distance else None
        if not (m and top3):
            return [None] * len(top3)
        rating_key = 'overall_rating' if disc == 'overall' else f'{disc}_rating'
        leader_rating = top3[0][rating_key] or START_RATING
        leader_time   = m['slope'] * leader_rating + m['intercept']
        out = []
        for a in top3:
            r = a[rating_key] or START_RATING
            out.append(max(0, round(leader_time * (10 ** ((leader_rating - r) / SCALE)))))
        return out

    overall_t = _predict('overall')
    swim_t    = _predict('swim')
    bike_t    = _predict('bike')
    run_t     = _predict('run')

    # Field-fastest per leg (within the top-3 here, since we don't have
    # the full field's predictions). Used for the "fastest" tag + gap-to-
    # fastest annotations the wide podium widget renders.
    def _ff(values):
        clean = [v for v in values if v]
        return min(clean) if clean else None

    ff = {'swim': _ff(swim_t), 'bike': _ff(bike_t), 'run': _ff(run_t)}

    def _leg(val, leg_key):
        if not val:
            return {'fmt': None, 'fastest': False, 'gap': None}
        best = ff.get(leg_key)
        if best and val == best:
            return {'fmt': format_time(val), 'fastest': True, 'gap': None}
        return {
            'fmt':     format_time(val),
            'fastest': False,
            'gap':     f'+{format_time(val - best)}' if best else None,
        }

    podium = []
    for i, athlete in enumerate(top3):
        position = i + 1
        o = overall_t[i]
        # Transition slack = predicted overall − (swim + bike + run). Split
        # it evenly between T1 and T2 so the row totals back up to the
        # predicted overall. Skip if any leg is missing.
        legs = [swim_t[i], bike_t[i], run_t[i]]
        if o and all(legs):
            slack = max(0, o - sum(legs))
            t1 = slack // 2
            t2 = slack - t1
        else:
            t1 = t2 = None
        podium.append({
            'position':    position,
            'athlete_id':  athlete['athlete_id'],
            'name':        athlete['name'],
            'country_alpha3': athlete['country_alpha3'],
            'profile_img': athlete['profile_img'],
            'time':        format_time(o) if o else None,
            'gap':         format_time_behind(o - overall_t[0])
                           if (o and overall_t[0] and position > 1) else None,
            'swim': _leg(swim_t[i], 'swim'),
            'bike': _leg(bike_t[i], 'bike'),
            'run':  _leg(run_t[i],  'run'),
            # Transitions get a formatted value but no fastest/gap context
            # since the slack estimate is the same for every athlete by
            # construction (we just halve the same number).
            't1':   {'fmt': format_time(t1) if t1 else None, 'fastest': False, 'gap': None},
            't2':   {'fmt': format_time(t2) if t2 else None, 'fastest': False, 'gap': None},
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
                race["standards"]        = {d: round(raw[d]) if raw.get(d) is not None else None for d in _DISCS}
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
            race["standards"]        = {d: round(raw[d]) if raw.get(d) is not None else None for d in _DISCS}
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
