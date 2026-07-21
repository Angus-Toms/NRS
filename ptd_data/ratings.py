"""
Computes ELO ratings and world/national rankings from race results in DuckDB.

Processes all races chronologically, computes pairwise log-time-ratio ELO
across 5 disciplines (overall, swim, bike, run, transition) with Glicko-style
confidence weighting, then computes world and national rankings per gender.

Confidence model:
  - Each athlete tracks a race count. Confidence = min(1, race_count / CONF_THRESHOLD).
  - Self-K multiplier: new athletes move faster (2.5× at race 0, linearly decaying to 1× over CONF_THRESHOLD races).
  - Opponent weight: pairings against established athletes count more than against newcomers.

Usage:
    python -m ptd_data.ratings
"""

import math
from datetime import timedelta

import numpy as np
from tqdm import tqdm

ACTIVE_WINDOW_DAYS = int(18 * 30.44)  # ~18 months

from ptd_data import db

SCALE = 46175.8
CONF_THRESHOLD = 10  # races to reach full confidence
START_RATING = 1500

# Race standards = exp-decay weighted average of pre-race ratings of the top
# STANDARD_POS_CAP finishers. Below STANDARD_FLOOR finishers we keep dividing
# by the FLOOR-size denominator, so thin fields take a haircut proportional to
# how short they are (penalises 2-athlete exhibition races etc.).
STANDARD_K = 0.1
STANDARD_FLOOR = 10
STANDARD_POS_CAP = 25
_STANDARD_WEIGHTS = [math.exp(-STANDARD_K * i) for i in range(STANDARD_POS_CAP)]
# _STANDARD_DENOM[n] = sum of weights for positions 1..n (i.e. first n entries).
_STANDARD_DENOM = [sum(_STANDARD_WEIGHTS[:n]) for n in range(STANDARD_POS_CAP + 1)]


def standard_denom(n_finishers):
    """Denominator for the race-standard weighted average: clamps field size into [FLOOR, POS_CAP]."""
    return _STANDARD_DENOM[min(max(n_finishers, STANDARD_FLOOR), STANDARD_POS_CAP)]

# Prediction-model fit parameters. We want the stored slope/intercept to map
# leader-rating -> leader-time, so we fit a low quantile of the (time, rating)
# cloud rather than the mean. IRLS re-weights each iteration by 1/|residual|
# scaled by the asymmetric quantile weight (tau above the fit, 1-tau below).
#
# tau is course-specific: short-course fields are tight (p10..p50 gap ~40s) and
# p10 over-predicts speed, so we use p20 to land near actual winning times.
# Long-course spreads are wider (~4-5 min p10..p50 gap at elite ratings), where
# p10 matches what winners actually run. Values were fit by eye against known
# recent elite benchmarks (Yee 5k ~14:20, Potter 750m swim ~9:00, Laidlow 70.3
# run ~70 min).
QUANTILE_TAU_BY_COURSE = {'short': 0.20, 'long': 0.10}
QUANTILE_MAX_ITER      = 100
QUANTILE_EPS           = 1.0    # seconds; floor on |residual| to keep IRLS weights finite

# Reference year for the year-term regression (long course only). year_coef
# × (target_year − YEAR_REF) adjusts the predicted time for era drift —
# modern long-course bike is faster than historical due to equipment and
# training advances. Predicting 2020 races uses no year adjustment; earlier
# years shift toward the historical average, later years toward modern.
YEAR_REF = 2020

# Discipline indices: overall=0, swim=1, bike=2, run=3, transition=4
N_DISCIPLINES = 5

# Per-course ELO parameters. Long course gets a higher K because athletes race
# far less frequently — fewer updates per year means each race should move the
# rating more to keep the model responsive. K values raised ~1.4× from the
# previous 36/54 based on an empirical calibration check: log₁₀(time_b/time_a)
# × SCALE consistently overshot the stored rating gap by a factor of 1.3-1.5
# for athletes with <30 career races, meaning the system was converging too
# slowly. The 30+ career-race asymptote was already correct, so the SCALE
# constant itself (46,175.8) stays unchanged — we just move ratings faster
# toward their asymptote.
COURSES = {
    'short': {'distances': ('sprint', 'standard', 'relay'), 'k_factor': 50},
    'long':  {'distances': ('middle', 't100',   'long'),    'k_factor': 76},
}

# Mixed team relay legs feed the short-course athlete ELO. Athletes are paired
# only within (leg_num, gender): the pure positional offset between same-gender
# legs is large (leg 3 runs ~2.6% slower than leg 1, ~508 rating pts; leg 4
# ~1.7% slower than leg 2), so cross-leg pooling would bias every leg-1 athlete
# upward. Within-leg keeps the comparison pool small, so K is left at 1.0 (no
# damping) to offset the few comparisons per race; revisit once real results
# are in (leg-offset-corrected pooling is the alternative if convergence is
# too slow).
RELAY_K_MULT = 1.0

# Era-scaled K. In the early years of the sport a season had only a handful of
# elite races, so athletes had far fewer opportunities per year to accumulate
# ELO — the 2000 Sydney Olympics winner ended up "rated" near START_RATING
# because the pool simply hadn't had enough ELO exchanges yet. To compensate,
# scale K by inverse-sqrt of the course's annual race volume (relative to the
# peak year). Cap at ERA_K_MAX_MULT so single wins in ultra-sparse years
# (e.g. 2 long-course races in 1988) don't swing ratings by hundreds of
# points. Smooth with a 3-year rolling window so one-off dips (COVID 2020)
# don't spuriously inflate K.
ERA_K_MAX_MULT    = 3.0
ERA_K_ROLLING_YRS = 3


def _era_k_multiplier_table(conn, course_distances):
    """Return {year: k_multiplier} for a course's distances.

    Multiplier is capped at ERA_K_MAX_MULT and floored at 1.0 (we only ever
    boost K for old/sparse eras, never dampen it for modern ones).

    The current calendar year is excluded from baseline/smoothing calculations
    (its race count is always incomplete) and instead inherits the prior full
    year's multiplier — otherwise a partial 2026 with 52 races gets treated
    like a sparse era and spuriously boosts current-year K.
    """
    import datetime as _dt
    current_year = _dt.date.today().year

    rows = conn.execute(f"""
        SELECT EXTRACT(YEAR FROM race_date)::INTEGER AS yr, COUNT(*) AS cnt
        FROM races
        WHERE category = 'elite' AND sub_category = 'elite'
          AND distance IN {_in_sql(course_distances)}
        GROUP BY yr
    """).fetchall()
    by_year = dict(rows)
    full_years = {y: v for y, v in by_year.items() if y < current_year}
    if not full_years:
        return {}

    years     = sorted(full_years)
    raw_vols  = [full_years[y] for y in years]
    half_win  = ERA_K_ROLLING_YRS // 2
    smoothed  = {}
    for i, y in enumerate(years):
        window = raw_vols[max(0, i - half_win): i + half_win + 1]
        smoothed[y] = sum(window) / len(window)
    baseline = max(smoothed.values())
    table = {
        y: max(1.0, min(ERA_K_MAX_MULT, (baseline / max(smoothed[y], 1)) ** 0.5))
        for y in years
    }
    # Current year inherits the most recent full year's multiplier (typically
    # ~1.0 for modern eras). Avoids a spurious boost from incomplete volume.
    if current_year in by_year:
        table[current_year] = table[years[-1]]
    return table


