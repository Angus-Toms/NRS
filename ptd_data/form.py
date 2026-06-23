"""Athlete form model: discipline-level form from split history.

Each finishing split is converted to rel = ln(split / race_median_split),
removing course length and conditions to first order. The remaining field-
strength confound is removed by alternating least squares (athlete form vs
per-race adjustment). A local-level Kalman filter tracks each athlete's form
through the adjusted observations - process variance grows with days since
their last race - and the posterior is blended with the rating-implied form
(discipline Elo mapped linearly into rel space).

Validated in analysis/{form_model,model_compare,form_only_eval}.py: on long
course the form model beats the winner-anchored prediction pipeline on both
ordering and outright times, so long-course race predictions read from here.
Short-course race predictions do NOT use form - they use observed splits
directly (race_page._apply_short_course); short course only computes swim/run
form, for the athlete-page form display.

Weak, poorly-connected tiers (Development Regional Cups) get their course
constant anchored to the neutral baseline (see compute_form), since ALS can't
gauge their field strength and would otherwise leave their form inflated.

Two tables, rebuilt by compute_form() during the ratings 'models' phase:

  athlete_form         (race_id, athlete_id, discipline, form_rel, n_obs)
      blended form *after* that race. Pre-race form for race R = the latest
      row strictly before R's date (same pattern as the ratings table), so
      historical race pages stay leakage-free. Latest row = current form.
  form_race_constants  (race_id, discipline, c)
      course constant C = ln(median_split) + field adjustment. Predicted
      split = exp(form_rel + C_est), with C_est estimated from the event's
      past editions (see queries.get_form_course_constants).

Kalman/blend hyperparameters are frozen from the analysis tuning runs
(June 2026); the cheap parts (ALS, per-tier observation noise, Elo map) are
refit on every compute.
"""

import json
import math
import statistics
from collections import defaultdict

DATA_START = '2012-01-01'
MIN_FIELD  = 10   # valid splits a race needs to define a median
MIN_PRIOR  = 3    # observations before a form value is usable downstream
ALS_ITERS  = 3

COURSE_DISTANCES = {'short': ('sprint', 'standard'),
                    'long':  ('middle', 't100', 'long')}
# Long course computes all four (they drive long-course race predictions).
# Short course only needs swim/run, for the athlete-page form display - short
# race predictions use observed splits, not form (race_page._apply_short_course).
COURSE_DISCS = {'short': ('swim', 'run'),
                'long':  ('overall', 'swim', 'bike', 'run')}

# (q_per_day, r_default, blend_weight) per (course, discipline), tuned on
# pre-2024 data in analysis/form_model.py / model_compare.py.
PARAMS = {
    ('short', 'swim'):    (1e-6, 2e-3, 0.7),
    ('short', 'run'):     (1e-6, 2e-3, 0.7),
    ('long', 'overall'):  (1e-6, 2e-3, 0.9),
    ('long', 'swim'):     (1e-6, 2e-3, 0.9),
    ('long', 'bike'):     (1e-6, 2e-3, 0.9),
    ('long', 'run'):      (2e-7, 5e-4, 0.7),
}

# sane split windows (seconds): outside these the timing is broken
BOUNDS = {
    'overall': {'sprint': (2400, 5400), 'standard': (5100, 9900),
                'middle': (10000, 21600), 't100': (9600, 18000), 'long': (24000, 45000)},
    'swim':    {'sprint': (240, 1500), 'standard': (600, 3600),
                'middle': (1200, 3600), 't100': (900, 3600), 'long': (2400, 6000)},
    'bike':    {'sprint': (1200, 3600), 'standard': (2400, 5400),
                'middle': (6000, 12600), 't100': (5400, 10800), 'long': (12000, 25200)},
    'run':     {'sprint': (480, 2400), 'standard': (1800, 5400),
                'middle': (3600, 7200), 't100': (2700, 7200), 'long': (8400, 21600)},
}

# WT category ids -> tier, for per-tier Kalman observation noise. Long course
# has no WT tier structure; the distance itself buckets the noise.
TIER_RULES = [({348, 351}, 'WTCS'), ({343, 346}, 'Games'), ({349}, 'World Cup'),
              ({340}, 'Continental Champs'), ({341}, 'Continental Cup'),
              ({477}, 'Development')]

