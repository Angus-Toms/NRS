import math

import numpy as np

from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import STATIC_BASE_URL

from ptd_data import queries
from ptd_data.ratings import SCALE
from app.routers.router_utils import format_time, format_time_behind, format_rating, format_rating_change

templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
router = APIRouter()

DNF_STATUSES = {"DNF", "DNS", "DQ", "LAP", "NC"}
START_RATING  = 1500


def _compute_race_predictions(race_id, race, results, models):
    """Compute full-field predicted times, position diffs, and course conditions.

    Returns (pred_rows, pos_diffs, course_conditions).
    pred_rows: sorted by predicted overall time.
    pos_diffs: athlete_id -> (predicted_pos - actual_pos), positive = beat prediction.
    course_conditions: disc -> {formatted, category} where formatted is ±mm:ss.
    Returns (None, None, None) if predictions are unavailable for this race.
    """
    distance = queries.get_race_distance_type(race_id)
    if not distance:
        return None, None, None

    pre_ratings = {r['athlete_id']: r for r in queries.get_race_pre_race_ratings(race_id)}
    gender  = race['gender']
    DISCS   = ['overall', 'swim', 'bike', 'run']  # transitions excluded — too course-specific

    # For each discipline, pick the highest-rated athlete as predicted leader,
    # then derive all other times via the ELO log-ratio formula.
    preds = {}  # athlete_id -> {disc: predicted_time_s}
    for disc in DISCS:
        m = models.get((gender, distance, disc))
        if not m:
            continue
        field_ratings = {}
        for r in results:
            aid = r['athlete_id']
            pr  = pre_ratings.get(aid)
            field_ratings[aid] = pr[disc] if pr and pr[disc] else START_RATING

        leader_id     = max(field_ratings, key=field_ratings.get)
        leader_rating = field_ratings[leader_id]
        leader_time   = m['slope'] * leader_rating + m['intercept']

        for aid, rating in field_ratings.items():
            t = leader_time * (10 ** ((leader_rating - rating) / SCALE))
            preds.setdefault(aid, {})[disc] = max(0, round(t))

    if not preds:
        return None, None, None

    # Build rows sorted by predicted overall time
    pred_rows = []
    for r in results:
        aid = r['athlete_id']
        p   = preds.get(aid, {})
        pred_rows.append({
            'athlete_id':    aid,
            'name':          r['name'],
            'country_emoji': r.get('country_emoji', ''),
            'year_of_birth': r.get('year_of_birth'),
            'is_debut':      aid not in pre_ratings,
            '_overall_raw':  p.get('overall', 9_999_999),
            '_swim_raw':     p.get('swim', 0),
            '_bike_raw':     p.get('bike', 0),
            '_run_raw':      p.get('run', 0),
        })
    pred_rows.sort(key=lambda x: x['_overall_raw'])
    for i, row in enumerate(pred_rows):
        row['predicted_position'] = i + 1

    # Fastest split times across the field
    best = {}
    for disc in DISCS:
        vals = [r[f'_{disc}_raw'] for r in pred_rows if r.get(f'_{disc}_raw', 0) > 0]
        best[disc] = min(vals) if vals else None

    # Annotate each row with formatted times, fastest flags, and behind gaps
    for row in pred_rows:
        for disc in DISCS:
            raw = row.pop(f'_{disc}_raw', 0)
            key = 'overall_s' if disc == 'overall' else f'{disc}_s'
            row[key] = format_time(raw)
            b = best[disc]
            if disc == 'overall':
                row['overall_behind_s'] = format_time_behind(raw - b) if (raw and b and raw != b) else ''
            else:
                row[f'{disc}_fastest']  = bool(raw and b and raw == b)
                row[f'{disc}_behind_s'] = format_time_behind(raw - b) if (raw and b and raw != b) else ''

    pred_pos_map = {r['athlete_id']: r['predicted_position'] for r in pred_rows}
    pos_diffs = {}
    for r in results:
        if r['status'] in DNF_STATUSES:
            continue
        aid     = r['athlete_id']
        actual  = r['position']
        predicted = pred_pos_map.get(aid)
        if predicted is not None and actual is not None:
            pos_diffs[aid] = predicted - actual  # positive = beat prediction

    # Course conditions: avg(predicted - actual) per discipline for finishers.
    # Expressed as % of avg predicted overall; positive = fast/short course.
    # Thresholds: ±1% = normal, ±1-3% = fast/slow, >±3% = very fast/slow.
    pred_overalls = [preds[r['athlete_id']]['overall'] for r in results
                     if r['status'] not in DNF_STATUSES
                     and r['athlete_id'] in preds
                     and preds[r['athlete_id']].get('overall')]
    avg_pred_overall = sum(pred_overalls) / len(pred_overalls) if pred_overalls else None

    course_conditions = {}
    if avg_pred_overall:
        for disc in DISCS:
            time_key = f'{disc}_s'
            diffs = [
                preds[r['athlete_id']][disc] - r[time_key]
                for r in results
                if r['status'] not in DNF_STATUSES
                and r['athlete_id'] in preds
                and preds[r['athlete_id']].get(disc)
                and (r.get(time_key) or 0) > 0
            ]
            if len(diffs) < 3:
                continue
            avg_diff = sum(diffs) / len(diffs)
            pct = avg_diff / avg_pred_overall
            if   pct >  0.03: category = 'very_fast'
            elif pct >  0.01: category = 'fast'
            elif pct > -0.01: category = 'normal'
            elif pct > -0.03: category = 'slow'
            else:              category = 'very_slow'
            # positive avg_diff = predicted > actual = course is fast = show -
            sign  = '-' if avg_diff >= 0 else '+'
            abs_s = abs(round(avg_diff))
            mins, secs = divmod(abs_s, 60)
            course_conditions[disc] = {
                'formatted': f"{sign}{mins:02d}:{secs:02d}",
                'category':  category,
            }

    return pred_rows, pos_diffs, course_conditions