def _in_sql(values):
    """Build a SQL IN-clause literal from a tuple of strings."""
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def _confidence(race_count):
    """0..1 confidence based on how many races an athlete has completed."""
    return min(1.0, race_count / CONF_THRESHOLD)


def _self_k_mult(race_count):
    """Higher K multiplier for new athletes - 2.5× at race 0, linearly decaying to 1× by race CONF_THRESHOLD.

    Lowered from 3× after a backtest (analysis/k_replay.py): the old 3× boost
    overshot on thin evidence - it over-ranked genuine debutants and inflated
    young risers' ratings faster than results justified. 2.5× keeps convergence
    responsive while trimming that overshoot (full-field ordering rho 0.311 ->
    0.321; debut over-ranking eased)."""
    return 1.0 + 1.5 * max(0.0, 1.0 - race_count / CONF_THRESHOLD)


# ---------------------------------------------------------------------------
# Phase 1: ELO ratings
# ---------------------------------------------------------------------------

def _compute_ratings(conn, category, course, clear=True):
    """Process all races chronologically for one (category, course), compute confidence-weighted pairwise ELO.

    Ratings are tracked independently per course: an athlete's short-course
    trajectory does not influence their long-course trajectory (different
    skillsets, different race densities).

    With clear=False, find the earliest unprocessed race (with >=2 results) in
    this (category, course), wipe all ratings rows from that date onward, then
    rebuild from there. Handles late-arriving backdated results cleanly: their
    date sets the cutoff and everything downstream gets recomputed.
    """
    ignored = set(
        r[0] for r in conn.execute("SELECT race_id FROM ignored_races").fetchall()
    )

    distances = COURSES[course]['distances']
    in_sql    = _in_sql(distances)
    k_factor  = COURSES[course]['k_factor']
    # Era K is calibrated on individual race volume; relay races are a side
    # channel and shouldn't shift the historical multipliers.
    era_k_mult = _era_k_multiplier_table(
        conn, tuple(d for d in distances if d != 'relay'))

    # Relay races live in relay_legs, not results, and pair only within a
    # (leg_num, gender) group. Genders come from the athletes table since a
    # relay race row is 'mixed'.
    relay_ids = set()
    athlete_gender = {}
    if 'relay' in distances:
        relay_ids = set(r[0] for r in conn.execute(
            "SELECT race_id FROM races WHERE distance = 'relay' AND category = ?",
            [category]).fetchall())
        athlete_gender = dict(conn.execute(
            "SELECT athlete_id, gender FROM athletes").fetchall())

    current_ratings = {}   # athlete_id -> [overall, swim, bike, run, transition]
    race_counts = {}       # athlete_id -> number of races completed so far
    processed_race_ids = set()

    if not clear:
        cutoff_date = conn.execute(f"""
            SELECT MIN(r.race_date)
            FROM races r
            JOIN (SELECT race_id FROM results GROUP BY race_id HAVING COUNT(*) >= 2
                  UNION
                  SELECT race_id FROM relay_legs GROUP BY race_id HAVING COUNT(*) >= 2) res
              ON res.race_id = r.race_id
            WHERE r.category = ? AND r.distance IN {in_sql}
              AND r.race_id NOT IN (SELECT race_id FROM ratings WHERE category = ?)
              AND r.race_id NOT IN (SELECT race_id FROM ignored_races)
        """, [category, category]).fetchone()[0]

        if cutoff_date is None:
            print(f"  {category} {course}: no new races to process")
            return

        conn.execute(f"""
            DELETE FROM ratings
            WHERE category = ? AND race_id IN (
                SELECT race_id FROM races
                WHERE distance IN {in_sql} AND race_date >= ?
            )
        """, [category, cutoff_date])
        print(f"  {category} {course}: extending from {cutoff_date}")

        for athlete_id, overall, swim, bike, run, transition in conn.execute(f"""
            SELECT DISTINCT ON (ra.athlete_id)
                   ra.athlete_id, ra.overall, ra.swim, ra.bike, ra.run, ra.transition
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE ra.category = ? AND r.distance IN {in_sql}
            ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
        """, [category]).fetchall():
            current_ratings[athlete_id] = [overall, swim, bike, run, transition]

        for athlete_id, cnt in conn.execute(f"""
            SELECT ra.athlete_id, COUNT(*) FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE ra.category = ? AND r.distance IN {in_sql}
            GROUP BY ra.athlete_id
        """, [category]).fetchall():
            race_counts[athlete_id] = cnt

        processed_race_ids = set(r[0] for r in conn.execute(f"""
            SELECT DISTINCT ra.race_id FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE ra.category = ? AND r.distance IN {in_sql}
        """, [category]).fetchall())

    races = conn.execute(f"""
        SELECT race_id, EXTRACT(YEAR FROM race_date)::INTEGER AS yr, race_date
        FROM races
        WHERE category = ? AND distance IN {in_sql}
        ORDER BY race_date, race_id
    """, [category]).fetchall()

    for race_id, race_year, _ in tqdm(races, desc=f"Computing {category} {course} ratings", unit="race"):
        if race_id in ignored or race_id in processed_race_ids:
            continue

        if race_id in relay_ids:
            # Mixed relay: pair athletes only within the same (leg_num, gender)
            # group - legs differ in length by position, and cross-gender
            # comparisons are meaningless. Damped K (see RELAY_K_MULT).
            legs = conn.execute("""
                SELECT athlete_id, leg_num, leg_s, swim_s, bike_s, run_s, t1_s, t2_s
                FROM relay_legs WHERE race_id = ?
            """, [race_id]).fetchall()

            athlete_data = {}
            leg_groups = {}
            for athlete_id, leg_num, leg_s, swim_s, bike_s, run_s, t1_s, t2_s in legs:
                gender = athlete_gender.get(athlete_id)
                if gender is None:
                    continue
                if athlete_id not in current_ratings:
                    current_ratings[athlete_id] = [float(START_RATING)] * N_DISCIPLINES
                    race_counts[athlete_id] = 0
                transition_s = (t1_s + t2_s) if t1_s > 0 and t2_s > 0 else 0.0
                times = [leg_s, swim_s, bike_s, run_s, transition_s]
                entry = (current_ratings[athlete_id][:], times, race_counts[athlete_id])
                athlete_data[athlete_id] = entry
                leg_groups.setdefault((leg_num, gender), {})[athlete_id] = entry

            k_eff = k_factor * era_k_mult.get(race_year, 1.0) * RELAY_K_MULT
            elo_changes = {}
            for group in leg_groups.values():
                if len(group) >= 2:
                    elo_changes.update(_pairwise_elo(group, k_eff))

            rating_rows = []
            for athlete_id, deltas in elo_changes.items():
                old = athlete_data[athlete_id][0]
                new_ratings = [old[k] + deltas[k] for k in range(N_DISCIPLINES)]
                current_ratings[athlete_id] = new_ratings
                race_counts[athlete_id] += 1
                rating_rows.append((race_id, athlete_id, category, *new_ratings, *deltas))

            if rating_rows:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO ratings
                        (race_id, athlete_id, category, overall, swim, bike, run, transition,
                         overall_change, swim_change, bike_change, run_change, transition_change)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rating_rows,
                )
            continue

        # Corrections are per-discipline, long-format. Manual rows win over auto;
        # absent rows fall through to the original result values.
        results = conn.execute("""
            WITH corr AS (
                SELECT race_id, athlete_id, discipline,
                       COALESCE(MAX(value) FILTER (WHERE source='manual'),
                                MAX(value) FILTER (WHERE source='auto')) AS value
                FROM corrections WHERE race_id = ?
                GROUP BY race_id, athlete_id, discipline
            ),
            corr_wide AS (
                SELECT athlete_id,
                       MAX(value) FILTER (WHERE discipline='overall') AS overall,
                       MAX(value) FILTER (WHERE discipline='swim')    AS swim,
                       MAX(value) FILTER (WHERE discipline='bike')    AS bike,
                       MAX(value) FILTER (WHERE discipline='run')     AS run,
                       MAX(value) FILTER (WHERE discipline='t1')      AS t1,
                       MAX(value) FILTER (WHERE discipline='t2')      AS t2
                FROM corr GROUP BY athlete_id
            )
            SELECT res.athlete_id,
                   COALESCE(c.overall, res.overall_s),
                   COALESCE(c.swim,    res.swim_s),
                   COALESCE(c.bike,    res.bike_s),
                   COALESCE(c.run,     res.run_s),
                   COALESCE(c.t1,      res.t1_s),
                   COALESCE(c.t2,      res.t2_s)
            FROM results res
            LEFT JOIN corr_wide c ON res.athlete_id = c.athlete_id
            WHERE res.race_id = ?
        """, [race_id, race_id]).fetchall()

        if len(results) < 2:
            continue

        # Build athlete_data: athlete_id -> (ratings, times, race_count_before_this_race)
        athlete_data = {}
        for athlete_id, overall_s, swim_s, bike_s, run_s, t1_s, t2_s in results:
            if athlete_id not in current_ratings:
                current_ratings[athlete_id] = [float(START_RATING)] * N_DISCIPLINES
                race_counts[athlete_id] = 0

            # Split sanitation (impossible values, outliers, DNF handling) is done
            # in ptd_data.auto_corrections, applied via the corr_wide join above.
            # A zero in any time means "skip this discipline for this athlete in
            # this race" — _pairwise_elo drops zero-leg pairings per-discipline,
            # so DNFs with a clean swim still update swim ELO.
            transition_s = (t1_s + t2_s) if t1_s > 0 and t2_s > 0 else 0.0
            times = [overall_s, swim_s, bike_s, run_s, transition_s]

            athlete_data[athlete_id] = (
                current_ratings[athlete_id][:], times, race_counts[athlete_id]
            )

        # Pairwise ELO with confidence weighting, K boosted for sparse eras
        # so early-days athletes can accumulate rating at a comparable rate to
        # modern athletes despite fewer annual races.
        k_eff = k_factor * era_k_mult.get(race_year, 1.0)
        elo_changes = _pairwise_elo(athlete_data, k_eff)

        # Apply changes and build rows for bulk insert
        rating_rows = []
        for athlete_id, deltas in elo_changes.items():
            old = athlete_data[athlete_id][0]
            new_ratings = [old[k] + deltas[k] for k in range(N_DISCIPLINES)]
            current_ratings[athlete_id] = new_ratings
            race_counts[athlete_id] += 1
            rating_rows.append((race_id, athlete_id, category, *new_ratings, *deltas))

        conn.executemany(
            """
            INSERT OR IGNORE INTO ratings
                (race_id, athlete_id, category, overall, swim, bike, run, transition,
                 overall_change, swim_change, bike_change, run_change, transition_change)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rating_rows,
        )


def _pairwise_elo(athlete_data, k_factor):
    """Compute confidence-weighted pairwise ELO changes for all athletes in a race.

    athlete_data: dict of athlete_id -> (ratings_list, times_list, race_count)
    k_factor:     course-specific base K.

    Returns dict of athlete_id -> [delta_overall, ..., delta_transition]
    (already scaled by k_factor, self_k_mult, and opponent confidence).
    """
    ids = list(athlete_data.keys())
    n = len(ids)
    changes = {aid: [0.0] * N_DISCIPLINES for aid in ids}

    for i in range(n):
        id1 = ids[i]
        ratings1, times1, rc1 = athlete_data[id1]
        sk1 = _self_k_mult(rc1)
        conf1 = _confidence(rc1)

        for j in range(i + 1, n):
            id2 = ids[j]
            ratings2, times2, rc2 = athlete_data[id2]
            sk2 = _self_k_mult(rc2)
            conf2 = _confidence(rc2)

            for k in range(N_DISCIPLINES):
                t1, t2 = times1[k], times2[k]
                if t1 == 0 or t2 == 0:
                    continue
                raw = _logtime_elo(ratings1[k], ratings2[k], t1, t2)
                # Asymmetric: each side scaled by own self_k_mult × opponent's confidence
                changes[id1][k] += raw * k_factor * sk1 * conf2
                changes[id2][k] -= raw * k_factor * sk2 * conf1

    return changes


def _logtime_elo(rating1, rating2, time1, time2):
    """Core ELO: surprise in log-ratio space.

    Returns raw change for athlete 1 (positive = performed better than expected).
    """
    expected = ((rating1 - rating2) / SCALE) * math.log(10)
    actual = math.log(time2 / time1)
    return actual - expected


# ---------------------------------------------------------------------------
# Phase 2: Rankings
# ---------------------------------------------------------------------------

def _compute_rankings(conn, category, course, clear=True):
    """Compute world and national rankings for one (category, course) from the ratings table.

    Rankings are course-scoped: being #1 in short course says nothing about
    your long-course rank.

    With clear=False, find the earliest ratings row (in this category, course)
    that has no rankings entry yet, wipe all rankings rows from that race date
    onward, then rebuild from there. Mirrors the ratings extend strategy.
    """
    athlete_info = {}  # athlete_id -> (gender, country_full)
    for athlete_id, gender, country in conn.execute(
        "SELECT athlete_id, gender, country_full FROM athletes"
    ).fetchall():
        athlete_info[athlete_id] = (gender, country)

    distances = COURSES[course]['distances']
    in_sql = _in_sql(distances)

    gender_state = {
        'male': _RankingState(),
        'female': _RankingState(),
    }
    processed_race_ids = set()

    if not clear:
        cutoff_date = conn.execute(f"""
            SELECT MIN(r.race_date)
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE ra.category = ? AND r.distance IN {in_sql}
              AND ra.race_id NOT IN (SELECT race_id FROM rankings WHERE category = ?)
        """, [category, category]).fetchone()[0]

        if cutoff_date is None:
            print(f"  {category} {course}: no new ratings to rank")
            return

        conn.execute(f"""
            DELETE FROM rankings
            WHERE category = ? AND race_id IN (
                SELECT race_id FROM races
                WHERE distance IN {in_sql} AND race_date >= ?
            )
        """, [category, cutoff_date])
        print(f"  {category} {course}: extending rankings from {cutoff_date}")

        processed_race_ids = set(r[0] for r in conn.execute(f"""
            SELECT DISTINCT rk.race_id FROM rankings rk
            JOIN races r ON rk.race_id = r.race_id
            WHERE rk.category = ? AND r.distance IN {in_sql}
        """, [category]).fetchall())

        for athlete_id, overall, swim, bike, run, transition, race_date in conn.execute(f"""
            SELECT DISTINCT ON (ra.athlete_id)
                   ra.athlete_id, ra.overall, ra.swim, ra.bike, ra.run, ra.transition, r.race_date
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE ra.category = ? AND r.distance IN {in_sql}
              AND ra.race_id IN (SELECT race_id FROM rankings WHERE category = ?)
            ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
        """, [category, category]).fetchall():
            gender, country = athlete_info.get(athlete_id, ('male', ''))
            gender_state[gender].update(athlete_id, [overall, swim, bike, run, transition], country, race_date)

    entries = conn.execute(f"""
        SELECT ra.race_id, ra.athlete_id, ra.overall, ra.swim, ra.bike, ra.run, ra.transition,
               r.race_date
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        WHERE ra.category = ? AND r.distance IN {in_sql}
        ORDER BY r.race_date, ra.race_id
    """, [category]).fetchall()

    if not clear and processed_race_ids:
        entries = [e for e in entries if e[0] not in processed_race_ids]

    n_races = len({e[0] for e in entries})

    ranking_rows = []
    current_race_id = None
    current_race_date = None
    race_participants = []

    with tqdm(total=n_races, desc=f"Computing {category} {course} rankings", unit="race") as pbar:
        for race_id, athlete_id, overall, swim, bike, run, transition, race_date in entries:
            if race_id != current_race_id:
                if current_race_id is not None:
                    _flush_rankings(current_race_id, current_race_date, race_participants, gender_state, ranking_rows, category)
                    pbar.update(1)
                current_race_id = race_id
                current_race_date = race_date
                race_participants = []

            gender, country = athlete_info.get(athlete_id, ('male', ''))
            state = gender_state[gender]
            state.update(athlete_id, [overall, swim, bike, run, transition], country, race_date)
            race_participants.append((athlete_id, gender))

        if current_race_id is not None:
            _flush_rankings(current_race_id, current_race_date, race_participants, gender_state, ranking_rows, category)
            pbar.update(1)

    print(f"Inserting {len(ranking_rows)} {category} {course} ranking rows...")
    if ranking_rows:
        conn.executemany(
            """
            INSERT OR IGNORE INTO rankings
                (race_id, athlete_id, category,
                 world_overall, world_swim, world_bike, world_run, world_transition,
                 national_overall, national_swim, national_bike, national_run, national_transition,
                 active_world_overall, active_world_swim, active_world_bike, active_world_run, active_world_transition)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ranking_rows,
        )

    print(f"{category} {course} rankings complete: {len(ranking_rows)} athlete-race entries")