# Tiers whose fields are too weak and too poorly connected for ALS to gauge
# their strength. Their course constant C is anchored to the neutral baseline
# in compute_form (see the tier-anchor step) rather than left at the weak
# field's slow median, which would inflate the form of anyone who races them.
WEAK_TIERS = {'Development'}


def _tier_for(cat_ids_json):
    try:
        cats = set(json.loads(cat_ids_json or '[]'))
    except json.JSONDecodeError:
        return 'other'
    for ids, name in TIER_RULES:
        if cats & ids:
            return name
    return 'other'


def _load_observations(conn, disc, course):
    """Chronological (athlete_id, race_id, date, tier, rel) with corrections
    applied, plus race_id -> ln(median_split). rel = ln(split / median)."""
    col = f'{disc}_s'
    dist_in = ','.join(f"'{d}'" for d in COURSE_DISTANCES[course])
    rows = conn.execute(f"""
        WITH corr AS (
            SELECT race_id, athlete_id,
                   COALESCE(MAX(value) FILTER (WHERE source='manual'),
                            MAX(value) FILTER (WHERE source='auto')) AS value
            FROM corrections WHERE discipline = ?
            GROUP BY race_id, athlete_id
        )
        SELECT res.athlete_id, res.race_id, r.race_date, r.distance, r.cat_ids,
               COALESCE(c.value, res.{col}) AS split
        FROM results res
        JOIN races r ON res.race_id = r.race_id
        LEFT JOIN corr c ON c.race_id = res.race_id AND c.athlete_id = res.athlete_id
        WHERE r.category = 'elite'
          AND r.distance IN ({dist_in})
          AND r.gender IN ('male', 'female')
          AND r.race_date >= ?
          AND res.status = 'Finished'
          AND COALESCE(c.value, res.{col}) > 0
          AND r.race_id NOT IN (SELECT race_id FROM ignored_races)
        ORDER BY r.race_date
    """, [disc, DATA_START]).fetchall()

    by_race = defaultdict(list)
    for aid, rid, d, dist, cat_ids, split in rows:
        lo, hi = BOUNDS[disc][dist]
        if lo <= split <= hi:
            by_race[rid].append((aid, d, dist, cat_ids, split))

    obs, log_median = [], {}
    for rid, entries in by_race.items():
        if len(entries) < MIN_FIELD:
            continue
        med = statistics.median(e[4] for e in entries)
        tier = entries[0][2] if course == 'long' else _tier_for(entries[0][3])
        log_median[rid] = math.log(med)
        for aid, d, dist, _, split in entries:
            rel = math.log(split / med)
            if abs(rel) < 0.30:
                obs.append((aid, rid, d, tier, rel))
    obs.sort(key=lambda o: (o[2], o[1]))
    return obs, log_median