def _build_time_histograms(time_values, bins=20):
    """
    Build Chart.js histogram data from raw time arrays.
    time_values: dict of discipline -> list of seconds values.
    """
    discipline_details = {
        "overall": {"background": "#357ABD", "display_name": "Overall"},
        "swim":    {"background": "#4CAF50", "display_name": "Swim"},
        "bike":    {"background": "#FF9800", "display_name": "Bike"},
        "run":     {"background": "#E91E63", "display_name": "Run"},
        "t1":      {"background": "#9C27B0", "display_name": "Transition 1"},
        "t2":      {"background": "#673AB7", "display_name": "Transition 2"},
    }
    chart_data = {}
    for disc, values in time_values.items():
        if not values:
            chart_data[disc] = {}
            continue
        counts, bin_edges = np.histogram(values, bins=bins)
        bin_centers = [(bin_edges[i] + bin_edges[i + 1]) / 2 for i in range(len(counts))]
        bin_labels = []
        for i in range(len(bin_edges) - 1):
            if round(bin_edges[i + 1]) - round(bin_edges[i]) <= 1:
                bin_labels.append(format_time(round(bin_edges[i])))
            else:
                bin_labels.append(f"{format_time(int(bin_edges[i]))} - {format_time(int(bin_edges[i + 1]))}")
        chart_data[disc] = {
            "labels": bin_centers,
            "datasets": [{
                "label": discipline_details[disc]["display_name"],
                "data": [
                    {"x": c, "y": int(n), "label": lbl}
                    for c, n, lbl in zip(bin_centers, counts, bin_labels)
                ],
                "backgroundColor": discipline_details[disc]["background"],
                "borderWidth": 0,
                "barPercentage": 1.0,
            }],
        }
    return chart_data


def _build_rating_histograms(rating_values, bins=20):
    """Build Chart.js histogram data from raw rating arrays."""
    discipline_details = {
        "overall":    {"background": "#357ABD"},
        "swim":       {"background": "#4CAF50"},
        "bike":       {"background": "#FF9800"},
        "run":        {"background": "#E91E63"},
        "transition": {"background": "#9C27B0"},
    }
    chart_data = {}
    for disc, values in rating_values.items():
        if not values:
            chart_data[disc] = {}
            continue
        counts, bin_edges = np.histogram(values, bins=bins)
        bin_centers = [(bin_edges[i] + bin_edges[i + 1]) / 2 for i in range(len(counts))]
        bin_labels = [
            f"{int(bin_edges[i])} - {int(bin_edges[i + 1])}"
            for i in range(len(bin_edges) - 1)
        ]
        chart_data[disc] = {
            "labels": bin_centers,
            "datasets": [{
                "label": disc.capitalize(),
                "data": [
                    {"x": c, "y": int(n), "label": lbl}
                    for c, n, lbl in zip(bin_centers, counts, bin_labels)
                ],
                "backgroundColor": discipline_details[disc]["background"],
                "borderWidth": 0,
                "barPercentage": 1.0,
            }],
        }
    return chart_data


