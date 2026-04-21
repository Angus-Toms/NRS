"""
Computes ELO ratings and world/national rankings from race results in DuckDB.

Processes all races chronologically, computes pairwise log-time-ratio ELO
across 5 disciplines (overall, swim, bike, run, transition) with Glicko-style
confidence weighting, then computes world and national rankings per gender.

Confidence model:
  - Each athlete tracks a race count. Confidence = min(1, race_count / CONF_THRESHOLD).
  - Self-K multiplier: new athletes move faster (3× at race 0, linearly decaying to 1× over CONF_THRESHOLD races).
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

# Discipline indices: overall=0, swim=1, bike=2, run=3, transition=4
N_DISCIPLINES = 5

# Per-course ELO parameters. Long course gets a higher K because athletes race
# far less frequently — fewer updates per year means each race should move the
# rating more to keep the model responsive.
COURSES = {
    'short': {'distances': ('sprint', 'standard'),        'k_factor': 36},
    'long':  {'distances': ('middle', 't100',   'long'),  'k_factor': 54},
}


def _in_sql(values):
    """Build a SQL IN-clause literal from a tuple of strings."""
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def _confidence(race_count):
    """0..1 confidence based on how many races an athlete has completed."""
    return min(1.0, race_count / CONF_THRESHOLD)


def _self_k_mult(race_count):
    """Higher K multiplier for new athletes - 3× at race 0, linearly decaying to 1× by race CONF_THRESHOLD."""
    return 1.0 + 2.0 * max(0.0, 1.0 - race_count / CONF_THRESHOLD)


def compute_all(conn):
    """Full recompute: clear computed tables, ratings, rankings. Manual corrections
    from corrections.csv are NOT loaded — only auto corrections are used."""
    conn.execute("DELETE FROM rankings")
    conn.execute("DELETE FROM ratings")
    for category in ('elite', 'ag'):
        for course in COURSES:
            _compute_ratings(conn, category, course)
    for category in ('elite', 'ag'):
        for course in COURSES:
            _compute_rankings(conn, category, course)
    conn.execute("DELETE FROM prediction_models")
    _fit_prediction_models(conn)


# ---------------------------------------------------------------------------
# Phase 1: ELO ratings
# ---------------------------------------------------------------------------

def _compute_ratings(conn, category, course):
    """Process all races chronologically for one (category, course), compute confidence-weighted pairwise ELO.

    Ratings are tracked independently per course: an athlete's short-course
    trajectory does not influence their long-course trajectory (different
    skillsets, different race densities).
    """
    ignored = set(
        r[0] for r in conn.execute("SELECT race_id FROM ignored_races").fetchall()
    )

    distances = COURSES[course]['distances']
    k_factor = COURSES[course]['k_factor']

    races = conn.execute(f"""
        SELECT race_id
        FROM races
        WHERE category = ? AND distance IN {_in_sql(distances)}
        ORDER BY race_date, race_id
    """, [category]).fetchall()

    current_ratings = {}   # athlete_id -> [overall, swim, bike, run, transition]
    race_counts = {}       # athlete_id -> number of races completed so far

    for (race_id,) in tqdm(races, desc=f"Computing {category} {course} ratings", unit="race"):
        if race_id in ignored:
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

        # Pairwise ELO with confidence weighting
        elo_changes = _pairwise_elo(athlete_data, k_factor)

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

def _compute_rankings(conn, category, course):
    """Compute world and national rankings for one (category, course) from the ratings table.

    Rankings are course-scoped: being #1 in short course says nothing about
    your long-course rank.
    """
    athlete_info = {}  # athlete_id -> (gender, country_full)
    for athlete_id, gender, country in conn.execute(
        "SELECT athlete_id, gender, country_full FROM athletes"
    ).fetchall():
        athlete_info[athlete_id] = (gender, country)

    distances = COURSES[course]['distances']
    in_sql = _in_sql(distances)

    entries = conn.execute(f"""
        SELECT ra.race_id, ra.athlete_id, ra.overall, ra.swim, ra.bike, ra.run, ra.transition,
               r.race_date
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        WHERE ra.category = ? AND r.distance IN {in_sql}
        ORDER BY r.race_date, ra.race_id
    """, [category]).fetchall()

    gender_state = {
        'male': _RankingState(),
        'female': _RankingState(),
    }

    n_races = conn.execute(f"""
        SELECT COUNT(DISTINCT ra.race_id)
        FROM ratings ra JOIN races r ON ra.race_id = r.race_id
        WHERE ra.category = ? AND r.distance IN {in_sql}
    """, [category]).fetchone()[0]

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
            'overall': (12600, 21600),  # 3.5–6 hr (100km ≈ middle-distance pace)
            'swim':    ( 1200,  3600),
            'bike':    ( 6000, 12000),
            'run':     ( 3600,  7200),
        },
        'long': {
            'overall': (25200, 50400),  # 7–14 hr
            'swim':    ( 2400,  6000),  # 40–100 min
            'bike':    (12000, 25200),  # 200–420 min
            'run':     ( 8400, 21600),  # 140–360 min
        },
    }

    # All finishers across all elite races. Target-race distance filter comes
    # from the {course_in} placeholder so pre-race ratings are picked from the
    # same course (ratings are course-scoped and shouldn't cross-pollinate).
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
                   COUNT(*) OVER (PARTITION BY ra.athlete_id
                                  ORDER BY r_hist.race_date
                                  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS race_count
            FROM all_finishers af
            JOIN races r_target ON af.race_id = r_target.race_id
            JOIN ratings ra ON ra.athlete_id = af.athlete_id
            JOIN races r_hist ON ra.race_id = r_hist.race_id
            WHERE r_hist.race_date < r_target.race_date
              AND ra.category = 'elite'
              AND r_target.distance IN {course_in}
              AND r_hist.distance   IN {course_in}
            ORDER BY af.race_id, af.athlete_id, r_hist.race_date DESC, ra.race_id DESC
        )
        SELECT lp.overall_s, lp.swim_s, lp.bike_s, lp.run_s, lp.transition_s,
               lp.r_overall, lp.r_swim, lp.r_bike, lp.r_run, lp.r_transition,
               lp.race_count
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
        ('t100',     12600, 21600),   # 100km ~ middle
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
            rows = conn.execute(sql, [gender, overall_lo, overall_hi]).fetchall()
            if not rows:
                print(f"  No data for {gender} {distance}, skipping")
                continue
            print(f"  {gender} {distance}: {len(rows)} finisher records (after overall time filter)")

            data = np.array(rows, dtype=np.float64)
            # cols: 0-4 = time cols, 5-9 = rating cols, 10 = race_count
            # Weight by experience using same confidence curve as the ELO model:
            # min(1, race_count / CONF_THRESHOLD) — debuts get 0, full weight at 10 starts.
            ws_all = np.minimum(1.0, data[:, 10] / CONF_THRESHOLD)

            for disc_idx, disc in enumerate(DISCS):
                ys = data[:, disc_idx]       # winner times
                xs = data[:, 5 + disc_idx]   # pre-race ratings
                ws = ws_all.copy()

                lo, hi = TIME_BOUNDS[distance][disc]
                # Apply per-discipline time bounds, exclude debuts (ws=0, ratings
                # not yet initialised), and require a non-zero pre-race rating.
                mask = (ys >= lo) & (ys <= hi) & (xs > 100) & (ws > 0)
                ys, xs, ws = ys[mask], xs[mask], ws[mask]
                n = len(ys)
                if n < 30:
                    print(f"    {disc}: only {n} samples after filtering, skipping")
                    continue

                def _wls(ys, xs, ws):
                    W   = ws.sum()
                    sX  = (ws * xs).sum()
                    sY  = (ws * ys).sum()
                    sXY = (ws * xs * ys).sum()
                    sXX = (ws * xs * xs).sum()
                    denom = W * sXX - sX * sX
                    s = (W * sXY - sX * sY) / denom
                    return s, (sY - s * sX) / W

                # Initial fit, then sigma-clip once. Overall and run have naturally
                # wider spread so use 3σ; swim and bike use 2.5σ.
                sigma = 3.0 if disc in ('overall', 'run') else 2.5
                slope, intercept = _wls(ys, xs, ws)
                residuals = ys - (slope * xs + intercept)
                keep = np.abs(residuals) < sigma * residuals.std()
                ys, xs, ws = ys[keep], xs[keep], ws[keep]
                n = len(ys)
                slope, intercept = _wls(ys, xs, ws)

                model_rows.append((gender, distance, disc, slope, intercept, n))
                print(f"    {disc}: slope={slope:.4f}  intercept={intercept:.1f}  n={n}")

                # Debug scatter plot
                fig, ax = plt.subplots(figsize=(8, 5))
                ax.scatter(xs, ys / 60, alpha=0.4, s=10, label='data')
                x_line = np.linspace(xs.min(), xs.max(), 100)
                ax.plot(x_line, (slope * x_line + intercept) / 60, 'r-', label=f'fit (n={n})')
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
            "(gender, distance, discipline, slope, intercept, n_samples) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            model_rows,
        )
    print(f"Saved {len(model_rows)} prediction models")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ratings-only", action="store_true", help="Recompute ratings only, leave rankings untouched")
    parser.add_argument("--rankings-only", action="store_true", help="Recompute rankings only, leave ratings untouched")
    args = parser.parse_args()

    conn = db.get_conn(read_only=False)
    if args.rankings_only:
        print("Recomputing rankings only...")
        conn.execute("DELETE FROM rankings")
        for category in ('elite', 'ag'):
            for course in COURSES:
                _compute_rankings(conn, category, course)
    elif args.ratings_only:
        print("Recomputing ratings only...")
        conn.execute("DELETE FROM ratings")
        for category in ('elite', 'ag'):
            for course in COURSES:
                _compute_ratings(conn, category, course)
    else:
        print("Computing ratings and rankings...")
        compute_all(conn)
    conn.close()
    print("Done.")