class _RankingState:
    """Tracks current ratings for all athletes of one gender, supports fast ranking."""

    def __init__(self):
        self.athlete_ids = []       # ordered list of athlete_ids
        self.id_to_idx = {}         # athlete_id -> index in arrays
        self.ratings = None         # numpy array (n_athletes, 5)
        self.countries = []         # country_full per athlete, same order as athlete_ids
        self.last_ordinals = None   # numpy int array of date.toordinal() per athlete

    def update(self, athlete_id, ratings_list, country, race_date):
        ordinal = race_date.toordinal()
        if athlete_id in self.id_to_idx:
            idx = self.id_to_idx[athlete_id]
            self.ratings[idx] = ratings_list
            self.last_ordinals[idx] = ordinal
        else:
            idx = len(self.athlete_ids)
            self.id_to_idx[athlete_id] = idx
            self.athlete_ids.append(athlete_id)
            self.countries.append(country)
            new_ord = np.array([ordinal], dtype=np.int32)
            self.last_ordinals = new_ord if self.last_ordinals is None else np.append(self.last_ordinals, new_ord)
            row = np.array([ratings_list], dtype=np.float64)
            if self.ratings is None:
                self.ratings = row
            else:
                self.ratings = np.vstack([self.ratings, row])

    def world_rank(self, athlete_id, disc_idx):
        """Count of athletes with strictly higher rating + 1."""
        idx = self.id_to_idx[athlete_id]
        val = self.ratings[idx, disc_idx]
        return int((self.ratings[:, disc_idx] > val).sum()) + 1

    def active_mask(self, cutoff_ordinal):
        """Boolean mask of athletes whose last race is on or after cutoff_ordinal."""
        return self.last_ordinals >= cutoff_ordinal

    def national_rank(self, athlete_id, disc_idx):
        """Count of same-country athletes with strictly higher rating + 1."""
        idx = self.id_to_idx[athlete_id]
        val = self.ratings[idx, disc_idx]
        country = self.countries[idx]
        # Build country mask
        mask = np.array([c == country for c in self.countries], dtype=bool)
        return int((self.ratings[mask, disc_idx] > val).sum()) + 1