@router.get("/race/{race_id}", response_class=HTMLResponse)
async def get_race(request: Request, race_id: int, partial: bool = False):
    race = queries.get_race_info(race_id)
    if not race:
        raise HTTPException(status_code=404, detail=f"Race {race_id} not found")

    ignored_info = queries.get_race_ignored_info(race_id)
    results   = queries.get_race_results(race_id)
    ratings   = queries.get_race_ratings(race_id)
    standards = queries.get_race_standards(race_id)
    best_perf = queries.get_race_best_performances(race_id)

    finish_count = sum(1 for r in results if r["status"] not in DNF_STATUSES)
    dnf_count    = len(results) - finish_count

    # Add race year for age calculation
    race_year = race["race_date"].year if hasattr(race["race_date"], "year") else int(str(race["race_date"])[:4])
    for r in results + ratings:
        r["age"] = race_year - r["year_of_birth"] if r["year_of_birth"] else None

    # Compute full-field predictions using pre-trained global models
    race_distance = queries.get_race_distance_type(race_id)  # 'sprint' | 'standard' | None
    predictions, pos_diffs, course_conditions = _compute_race_predictions(
        race_id, race, results, queries.get_prediction_models()
    )
    if predictions:
        for r in predictions:
            r["age"] = race_year - r["year_of_birth"] if r["year_of_birth"] else None

    # Format splits data
    splits_data = [{
        **r,
        "overall_s":        format_time(r["overall_s"]),
        "overall_behind_s": format_time_behind(r["overall_behind_s"]),
        "swim_s":           format_time(r["swim_s"]),
        "swim_behind_s":    format_time_behind(r["swim_behind_s"]),
        "bike_s":           format_time(r["bike_s"]),
        "bike_behind_s":    format_time_behind(r["bike_behind_s"]),
        "run_s":            format_time(r["run_s"]),
        "run_behind_s":     format_time_behind(r["run_behind_s"]),
        "t1_s":             format_time(r["t1_s"]),
        "t1_behind_s":      format_time_behind(r["t1_behind_s"]),
        "t2_s":             format_time(r["t2_s"]),
        "t2_behind_s":      format_time_behind(r["t2_behind_s"]),
        # fastest flags: behind == 0 AND the split was actually recorded
        "swim_fastest": r["swim_behind_s"] == 0 and (r["swim_s"] or 0) > 0,
        "bike_fastest": r["bike_behind_s"] == 0 and (r["bike_s"] or 0) > 0,
        "run_fastest":  r["run_behind_s"]  == 0 and (r["run_s"]  or 0) > 0,
        "t1_fastest":   r["t1_behind_s"]   == 0 and (r["t1_s"]   or 0) > 0,
        "t2_fastest":   r["t2_behind_s"]   == 0 and (r["t2_s"]   or 0) > 0,
        "pos_diff":     pos_diffs.get(r["athlete_id"]) if pos_diffs else None,
    } for r in results]

    # Format ratings data
    ratings_data = [{
        **r,
        "overall_rating":    format_rating(r["overall_rating"]),
        "swim_rating":       format_rating(r["swim_rating"]),
        "bike_rating":       format_rating(r["bike_rating"]),
        "run_rating":        format_rating(r["run_rating"]),
        "transition_rating": format_rating(r["transition_rating"]),
        "overall_change":    format_rating_change(r["overall_change"]),
        "swim_change":       format_rating_change(r["swim_change"]),
        "bike_change":       format_rating_change(r["bike_change"]),
        "run_change":        format_rating_change(r["run_change"]),
        "transition_change": format_rating_change(r["transition_change"]),
    } for r in ratings]

    # Format standards and classify by percentile vs all races of this gender
    race_standards = {d: format_rating(v) for d, v in standards.items()}

    thresholds = queries.get_race_standard_thresholds(race["gender"])
    def _classify(val, t):
        if val >= t["p95"]: return "expert"
        if val >= t["p85"]: return "advanced"
        if val >= t["p60"]: return "intermediate"
        if val >= t["p30"]: return "novice"
        return "beginner"
    race_standard_classes = {d: _classify(standards[d], thresholds[d]) for d in standards}

    # Format best performances
    best_performances = {}
    for disc in ["overall", "swim", "bike", "run", "transition"]:
        best_performances[f"{disc}_change"]       = format_rating_change(best_perf[f"{disc}_change"])
        best_performances[f"{disc}_athlete_name"] = best_perf[f"{disc}_athlete_name"]

    # Prediction model data: rating→time pairs with race-count weights for WLS
    # Store full rating record so discipline models can be fitted in JS
    ratings_by_id  = {r["athlete_id"]: r for r in ratings}
    finisher_ids   = [r["athlete_id"] for r in results
                      if r["status"] not in DNF_STATUSES and (r["overall_s"] or 0) > 0]
    race_count_map = queries.get_athlete_race_counts(finisher_ids)
    prediction_data = [
        {
            "rating":      rat["overall_rating"],
            "time":        r["overall_s"],
            "swim_rating": rat["swim_rating"],
            "swim_time":   r["swim_s"] if (r["swim_s"] or 0) > 0 else None,
            "bike_rating": rat["bike_rating"],
            "bike_time":   r["bike_s"] if (r["bike_s"] or 0) > 0 else None,
            "run_rating":  rat["run_rating"],
            "run_time":    r["run_s"] if (r["run_s"] or 0) > 0 else None,
            "w":           max(1, race_count_map.get(r["athlete_id"], 1)),
        }
        for r in results
        if r["status"] not in DNF_STATUSES
        and (r["overall_s"] or 0) > 0
        and (rat := ratings_by_id.get(r["athlete_id"])) is not None
    ]

    # Histograms
    time_hists   = _build_time_histograms(queries.get_race_time_values(race_id))
    rating_hists = _build_rating_histograms(queries.get_race_rating_values(race_id))

    event_id = race.get("event_id")
    event_races = queries.get_races_by_event(event_id) if event_id else []

    _venue = race["location"]
    race_location = str(_venue).replace('"', '').replace("'", "").strip() if _venue else ""
    race_country  = str(race["country"]).replace('"', '').replace("'", "")

    # Augment race dict with fields the template accesses directly on `race`
    race["date"]          = race["race_date"]   # template uses race.date.strftime(...)
    race["athlete_count"] = finish_count
    for disc in ["overall", "swim", "bike", "run", "transition"]:
        race[f"{disc}_increase_athlete_id"] = best_perf[f"{disc}_athlete_id"] or 0

    template = "race_partial.html" if partial else "race.html"
    return templates.TemplateResponse(template, {
        "request":        request,
        "active_page":    "races",
        "race":           race,
        "race_location":  race_location,
        "race_country":   race_country,
        "event_id":       event_id,
        "event_races":    event_races,
        "finish_count":   finish_count,
        "dnf_count":      dnf_count,
        "ignored_info":          ignored_info,
        "race_standards":        race_standards,
        "race_standard_classes": race_standard_classes,
        "best_performances": best_performances,
        "splits_data":    splits_data,
        "ratings_data":   ratings_data,
        "overall_time_hist":       time_hists.get("overall", {}),
        "swim_time_hist":          time_hists.get("swim", {}),
        "bike_time_hist":          time_hists.get("bike", {}),
        "run_time_hist":           time_hists.get("run", {}),
        "t1_time_hist":            time_hists.get("t1", {}),
        "t2_time_hist":            time_hists.get("t2", {}),
        "overall_rating_hist":     rating_hists.get("overall", {}),
        "swim_rating_hist":        rating_hists.get("swim", {}),
        "bike_rating_hist":        rating_hists.get("bike", {}),
        "run_rating_hist":         rating_hists.get("run", {}),
        "transition_rating_hist":  rating_hists.get("transition", {}),
        "prediction_data":         prediction_data,
        "predictions":             predictions,
        "has_predictions":         predictions is not None,
        "race_distance":           race_distance,
        "course_conditions":       course_conditions if not ignored_info else None,
    })
