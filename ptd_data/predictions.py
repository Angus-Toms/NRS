"""Build-time precompute of race predictions and course conditions.

Predictions are a pure function of the weekly read-only DB (pre-race ratings,
form, prediction models, start lists), so they are computed once per build and
served straight from the race_predictions / race_course_conditions tables.
The anchor/form logic here previously lived in app/routers/race_page.py and
ran on every race-page request.

Covers:
  - completed elite races (predicted results table + pos diffs on race pages)
  - upcoming races with start lists (race page, event podium, athlete page)
  - course conditions, pooled per event across its elite races

Run as the build step after ratings: python3 -m ptd_data.predictions
"""

import math

import numpy as np
from tqdm import tqdm

from ptd_data import db, queries
from ptd_data.ratings import SCALE, YEAR_REF

DNF_STATUSES = {"DNF", "DNS", "DQ", "LAP", "NC"}
START_RATING = 1500
DISCS        = ['overall', 'swim', 'bike', 'run']  # transitions excluded - too course-specific

# History-window size by course. Tuned from a 300-race MAE sweep: short-course
# pros race often enough that N=10 smooths course-to-course variance; long-
# course pros race rarely, so a smaller N keeps the anchor on recent form.
ANCHOR_HISTORY_N   = {'short': 10, 'long': 5}
ANCHOR_MIN_SAMPLES = 2

# Top-K highest-rated athletes whose history is pooled to form the anchor.
# K=3 won the MAE sweep — pooling across multiple top athletes dilutes any
# one athlete's inconsistency without sacrificing rating-relevance. ELO still
# picks who the top-K are and the leader's rating still drives the ELO-
# scaling reference, so the rating system remains the backbone.
ANCHOR_TOP_K = 3

# Quantile of the pooled history used as the anchor. Short course:
# p50 (median) is already optimal — winner bias ~0. Long course: p50 is too
# slow for winners (long overall winners run +16m faster than the median,
# long run +9m faster) because the pool captures a top-3 athlete's *typical*
# performance, but on race day the actual winner usually pushes harder than
# their own personal median. p33 cuts ~3m (run) / ~7m (overall) of winner
# bias for only ~1–2m of full-field MAE cost. Trade-off validated on a 400-
# race sweep.
ANCHOR_POOL_QUANTILE = {'short': 0.50, 'long': 0.33}

_SHORT_DISTANCES = {'sprint', 'standard'}

# Predictions for athletes with fewer than this many prior elite starts (in the
# race's course bucket) are flagged low-confidence in the UI: their ELO rating
# hasn't converged, so the prediction is less reliable. Backtest: within-race
# ordering rho climbs from ~0.04 (0-2 starts) to ~0.42 by 5-7 and ~0.46 by 8-10
# (analysis/k_replay.py).
LOW_CONF_STARTS = 8

# Course-condition confidence: an athlete's diff contributes with weight
# min(1, prior_starts / CONF_THRESHOLD).
CONF_THRESHOLD = 10


def _anchor_time(field_ratings, leader_rating, distance, disc, model,
                 target_year=None, target_date=None):
    """Return the anchor time for this discipline across the whole field.

    Strategy: pool the last-N finishes of the top-K highest-rated athletes
    at this distance/discipline (strictly before `target_date` if given),
    take a course-specific quantile. The field is still ELO-scaled from the
    leader's rating. Falls back to the population quantile model if the
    pool is too sparse.

    `target_date` avoids post-target history leaking into the pool on
    historical-race displays — old-era races otherwise draw from modern tech
    times and over-predict speed.

    `target_year` is used by the long-course population model's year term to
    adjust fallback predictions for era drift (tech, training). Only fires
    for long-course races; short-course models store year_coef=0.
    """
    if disc in DISCS:
        course        = 'short' if distance in _SHORT_DISTANCES else 'long'
        n_per_athlete = ANCHOR_HISTORY_N[course]
        quantile      = ANCHOR_POOL_QUANTILE[course]
        topK = sorted(field_ratings, key=field_ratings.get, reverse=True)[:ANCHOR_TOP_K]
        pool = []
        for aid in topK:
            pool.extend(queries.get_athlete_discipline_history(
                aid, distance, disc, limit=n_per_athlete, before_date=target_date,
            ))
        if len(pool) >= ANCHOR_MIN_SAMPLES:
            return float(np.quantile(pool, quantile))
    # Population-model fallback. Year term is stored 0 for short-course models,
    # so this formula is a no-op on short course. For long course with a known
    # target year, it adjusts old predictions slower to account for era drift.
    # Clamped to offset ≤ 0: the long-course year_coef fits a multi-decade
    # trend that extrapolates too aggressively into the recent plateau. So we
    # only apply the year term when the target year is earlier than YEAR_REF.
    year_offset = min(target_year - YEAR_REF, 0) if target_year is not None else 0.0
    return (model['slope'] * leader_rating
            + model.get('year_coef', 0.0) * year_offset
            + model['intercept'])


