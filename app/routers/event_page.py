from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import ASSET_VERSION, STATIC_BASE_URL, flag

from ptd_data import queries
from app.routers.router_utils import format_time, format_time_behind, format_course_conditions

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


def _event_course_conditions(races):
    """Course conditions for the event, precomputed at build time. Every race
    in an event carries identical stored rows (they're pooled per event), so
    the first race with rows wins. {} if no elite race has conditions."""
    for race in races:
        raw = queries.get_race_course_conditions(race['race_id'])
        if raw:
            return format_course_conditions(raw)
    return {}


def _classify(val, thresholds):
    if val is None:
        return "beginner"
    t = thresholds["overall"]
    if val >= t["p95"]: return "expert"
    if val >= t["p85"]: return "advanced"
    if val >= t["p60"]: return "intermediate"
    if val >= t["p30"]: return "novice"
    return "beginner"


def _predicted_podium(entries, race):
    """Predicted podium (top-3 by predicted overall) with per-leg splits.

    Reads the stored predictions (same rows the race page serves, so the two
    always agree). Transitions don't have their own model, so we estimate
    them as the slack between predicted overall and the predicted leg sum,
    split evenly between T1 and T2.

    Only elite races get a predicted podium: AG start lists are athletes without
    a meaningful rating history, so the prediction machinery has nothing to work
    with (matches the race page, which only predicts when category == 'elite').
    """
    if race.get('category') != 'elite':
        return []
    stored = queries.get_race_predictions(race['race_id'])
    preds = {r['athlete_id']: {d: r[f'{d}_s'] or None
                               for d in ('overall', 'swim', 'bike', 'run')}
             for r in stored}
    ranked = [r['athlete_id'] for r in stored if r['overall_s']][:3]
    if not ranked:
        return []
    emap = {e['athlete_id']: e for e in entries}

    # Field-fastest per leg, within the displayed top-3, for the "fastest" tag
    # and gap-to-fastest annotations the wide podium widget renders.
    def _ff(disc):
        vals = [preds[a].get(disc) for a in ranked if preds[a].get(disc)]
        return min(vals) if vals else None

    ff = {'swim': _ff('swim'), 'bike': _ff('bike'), 'run': _ff('run')}

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

    leader_overall = preds[ranked[0]]['overall']
    podium = []
    for i, aid in enumerate(ranked):
        p = preds[aid]
        e = emap[aid]
        o, sw, bk, rn = p.get('overall'), p.get('swim'), p.get('bike'), p.get('run')
        # Transition slack = predicted overall − (swim + bike + run), split
        # evenly so the row totals back up to the predicted overall.
        if o and sw and bk and rn:
            slack = max(0, o - (sw + bk + rn))
            t1 = slack // 2
            t2 = slack - t1
        else:
            t1 = t2 = None
        podium.append({
            'position':    i + 1,
            'athlete_id':  aid,
            'name':        e['name'],
            'country_alpha3': e.get('country_alpha3', ''),
            'profile_img': e.get('profile_img', ''),
            'time':        format_time(o) if o else None,
            'gap':         format_time_behind(o - leader_overall)
                           if (o and leader_overall and i > 0) else None,
            'swim': _leg(sw, 'swim'),
            'bike': _leg(bk, 'bike'),
            'run':  _leg(rn, 'run'),
            # Transitions get a formatted value but no fastest/gap context
            # since the slack estimate is the same for every athlete by
            # construction (we just halve the same number).
            't1':   {'fmt': format_time(t1) if t1 else None, 'fastest': False, 'gap': None},
            't2':   {'fmt': format_time(t2) if t2 else None, 'fastest': False, 'gap': None},
        })
    return podium


@router.get("/event/{event_id}", response_class=HTMLResponse)
def get_event(request: Request, event_id: int):
    event = queries.get_event_info(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    upcoming_races = queries.get_upcoming_event_races_detail(event_id)
    if upcoming_races:
        entries_by_race = queries.get_upcoming_race_entries_bulk(
            [r["race_id"] for r in upcoming_races])
        thresholds_by_gender = {
            g: queries.get_race_standard_thresholds(g)
            for g in {r["gender"] for r in upcoming_races if r.get("gender") in ("male", "female")}
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
            race["event_id"] = event_id
            race["podium"] = _predicted_podium(
                entries_by_race.get(race["race_id"], []), race)
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
    # No thresholds for 'mixed': relay races have no race_rankings rows and
    # the quantile query would return NULLs.
    thresholds_by_gender = {
        g: queries.get_race_standard_thresholds(g)
        for g in {r["gender"] for r in races if r.get("gender") in ("male", "female")}
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
    course_conditions = _event_course_conditions(sorted_races)

    return templates.TemplateResponse("event.html", {
        "request":          request,
        "active_page":      "races",
        "event":            event,
        "races":            sorted_races,
        "is_upcoming":      False,
        "course_conditions": course_conditions,
    })