def _flush_rankings(race_id, race_date, participants, gender_state, ranking_rows, category):
    """Compute and append ranking rows for all participants in a race."""
    cutoff_ordinal = (race_date - timedelta(days=ACTIVE_WINDOW_DAYS)).toordinal()
    # Precompute active mask and sliced active-ratings matrix once per gender
    active_ratings_by_gender = {}
    for gender, state in gender_state.items():
        if state.ratings is not None:
            mask = state.active_mask(cutoff_ordinal)
            active_ratings_by_gender[gender] = state.ratings[mask]

    for athlete_id, gender in participants:
        state = gender_state[gender]
        idx = state.id_to_idx[athlete_id]
        my_ratings = state.ratings[idx]  # shape (5,)

        world = [int((state.ratings[:, k] > my_ratings[k]).sum()) + 1 for k in range(N_DISCIPLINES)]
        national = [state.national_rank(athlete_id, k) for k in range(N_DISCIPLINES)]

        active_r = active_ratings_by_gender.get(gender)
        if active_r is not None and len(active_r):
            active_world = [int((active_r[:, k] > my_ratings[k]).sum()) + 1 for k in range(N_DISCIPLINES)]
        else:
            active_world = [1] * N_DISCIPLINES

        ranking_rows.append((race_id, athlete_id, category, *world, *national, *active_world))