def compute_form(conn):
    """Rebuild athlete_form and form_race_constants from scratch."""
    conn.execute("DELETE FROM athlete_form")
    conn.execute("DELETE FROM form_race_constants")

    for course, discs in COURSE_DISCS.items():
        for disc in discs:
            obs, log_median = _load_observations(conn, disc, course)
            if not obs:
                continue
            q, r_default, blend_w = PARAMS[(course, disc)]

            # ALS: iterate athlete form (median of adjusted obs) and per-race
            # field adjustment (median of rel - form over the race's field)
            form = defaultdict(float)
            race_adj = defaultdict(float)
            by_athlete_idx = defaultdict(list)
            by_race_idx = defaultdict(list)
            for i, (aid, rid, d, tier, rel) in enumerate(obs):
                by_athlete_idx[aid].append(i)
                by_race_idx[rid].append(i)
            rels = [o[4] for o in obs]
            for _ in range(ALS_ITERS):
                for aid, idxs in by_athlete_idx.items():
                    form[aid] = statistics.median(rels[i] - race_adj[obs[i][1]] for i in idxs)
                for rid, idxs in by_race_idx.items():
                    race_adj[rid] = statistics.median(rels[i] - form[obs[i][0]] for i in idxs)

            # Tier-anchor weak, poorly-connected fields. Development Regional
            # Cups are an isolated cluster, so ALS can't gauge their field
            # strength: race_adj stays ~0 and C sits at the weak field's slow
            # median, which inflates the form of anyone who races them. Anchor
            # their C to the neutral baseline (median C of the well-connected
            # non-weak races) so form measures absolute pace, comparable across
            # tiers. (Pace is ~tier-independent, especially on the run.)
            race_tier = {rid: obs[idxs[0]][3] for rid, idxs in by_race_idx.items()}
            well = [log_median[rid] + race_adj[rid] for rid in by_race_idx
                    if race_tier[rid] not in WEAK_TIERS]
            if well:
                neutral = statistics.median(well)
                for rid in by_race_idx:
                    if race_tier[rid] in WEAK_TIERS:
                        race_adj[rid] = neutral - log_median[rid]

            # per-athlete chronological sequences of adjusted observations
            by_athlete = defaultdict(list)  # aid -> [(date, tier, rid, adj_rel)]
            for aid, rid, d, tier, rel in obs:
                by_athlete[aid].append((d, tier, rid, rel - race_adj[rid]))

            # per-tier observation noise from one-step-ahead residuals
            resid = defaultdict(list)
            for aid, seq in by_athlete.items():
                m = P = None
                last_d = None
                for d, tier, rid, v in seq:
                    if m is None:
                        m, P, last_d = v, r_default, d
                        continue
                    P += q * (d - last_d).days
                    resid[tier].append((m - v) ** 2)
                    K = P / (P + r_default)
                    m += K * (v - m)
                    P *= (1 - K)
                    last_d = d
            r_by_tier = {t: statistics.fmean(v) for t, v in resid.items()
                         if len(v) > 300}

            # rating history for the Elo-implied form component
            dist_in = ','.join(f"'{d}'" for d in COURSE_DISTANCES[course])
            rat_rows = conn.execute(f"""
                SELECT ra.athlete_id, r.race_date, ra.{disc}
                FROM ratings ra
                JOIN races r ON ra.race_id = r.race_id
                WHERE r.distance IN ({dist_in}) AND ra.{disc} IS NOT NULL
                  AND ra.category = 'elite'
                ORDER BY ra.athlete_id, r.race_date
            """).fetchall()
            rat_hist = defaultdict(list)
            for aid, d, rating in rat_rows:
                rat_hist[aid].append((d, rating))

            def rating_at(aid, date):
                best = None
                for d, rating in rat_hist.get(aid, []):
                    if d > date:
                        break
                    best = rating
                return best

            # Elo -> rel map: OLS over observations with a current rating
            pairs = []
            for aid, seq in by_athlete.items():
                for d, tier, rid, v in seq:
                    rt = rating_at(aid, d)
                    if rt is not None:
                        pairs.append((rt, v))
            mx = statistics.fmean(p[0] for p in pairs)
            my = statistics.fmean(p[1] for p in pairs)
            sxx = sum((x - mx) ** 2 for x, _ in pairs)
            slope = sum((x - mx) * (y - my) for x, y in pairs) / sxx

            # Kalman posteriors per observation, blended with Elo-implied form
            form_rows = []
            for aid, seq in by_athlete.items():
                m = P = None
                last_d = None
                for n, (d, tier, rid, v) in enumerate(seq, 1):
                    if m is None:
                        m, P = v, r_default
                    else:
                        P += q * (d - last_d).days
                        r = r_by_tier.get(tier, r_default)
                        K = P / (P + r)
                        m += K * (v - m)
                        P *= (1 - K)
                    last_d = d
                    rt = rating_at(aid, d)
                    blended = (blend_w * m + (1 - blend_w) * (my + slope * (rt - mx))
                               if rt is not None else m)
                    form_rows.append((rid, aid, disc, blended, n))

            conn.executemany(
                "INSERT INTO athlete_form (race_id, athlete_id, discipline, form_rel, n_obs) "
                "VALUES (?, ?, ?, ?, ?)", form_rows)
            conn.executemany(
                "INSERT INTO form_race_constants (race_id, discipline, c) VALUES (?, ?, ?)",
                [(rid, disc, lm + race_adj[rid]) for rid, lm in log_median.items()])
            print(f"  form: {course}/{disc}: {len(form_rows)} observations, "
                  f"{len(log_median)} race constants")