def _apply_form_overrides(preds, form_map, c_map, field, discs):
    """Replace anchor-based predicted times with form-based ones on long
    course: exp(pre-race form + event course constant) per discipline.

    Athletes without enough form history (debuts, <3 prior splits) keep the
    anchor prediction, but rescaled onto the form level - the two models'
    absolute levels aren't mutually calibrated, and mixing them raw scrambles
    the cross-group ordering (full-field Spearman drops ~0.07).
    """
    for disc in discs:
        c = c_map.get(disc)
        if c is None:
            continue
        form_t = {aid: math.exp(f[disc] + c)
                  for aid, f in form_map.items() if disc in f}
        if not form_t:
            continue
        ratios = sorted(t / preds[aid][disc] for aid, t in form_t.items()
                        if preds.get(aid, {}).get(disc))
        scale = ratios[len(ratios) // 2] if ratios else 1.0
        for aid in field:
            if aid in form_t:
                preds.setdefault(aid, {})[disc] = max(0, round(form_t[aid]))
            elif preds.get(aid, {}).get(disc):
                preds[aid][disc] = max(0, round(preds[aid][disc] * scale))


def _completed_race_preds(race, results, models):
    """Predicted seconds for a completed race's field.

    Returns (anchor_preds, final_preds, distance, pre_ratings) or None when
    the distance is unclassifiable or no models exist. anchor_preds is the
    pure ELO-anchor version (feeds course-condition signals, matching the old
    _course_signal_for_race); final_preds additionally carries the long-course
    form override and is what gets stored/displayed.
    """
    race_id  = race['race_id']
    distance = queries.get_race_distance_type(race_id)
    if not distance:
        return None

    pre_ratings = {r['athlete_id']: r for r in queries.get_race_pre_race_ratings(race_id)}
    gender      = race['gender']
    target_date = race.get('race_date')
    target_year = target_date.year if target_date else None

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

        leader_rating = max(field_ratings.values())
        anchor        = _anchor_time(field_ratings, leader_rating, distance, disc, m,
                                     target_year=target_year, target_date=target_date)
        for aid, rating in field_ratings.items():
            t = anchor * (10 ** ((leader_rating - rating) / SCALE))
            preds.setdefault(aid, {})[disc] = max(0, round(t))

    if not preds:
        return None

    anchor_preds = {aid: dict(p) for aid, p in preds.items()}

    # Short course: pure ELO anchor for the whole field. The observed-split
    # blend was retired - it over-fit thin records (a few fast splits against
    # weak fields could vault a low-start athlete to the front). Long course:
    # the form model still beats the anchor outright, so override with it.
    field = tuple(sorted(r['athlete_id'] for r in results))
    if distance not in _SHORT_DISTANCES:
        form_map = queries.get_field_form(field, 'long', before_date=target_date)
        c_map = queries.get_form_course_constants(
            race.get('event_id'), gender, distance, target_date)
        _apply_form_overrides(preds, form_map, c_map, field, DISCS)

    return anchor_preds, preds, distance, pre_ratings


def _course_signal(results, pre_ratings, anchor_preds):
    """Per-discipline normalised course-condition signal for one race.

    Returns (signal, total_w, avg_pred_overall) or None if insufficient data.
      signal: disc -> avg(predicted - actual) / avg_predicted_overall (dimensionless %)
      total_w: sum of confidence weights (weights this race when pooling)
      avg_pred_overall: weighted-avg predicted overall seconds (display conversion)

    Uses the anchor-based predictions so the "expected" baseline judging
    course speed matches the ELO-scaled times, same as the old
    _course_signal_for_race.
    """
    def _w(aid):
        pr = pre_ratings.get(aid)
        if pr is None:
            return 0.0
        return min(1.0, (pr.get('prior_starts', 0) or 0) / CONF_THRESHOLD)

    finishers = [r for r in results
                 if r['status'] not in DNF_STATUSES and r['athlete_id'] in anchor_preds]
    pow_w = [(anchor_preds[r['athlete_id']]['overall'], _w(r['athlete_id']))
             for r in finishers if anchor_preds[r['athlete_id']].get('overall')]
    total_w = sum(w for _, w in pow_w)
    if total_w == 0:
        return None
    avg_pred_overall = sum(p * w for p, w in pow_w) / total_w

    signal = {}
    for disc in DISCS:
        tk = f'{disc}_s'
        diffs = [(anchor_preds[r['athlete_id']][disc] - r[tk], _w(r['athlete_id']))
                 for r in finishers
                 if anchor_preds[r['athlete_id']].get(disc) and (r.get(tk) or 0) > 0
                 and _w(r['athlete_id']) > 0]
        if len(diffs) < 3:
            continue
        tdw = sum(w for _, w in diffs)
        signal[disc] = sum(d * w for d, w in diffs) / tdw / avg_pred_overall

    if not signal:
        return None
    return signal, total_w, avg_pred_overall


def _upcoming_race_preds(race, entries, models):
    """Predicted seconds {athlete_id: {disc: sec}} for an upcoming race from
    current ratings - anchor, then long-course form override. Returns
    (preds, distance) or (None, None) when the distance is unclassifiable or
    no models exist."""
    distance = queries.get_upcoming_race_distance_type(race['race_id'])
    if not distance:
        return None, None

    gender      = race['gender']
    target_year = race['race_date'].year if race.get('race_date') else None

    current_ratings = {
        e['athlete_id']: {
            'overall': e['overall_rating'] or START_RATING,
            'swim':    e['swim_rating']    or START_RATING,
            'bike':    e['bike_rating']    or START_RATING,
            'run':     e['run_rating']     or START_RATING,
        }
        for e in entries
    }

    preds = {}
    for disc in DISCS:
        m = models.get((gender, distance, disc))
        if not m:
            continue
        field_ratings = {aid: cr[disc] for aid, cr in current_ratings.items()}
        leader_rating = max(field_ratings.values())
        anchor        = _anchor_time(field_ratings, leader_rating, distance, disc, m,
                                     target_year=target_year)
        for aid, rating in field_ratings.items():
            t = anchor * (10 ** ((leader_rating - rating) / SCALE))
            preds.setdefault(aid, {})[disc] = max(0, round(t))

    if not preds:
        return None, None

    # Current form for upcoming races (no date cutoff - there is no future to leak).
    field = tuple(sorted(e['athlete_id'] for e in entries))
    if distance not in _SHORT_DISTANCES:
        form_map = queries.get_field_form(field, 'long')
        c_map = queries.get_form_course_constants(
            race.get('event_id'), gender, distance, race['race_date'])
        _apply_form_overrides(preds, form_map, c_map, field, DISCS)
    return preds, distance


def _prediction_rows(race_id, preds, ordered_ids, course, target_date=None):
    """race_predictions tuples for one race. ordered_ids is the field in
    results/start-list order so ties (identical predicted times, e.g. a group
    of debuts) keep that order under the stable sort, matching the old
    request-time behaviour."""
    field = tuple(sorted(ordered_ids))
    start_counts = queries.get_field_start_counts(field, course, before_date=target_date)
    order = sorted(ordered_ids, key=lambda a: preds.get(a, {}).get('overall', 9_999_999))
    rows = []
    for i, aid in enumerate(order):
        p = preds.get(aid, {})
        rows.append((race_id, aid, i + 1,
                     p.get('overall', 0), p.get('swim', 0), p.get('bike', 0), p.get('run', 0),
                     start_counts.get(aid, 0) < LOW_CONF_STARTS))
    return rows


def rebuild(conn=None):
    """Wipe and repopulate race_predictions + race_course_conditions."""
    if conn is None:
        conn = db.get_conn()
    queries.use_connection(conn)

    models = queries.get_prediction_models()
    if not models:
        raise RuntimeError("prediction_models is empty - run the ratings step first")

    pred_rows = []
    cond_rows = []

    # --- completed elite races, grouped by event so course conditions pool once ---
    events = conn.execute("""
        SELECT event_id, list(race_id ORDER BY race_id)
        FROM races
        WHERE distance != 'relay'
        GROUP BY event_id
    """).fetchall()

    for event_id, race_ids in tqdm(events, desc="completed races"):
        elite_ids = [rid for rid in race_ids if queries.get_race_category(rid) == 'elite']
        pooled = {}             # disc -> [(pct_signal, total_w)]
        pred_overall_refs = []  # (avg_pred_overall, total_w)

        for rid in elite_ids:
            race    = queries.get_race_info(rid)
            results = queries.get_race_results(rid)
            if not results:
                continue
            out = _completed_race_preds(race, results, models)
            if not out:
                continue
            anchor_preds, final_preds, distance, pre_ratings = out

            course = 'short' if distance in _SHORT_DISTANCES else 'long'
            pred_rows.extend(_prediction_rows(
                rid, final_preds, [r['athlete_id'] for r in results],
                course, target_date=race.get('race_date')))

            sig = _course_signal(results, pre_ratings, anchor_preds)
            if sig:
                signal, total_w, avg_pred_overall = sig
                pred_overall_refs.append((avg_pred_overall, total_w))
                for disc, pct in signal.items():
                    pooled.setdefault(disc, []).append((pct, total_w))

        # Pool signals across the event's elite races; a combined reference
        # time makes all races in the event display identical conditions.
        combined_ref_w = sum(w for _, w in pred_overall_refs)
        if not combined_ref_w:
            continue
        combined_avg_pred_overall = sum(p * w for p, w in pred_overall_refs) / combined_ref_w
        for disc in DISCS:
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
            for rid in elite_ids:
                cond_rows.append((rid, disc, avg_diff_s, category))

    # --- upcoming races with start lists ---
    # Stored for every upcoming race regardless of category: the athlete page
    # shows predictions without an elite gate; race/event pages keep theirs.
    upcoming_ids = [r[0] for r in conn.execute("SELECT race_id FROM upcoming_races").fetchall()]
    for rid in tqdm(upcoming_ids, desc="upcoming races"):
        race    = queries.get_upcoming_race_info(rid)
        entries = queries.get_upcoming_race_entries(rid)
        if not entries:
            continue
        preds, distance = _upcoming_race_preds(race, entries, models)
        if not preds:
            continue
        course = 'short' if distance in _SHORT_DISTANCES else 'long'
        pred_rows.extend(_prediction_rows(
            rid, preds, [e['athlete_id'] for e in entries], course))

    conn.execute("DELETE FROM race_predictions")
    conn.execute("DELETE FROM race_course_conditions")
    conn.executemany(
        "INSERT INTO race_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", pred_rows)
    if cond_rows:
        conn.executemany(
            "INSERT INTO race_course_conditions VALUES (?, ?, ?, ?)", cond_rows)

    n_races = len({r[0] for r in pred_rows})
    n_cond  = len({r[0] for r in cond_rows})
    print(f"race_predictions: {len(pred_rows)} rows across {n_races} races")
    print(f"race_course_conditions: {len(cond_rows)} rows across {n_cond} races")


if __name__ == "__main__":
    rebuild()