# ---------------------------------------------------------------------------
# Phase 2b: Country mixed relay ratings + rankings
# ---------------------------------------------------------------------------

def _compute_country_ratings(conn):
    """Country-level mixed relay ELO and rankings, recomputed in full.

    Rating entity = country. Elite relays only (junior/u23 relays feed the
    athletes' individual ratings but say little about the senior squad), and
    only each country's best-finishing team per race - second teams are
    development lineups and would drag the rating below A-team strength.

    Disciplines mirror the athlete model: overall = team total, swim/bike/run/
    transition = the team's four leg splits summed (zero, i.e. skipped in the
    pairwise pass, unless all four legs have the split). Volume is tiny
    (~10-25 races/yr) so this is always a full clear + recompute.
    """
    conn.execute("DELETE FROM country_ratings")
    conn.execute("DELETE FROM country_rankings")

    k_factor = COURSES['short']['k_factor']
    era_k_mult = _era_k_multiplier_table(conn, ('relay',))

    races = conn.execute("""
        SELECT race_id, EXTRACT(YEAR FROM race_date)::INTEGER, race_date
        FROM races
        WHERE distance = 'relay' AND sub_category = 'elite'
          AND race_id NOT IN (SELECT race_id FROM ignored_races)
        ORDER BY race_date, race_id
    """).fetchall()

    current = {}      # country_full -> [overall, swim, bike, run, transition]
    counts = {}       # country_full -> races so far
    last_seen = {}    # country_full -> last race_date
    rating_rows, ranking_rows = [], []

    for race_id, race_year, race_date in tqdm(races, desc="Computing country relay ratings", unit="race"):
        teams = conn.execute("""
            SELECT DISTINCT ON (rt.country_full)
                   rt.country_full, rt.team_id, rt.total_s,
                   SUM(l.swim_s) FILTER (WHERE l.swim_s > 0),
                   COUNT(*) FILTER (WHERE l.swim_s > 0),
                   SUM(l.bike_s) FILTER (WHERE l.bike_s > 0),
                   COUNT(*) FILTER (WHERE l.bike_s > 0),
                   SUM(l.run_s)  FILTER (WHERE l.run_s > 0),
                   COUNT(*) FILTER (WHERE l.run_s > 0),
                   SUM(l.t1_s + l.t2_s) FILTER (WHERE l.t1_s > 0 AND l.t2_s > 0),
                   COUNT(*) FILTER (WHERE l.t1_s > 0 AND l.t2_s > 0)
            FROM relay_teams rt
            LEFT JOIN relay_legs l ON l.race_id = rt.race_id AND l.team_id = rt.team_id
            WHERE rt.race_id = ? AND rt.status = 'Finished'
              AND rt.position IS NOT NULL AND rt.total_s > 0
            GROUP BY rt.country_full, rt.team_id, rt.position, rt.total_s
            ORDER BY rt.country_full, rt.position
        """, [race_id]).fetchall()

        country_data = {}
        for (country, _team_id, total_s,
             swim_sum, swim_n, bike_sum, bike_n, run_sum, run_n, trans_sum, trans_n) in teams:
            if country not in current:
                current[country] = [float(START_RATING)] * N_DISCIPLINES
                counts[country] = 0
            times = [
                total_s,
                swim_sum if swim_n == 4 else 0.0,
                bike_sum if bike_n == 4 else 0.0,
                run_sum if run_n == 4 else 0.0,
                trans_sum if trans_n == 4 else 0.0,
            ]
            times = [float(t or 0.0) for t in times]
            country_data[country] = (current[country][:], times, counts[country])

        if len(country_data) < 2:
            continue

        k_eff = k_factor * era_k_mult.get(race_year, 1.0)
        elo_changes = _pairwise_elo(country_data, k_eff)

        for country, deltas in elo_changes.items():
            old = country_data[country][0]
            new_ratings = [old[k] + deltas[k] for k in range(N_DISCIPLINES)]
            current[country] = new_ratings
            counts[country] += 1
            last_seen[country] = race_date
            rating_rows.append((race_id, country, *new_ratings, *deltas))

        # Rankings snapshot for this race's participants
        cutoff = race_date - timedelta(days=ACTIVE_WINDOW_DAYS)
        active = {c for c, d in last_seen.items() if d >= cutoff}
        for country in elo_changes:
            mine = current[country]
            world = [sum(1 for c in current if current[c][k] > mine[k]) + 1
                     for k in range(N_DISCIPLINES)]
            active_world = [sum(1 for c in active if current[c][k] > mine[k]) + 1
                            for k in range(N_DISCIPLINES)]
            ranking_rows.append((race_id, country, *world, *active_world))

    if rating_rows:
        conn.executemany(
            """
            INSERT OR IGNORE INTO country_ratings
                (race_id, country_full, overall, swim, bike, run, transition,
                 overall_change, swim_change, bike_change, run_change, transition_change)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rating_rows,
        )
    if ranking_rows:
        conn.executemany(
            """
            INSERT OR IGNORE INTO country_rankings
                (race_id, country_full,
                 world_overall, world_swim, world_bike, world_run, world_transition,
                 active_world_overall, active_world_swim, active_world_bike,
                 active_world_run, active_world_transition)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ranking_rows,
        )
    print(f"Country relay ratings: {len(rating_rows)} rating rows over {len(races)} races")


# ---------------------------------------------------------------------------
# Phase 3: Prediction models
# ---------------------------------------------------------------------------

def _fit_prediction_models(conn):
    """Fit WLS linear models: winner_time = slope * pre_race_rating + intercept.

    One model per (gender, distance, discipline) - 20 total.
    Saves debug scatter plots to debug/prediction_models/.
    """
    import os
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    os.makedirs('debug/prediction_models', exist_ok=True)

    # Transitions vary too much by course layout to model reliably - excluded.
    DISCS = ['overall', 'swim', 'bike', 'run']

    # Valid winner time ranges per distance and discipline (seconds).
    # These exclude misclassified races (sprint results in the standard bucket and vice
    # versa), very slow national-championship outliers, and corrupted splits.
    TIME_BOUNDS = {
        # (lo, hi) in seconds
        'sprint': {
            'overall': (1800,  5400),   # 30–90 min
            'swim':    ( 240,  1500),   # 4–25 min
            'bike':    ( 600,  3600),   # 10–60 min
            'run':     ( 480,  2400),   # 8–40 min
        },
        'standard': {
            'overall': (5400, 10800),   # 90–180 min
            'swim':    ( 600,  3600),   # 10–60 min
            'bike':    (2400,  7200),   # 40–120 min
            'run':     (1800,  5400),   # 30–90 min
        },
        'middle': {
            'overall': (12600, 21600),  # 3.5–6 hr
            'swim':    ( 1200,  3600),  # 20–60 min
            'bike':    ( 6000, 12000),  # 100–200 min
            'run':     ( 3600,  7200),  # 60–120 min
        },
        't100': {
            # T100 is 2/80/18 km - meaningfully shorter than 70.3. Elite winners
            # come in around 3:10-3:25, with 18km runs often under 60 min.
            'overall': (10800, 18000),  # 3–5 hr
            'swim':    (  900,  3600),  # 15–60 min
            'bike':    ( 6000, 12000),  # 100–200 min
            'run':     ( 2700,  7200),  # 45–120 min
        },
        'long': {
            'overall': (25200, 50400),  # 7–14 hr
            'swim':    ( 2400,  6000),  # 40–100 min
            'bike':    (12000, 25200),  # 200–420 min
            'run':     ( 8400, 21600),  # 140–360 min
        },
    }

    # r_target is the race we're predicting — must match the specific distance
    # being fit (otherwise e.g. 70.3 finisher rows leak into the T100 model,
    # since middle + t100 overall times overlap heavily). r_hist is the athlete's
    # rating source, which legitimately spans the whole course bucket because
    # ratings are course-scoped, not distance-scoped.
    base_sql = """
        WITH all_finishers AS (
            SELECT res.race_id, res.athlete_id,
                   res.overall_s, res.swim_s, res.bike_s, res.run_s,
                   CASE WHEN res.t1_s > 0 AND res.t2_s > 0
                        THEN res.t1_s + res.t2_s ELSE 0 END AS transition_s
            FROM results res
            WHERE res.overall_s > 0
        ),
        latest_pre_race AS (
            SELECT DISTINCT ON (af.race_id, af.athlete_id)
                   af.race_id, af.athlete_id,
                   af.overall_s, af.swim_s, af.bike_s, af.run_s, af.transition_s,
                   ra.overall AS r_overall, ra.swim AS r_swim, ra.bike AS r_bike,
                   ra.run AS r_run, ra.transition AS r_transition,
                   -- Pre-race count = number of r_hist rows in this (target, athlete)
                   -- group. The CTE is target × hist pairs before DISTINCT ON, so a
                   -- partition over (target, athlete) counts the historical races
                   -- directly. Partitioning by athlete alone would quadratically
                   -- over-count and inflate weights for few-start athletes.
                   COUNT(*) OVER (PARTITION BY af.race_id, ra.athlete_id) AS race_count
            FROM all_finishers af
            JOIN races r_target ON af.race_id = r_target.race_id
            JOIN ratings ra ON ra.athlete_id = af.athlete_id
            JOIN races r_hist ON ra.race_id = r_hist.race_id
            WHERE r_hist.race_date < r_target.race_date
              AND ra.category = 'elite'
              AND r_target.distance = ?
              AND r_hist.distance   IN {course_in}
            ORDER BY af.race_id, af.athlete_id, r_hist.race_date DESC, ra.race_id DESC
        )
        SELECT lp.overall_s, lp.swim_s, lp.bike_s, lp.run_s, lp.transition_s,
               lp.r_overall, lp.r_swim, lp.r_bike, lp.r_run, lp.r_transition,
               lp.race_count,
               EXTRACT(YEAR FROM r.race_date)::INTEGER AS race_year
        FROM latest_pre_race lp
        JOIN races r ON lp.race_id = r.race_id
        JOIN athletes a ON lp.athlete_id = a.athlete_id
        WHERE a.gender = ?
          AND r.category = 'elite'
          AND lp.overall_s BETWEEN ? AND ?
    """

    distances = [
        ('sprint',    1800,  5400),   # 30–90 min
        ('standard',  5400, 10800),   # 90–180 min
        ('middle',   12600, 21600),   # 3.5–6 hr
        ('t100',     10800, 18000),   # T100: 2/80/18 km, 3–5 hr
        ('long',     25200, 50400),   # 7–14 hr
    ]
    genders = ['male', 'female']
    model_rows = []

    # Which course bucket a given distance belongs to (for pre-race rating scoping).
    distance_to_course = {d: c for c, info in COURSES.items() for d in info['distances']}

    for gender in genders:
        for distance, overall_lo, overall_hi in distances:
            course = distance_to_course[distance]
            course_in = _in_sql(COURSES[course]['distances'])
            sql = base_sql.format(course_in=course_in)
            rows = conn.execute(sql, [distance, gender, overall_lo, overall_hi]).fetchall()
            if not rows:
                print(f"  No data for {gender} {distance}, skipping")
                continue
            print(f"  {gender} {distance}: {len(rows)} finisher records (after overall time filter)")

            data = np.array(rows, dtype=np.float64)
            # cols: 0-4 = time cols, 5-9 = rating cols, 10 = race_count, 11 = race_year
            # Weight by experience using same confidence curve as the ELO model:
            # min(1, race_count / CONF_THRESHOLD) — debuts get 0, full weight at 10 starts.
            ws_all    = np.minimum(1.0, data[:, 10] / CONF_THRESHOLD)
            years_all = data[:, 11] - YEAR_REF

            # Year term captures era drift (equipment, training, fuelling) for
            # long-course distances with enough history to support it. Short
            # course showed no systematic era pattern in the residual sweep, so
            # it gets a simpler 2-param fit. T100 is a brand-new distance —
            # PTD's data starts 2018 but the pro-tier series only launched in
            # 2024, so year-coded drift just overfits to short-run noise (one
            # sweep landed female T100 overall at +233 s/yr). Exclude by name.
            use_year_term = (course == 'long') and (distance != 't100')

            for disc_idx, disc in enumerate(DISCS):
                ys = data[:, disc_idx]       # winner times
                xs = data[:, 5 + disc_idx]   # pre-race ratings
                ys_raw_full = ys
                ws = ws_all.copy()
                yrs = years_all.copy()

                lo, hi = TIME_BOUNDS[distance][disc]
                # Apply per-discipline time bounds, exclude debuts (ws=0, ratings
                # not yet initialised), and require a non-zero pre-race rating.
                mask = (ys >= lo) & (ys <= hi) & (xs > 100) & (ws > 0)
                ys, xs, ws, yrs = ys[mask], xs[mask], ws[mask], yrs[mask]
                n = len(ys)
                if n < 30:
                    print(f"    {disc}: only {n} samples after filtering, skipping")
                    continue

                def _wls2(xs_, ys_, ws_):
                    W   = ws_.sum()
                    sX  = (ws_ * xs_).sum()
                    sY  = (ws_ * ys_).sum()
                    sXY = (ws_ * xs_ * ys_).sum()
                    sXX = (ws_ * xs_ * xs_).sum()
                    denom = W * sXX - sX * sX
                    s = (W * sXY - sX * sY) / denom
                    return s, (sY - s * sX) / W

                # Initial OLS fit on (rating, time), then sigma-clip once to
                # strip truly broken data (bad splits, cut courses). Overall and
                # run have naturally wider spread so use 3σ; swim and bike 2.5σ.
                sigma = 3.0 if disc in ('overall', 'run') else 2.5
                slope, intercept = _wls2(xs, ys, ws)
                residuals = ys - (slope * xs + intercept)
                keep = np.abs(residuals) < sigma * residuals.std()
                ys, xs, ws, yrs = ys[keep], xs[keep], ws[keep], yrs[keep]
                n = len(ys)

                # Stage 1: 2-param quantile IRLS on (rating, time). Keeps slope
                # and intercept identical to the pre-year-term fit — i.e. model
                # behaviour at year_offset=0 is unchanged.
                tau = QUANTILE_TAU_BY_COURSE[course]
                slope, intercept = _wls2(xs, ys, ws)
                ols_slope, ols_intercept = slope, intercept
                for _ in range(QUANTILE_MAX_ITER):
                    residuals = ys - (slope * xs + intercept)
                    w_q = ws * np.abs(tau - (residuals < 0).astype(np.float64)) \
                          / np.maximum(np.abs(residuals), QUANTILE_EPS)
                    s_new, i_new = _wls2(xs, ys, w_q)
                    if abs(s_new - slope) < 1e-6 and abs(i_new - intercept) < 1e-3:
                        slope, intercept = s_new, i_new
                        break
                    slope, intercept = s_new, i_new

                if slope >= 0:
                    print(f"    {disc}: quantile slope {slope:.4f} >= 0, falling back to OLS")
                    slope, intercept = ols_slope, ols_intercept

                # Stage 2: fit year_coef only on residuals (one-variable WLS,
                # through origin because we don't want to double-count a mean
                # shift that's already in the intercept). Decoupling from the
                # slope fit prevents the joint 3-param fit from flattening slope
                # as it absorbs era-drift signal.
                year_coef = 0.0
                if use_year_term:
                    resid_for_year = ys - (slope * xs + intercept)
                    num = (ws * yrs * resid_for_year).sum()
                    den = (ws * yrs * yrs).sum()
                    if den > 0:
                        year_coef = float(num / den)

                model_rows.append((gender, distance, disc, slope, intercept, n, year_coef))
                year_str = f"  year_coef={year_coef:+.2f}s/yr" if use_year_term else ""
                print(f"    {disc}: slope={slope:.4f}  intercept={intercept:.1f}  n={n}{year_str}")

                # Debug scatter plot (fit line at year_offset=0, i.e. YEAR_REF)
                fig, ax = plt.subplots(figsize=(8, 5))
                ax.scatter(xs, ys / 60, alpha=0.4, s=10, label='data')
                x_line = np.linspace(xs.min(), xs.max(), 100)
                y_line = (slope * x_line + intercept) / 60
                ax.plot(x_line, y_line, 'r-', label=f'fit (n={n}{", @" + str(YEAR_REF) if use_year_term else ""})')
                ax.set_xlabel('Pre-race rating')
                ax.set_ylabel('Time (min)')
                ax.set_title(f'{gender} {distance} {disc}')
                ax.legend()
                fig.savefig(f'debug/prediction_models/{gender}_{distance}_{disc}.png',
                            dpi=100, bbox_inches='tight')
                plt.close(fig)

    if model_rows:
        conn.executemany(
            "INSERT OR REPLACE INTO prediction_models "
            "(gender, distance, discipline, slope, intercept, n_samples, year_coef) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            model_rows,
        )
    print(f"Saved {len(model_rows)} prediction models")


# ---------------------------------------------------------------------------
# Phase 4: Race rankings
# ---------------------------------------------------------------------------

def _compute_race_rankings(conn):
    """Compute per-race standards and rank each race vs same (gender, course).

    Standard for a discipline = exp-decay-weighted average of pre-race ratings,
    summed over the top STANDARD_POS_CAP entries for that discipline and
    divided by standard_denom(n_for_that_discipline).

    Per-discipline inclusion criteria:
      overall:    status='Finished' AND position IS NOT NULL, ranked by position
      swim/bike/run: leg time > 0, ranked by that leg time ascending
      transition: t1 > 0 AND t2 > 0, ranked by (t1 + t2) ascending

    This means a runner who DNFs on the run but completed swim and bike still
    contributes to the swim and bike standards. Ranks are computed within
    each (gender, course) bucket; rank 1 = highest standard. A discipline gets
    NULL std (and NULL rank) on races where no one recorded a time for it.
    """
    conn.execute("DELETE FROM race_rankings")

    rows = conn.execute(f"""
        WITH base AS (
            SELECT r.race_id, r.gender, r.category, r.distance,
                   res.status, res.position AS overall_pos,
                   res.swim_s, res.bike_s, res.run_s,
                   CASE WHEN res.t1_s > 0 AND res.t2_s > 0 THEN res.t1_s + res.t2_s ELSE 0 END AS trans_s,
                   ra.overall    - ra.overall_change    AS overall_pre,
                   ra.swim       - ra.swim_change       AS swim_pre,
                   ra.bike       - ra.bike_change       AS bike_pre,
                   ra.run        - ra.run_change        AS run_pre,
                   ra.transition - ra.transition_change AS transition_pre
            FROM races r
            JOIN results res ON res.race_id = r.race_id
            JOIN ratings ra  ON ra.race_id  = res.race_id AND ra.athlete_id = res.athlete_id
        ),
        ranked AS (
            SELECT *,
                CASE WHEN swim_s  > 0 THEN ROW_NUMBER() OVER (PARTITION BY race_id ORDER BY CASE WHEN swim_s  > 0 THEN swim_s  END NULLS LAST) END AS swim_pos,
                CASE WHEN bike_s  > 0 THEN ROW_NUMBER() OVER (PARTITION BY race_id ORDER BY CASE WHEN bike_s  > 0 THEN bike_s  END NULLS LAST) END AS bike_pos,
                CASE WHEN run_s   > 0 THEN ROW_NUMBER() OVER (PARTITION BY race_id ORDER BY CASE WHEN run_s   > 0 THEN run_s   END NULLS LAST) END AS run_pos,
                CASE WHEN trans_s > 0 THEN ROW_NUMBER() OVER (PARTITION BY race_id ORDER BY CASE WHEN trans_s > 0 THEN trans_s END NULLS LAST) END AS trans_pos
            FROM base
        )
        SELECT race_id, gender,
               CASE
                   WHEN category = 'ag' THEN 'ag'
                   WHEN distance IN ('sprint','standard') THEN 'short'
                   ELSE 'long'
               END AS course,
               COUNT(*) FILTER (WHERE status = 'Finished' AND overall_pos IS NOT NULL) AS n_overall,
               COUNT(*) FILTER (WHERE swim_s  > 0) AS n_swim,
               COUNT(*) FILTER (WHERE bike_s  > 0) AS n_bike,
               COUNT(*) FILTER (WHERE run_s   > 0) AS n_run,
               COUNT(*) FILTER (WHERE trans_s > 0) AS n_trans,
               SUM(overall_pre    * CASE WHEN status='Finished' AND overall_pos IS NOT NULL AND overall_pos <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (overall_pos - 1)) ELSE 0 END) AS overall_num,
               SUM(swim_pre       * CASE WHEN swim_pos  IS NOT NULL AND swim_pos  <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (swim_pos  - 1)) ELSE 0 END) AS swim_num,
               SUM(bike_pre       * CASE WHEN bike_pos  IS NOT NULL AND bike_pos  <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (bike_pos  - 1)) ELSE 0 END) AS bike_num,
               SUM(run_pre        * CASE WHEN run_pos   IS NOT NULL AND run_pos   <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (run_pos   - 1)) ELSE 0 END) AS run_num,
               SUM(transition_pre * CASE WHEN trans_pos IS NOT NULL AND trans_pos <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (trans_pos - 1)) ELSE 0 END) AS transition_num
        FROM ranked
        GROUP BY race_id, gender, category, distance
        HAVING COUNT(*) FILTER (WHERE status = 'Finished' AND overall_pos IS NOT NULL) >= 2
    """).fetchall()

    if not rows:
        print("No race standards computed.")
        return

    # Per-discipline counts/nums in column order matching the SELECT above.
    DISCS = ['overall', 'swim', 'bike', 'run', 'transition']
    by_bucket = {}  # (gender, course) -> list of dicts
    for race_id, gender, course, n_overall, n_swim, n_bike, n_run, n_trans, overall_num, swim_num, bike_num, run_num, transition_num in rows:
        counts = {'overall': n_overall, 'swim': n_swim, 'bike': n_bike, 'run': n_run, 'transition': n_trans}
        nums   = {'overall': overall_num, 'swim': swim_num, 'bike': bike_num, 'run': run_num, 'transition': transition_num}
        rec = {'race_id': race_id, 'gender': gender, 'course': course}
        for d in DISCS:
            n = counts[d]
            rec[d] = (nums[d] / standard_denom(n)) if n and n > 0 else None
        by_bucket.setdefault((gender, course), []).append(rec)

    ranked_rows = []
    for (gender, course), recs in by_bucket.items():
        rank_maps = {}
        for d in DISCS:
            ordered = sorted(
                [r for r in recs if r[d] is not None],
                key=lambda r: r[d],
                reverse=True,
            )
            rank_maps[d] = {r['race_id']: i + 1 for i, r in enumerate(ordered)}
        for r in recs:
            ranked_rows.append((
                r['race_id'], gender, course,
                r['overall'], r['swim'], r['bike'], r['run'], r['transition'],
                rank_maps['overall'].get(r['race_id']),
                rank_maps['swim'].get(r['race_id']),
                rank_maps['bike'].get(r['race_id']),
                rank_maps['run'].get(r['race_id']),
                rank_maps['transition'].get(r['race_id']),
            ))

    conn.executemany(
        """
        INSERT INTO race_rankings
            (race_id, gender, course,
             overall_std, swim_std, bike_std, run_std, transition_std,
             overall_rank, swim_rank, bike_rank, run_rank, transition_rank)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ranked_rows,
    )
    print(f"Computed race rankings for {len(ranked_rows)} races across "
          f"{len(by_bucket)} (gender, course) buckets")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["all", "ratings", "rankings", "models"], default="all",
                        help="Which phase to run. Default 'all' runs ratings, then rankings, then models.")
    parser.add_argument("--extend", action="store_true",
                        help="Incrementally extend ratings/rankings: find the earliest "
                             "unprocessed race per (category, course), wipe ratings/rankings "
                             "from that date forward, then rebuild. Handles backdated "
                             "late-arriving results. Ignored for --phase models "
                             "(models are always a full refit).")
    args = parser.parse_args()

    clear = not args.extend
    conn = db.get_conn(read_only=False)

    # Any race that now has results is no longer "upcoming". Purge stale rows so
    # extend runs don't leave completed races sitting in the upcoming table.
    conn.execute("DELETE FROM start_list_entries WHERE race_id IN (SELECT race_id FROM races)")
    conn.execute("DELETE FROM upcoming_races WHERE race_id IN (SELECT race_id FROM races)")

    if args.phase in ("ratings", "all"):
        print(f"{'Extending' if not clear else 'Recomputing'} ratings...")
        if clear:
            conn.execute("DELETE FROM ratings")
        for category in ('elite', 'ag'):
            for course in COURSES:
                _compute_ratings(conn, category, course, clear=clear)

    if args.phase in ("rankings", "all"):
        print(f"{'Extending' if not clear else 'Recomputing'} rankings...")
        if clear:
            conn.execute("DELETE FROM rankings")
        for category in ('elite', 'ag'):
            for course in COURSES:
                _compute_rankings(conn, category, course, clear=clear)

        # Race rankings always recompute fully — they're a global sort over
        # standards, so an incremental update would need to shift ranks across
        # every race anyway. Cheap relative to the ELO pass.
        print("Recomputing race rankings...")
        _compute_race_rankings(conn)

        # Country mixed relay ELO + rankings: tiny volume, always full recompute.
        print("Recomputing country relay ratings...")
        _compute_country_ratings(conn)

    if args.phase in ("models", "all"):
        print("Refitting prediction models...")
        conn.execute("DELETE FROM prediction_models")
        _fit_prediction_models(conn)
        print("Recomputing athlete form...")
        from ptd_data.form import compute_form
        compute_form(conn)

    conn.close()
    print("Done.")
