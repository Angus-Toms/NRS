"""
Computes ELO ratings and world/national rankings from race results in DuckDB.

Processes all races chronologically, computes pairwise log-time-ratio ELO
across 5 disciplines (overall, swim, bike, run, transition) with Glicko-style
confidence weighting, then computes world and national rankings per gender.

Confidence model:
  - Each athlete tracks a race count. Confidence = min(1, race_count / CONF_THRESHOLD).
  - Self-K multiplier: new athletes move faster (3× decaying to 1× over CONF_THRESHOLD races).
  - Opponent weight: pairings against established athletes count more than against newcomers.

Usage:
    python -m ptd_data.ratings
"""

import math

import numpy as np
from tqdm import tqdm

from ptd_data import db
from ptd_data.db import load_corrections

SCALE = 46175.8
K_FACTOR = 32
CONF_THRESHOLD = 10  # races to reach full confidence
START_RATING = 1500

# Discipline indices: overall=0, swim=1, bike=2, run=3, transition=4
N_DISCIPLINES = 5


def _confidence(race_count):
    """0..1 confidence based on how many races an athlete has completed."""
    return min(1.0, race_count / CONF_THRESHOLD)


def _self_k_mult(race_count):
    """Higher K multiplier for new athletes — 3× decaying to 1× over CONF_THRESHOLD races."""
    return 1.0 + 2.0 * max(0.0, 1.0 - race_count / CONF_THRESHOLD)


def compute_all(conn):
    """Full recompute: reload corrections, clear computed tables, ratings, rankings."""
    load_corrections(conn)
    conn.execute("DELETE FROM rankings")
    conn.execute("DELETE FROM ratings")
    for category in ('elite', 'ag'):
        _compute_ratings(conn, category)
    for category in ('elite', 'ag'):
        _compute_rankings(conn, category)


# ---------------------------------------------------------------------------
# Phase 1: ELO ratings
# ---------------------------------------------------------------------------

def _compute_ratings(conn, category):
    """Process all races chronologically for one category, compute confidence-weighted pairwise ELO."""
    ignored = set(
        r[0] for r in conn.execute("SELECT race_id FROM ignored_races").fetchall()
    )

    races = conn.execute("""
        SELECT race_id
        FROM races
        WHERE category = ?
        ORDER BY race_date, race_id
    """, [category]).fetchall()

    current_ratings = {}   # athlete_id -> [overall, swim, bike, run, transition]
    race_counts = {}       # athlete_id -> number of races completed so far

    for (race_id,) in tqdm(races, desc=f"Computing {category} ratings", unit="race"):
        if race_id in ignored:
            continue

        results = conn.execute("""
            SELECT res.athlete_id,
                   CASE WHEN c.athlete_id IS NOT NULL THEN c.overall ELSE res.overall_s END,
                   CASE WHEN c.athlete_id IS NOT NULL THEN c.swim    ELSE res.swim_s   END,
                   CASE WHEN c.athlete_id IS NOT NULL THEN c.bike    ELSE res.bike_s   END,
                   CASE WHEN c.athlete_id IS NOT NULL THEN c.run     ELSE res.run_s    END,
                   CASE WHEN c.athlete_id IS NOT NULL THEN c.t1      ELSE res.t1_s     END,
                   CASE WHEN c.athlete_id IS NOT NULL THEN c.t2      ELSE res.t2_s     END
            FROM results res
            LEFT JOIN corrections c ON res.race_id = c.race_id AND res.athlete_id = c.athlete_id
            WHERE res.race_id = ?
        """, [race_id]).fetchall()

        if len(results) < 2:
            continue

        # Build athlete_data: athlete_id -> (ratings, times, race_count_before_this_race)
        athlete_data = {}
        for athlete_id, overall_s, swim_s, bike_s, run_s, t1_s, t2_s in results:
            if athlete_id not in current_ratings:
                current_ratings[athlete_id] = [float(START_RATING)] * N_DISCIPLINES
                race_counts[athlete_id] = 0

            transition_s = (t1_s + t2_s) if t1_s > 0 and t2_s > 0 else 0.0
            times = [overall_s, swim_s, bike_s, run_s, transition_s]

            if overall_s == 0:
                times = [0.0] * N_DISCIPLINES

            athlete_data[athlete_id] = (
                current_ratings[athlete_id][:], times, race_counts[athlete_id]
            )

        # Pairwise ELO with confidence weighting
        elo_changes = _pairwise_elo(athlete_data)

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


def _pairwise_elo(athlete_data):
    """Compute confidence-weighted pairwise ELO changes for all athletes in a race.

    athlete_data: dict of athlete_id -> (ratings_list, times_list, race_count)

    Returns dict of athlete_id -> [delta_overall, ..., delta_transition]
    (already scaled by K_FACTOR, self_k_mult, and opponent confidence).
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
                changes[id1][k] += raw * K_FACTOR * sk1 * conf2
                changes[id2][k] -= raw * K_FACTOR * sk2 * conf1

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

def _compute_rankings(conn, category):
    """Compute world and national rankings for one category from the ratings table."""
    athlete_info = {}  # athlete_id -> (gender, country_full)
    for athlete_id, gender, country in conn.execute(
        "SELECT athlete_id, gender, country_full FROM athletes"
    ).fetchall():
        athlete_info[athlete_id] = (gender, country)

    entries = conn.execute("""
        SELECT ra.race_id, ra.athlete_id, ra.overall, ra.swim, ra.bike, ra.run, ra.transition
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        WHERE ra.category = ?
        ORDER BY r.race_date, ra.race_id
    """, [category]).fetchall()

    gender_state = {
        'male': _RankingState(),
        'female': _RankingState(),
    }

    n_races = conn.execute(
        "SELECT COUNT(DISTINCT race_id) FROM ratings WHERE category = ?", [category]
    ).fetchone()[0]

    ranking_rows = []
    current_race_id = None
    race_participants = []

    with tqdm(total=n_races, desc=f"Computing {category} rankings", unit="race") as pbar:
        for race_id, athlete_id, overall, swim, bike, run, transition in entries:
            if race_id != current_race_id:
                if current_race_id is not None:
                    _flush_rankings(current_race_id, race_participants, gender_state, ranking_rows, category)
                    pbar.update(1)
                current_race_id = race_id
                race_participants = []

            gender, country = athlete_info.get(athlete_id, ('male', ''))
            state = gender_state[gender]
            state.update(athlete_id, [overall, swim, bike, run, transition], country)
            race_participants.append((athlete_id, gender))

        if current_race_id is not None:
            _flush_rankings(current_race_id, race_participants, gender_state, ranking_rows, category)
            pbar.update(1)

    print(f"Inserting {len(ranking_rows)} {category} ranking rows...")
    if ranking_rows:
        conn.executemany(
            """
            INSERT OR IGNORE INTO rankings
                (race_id, athlete_id, category,
                 world_overall, world_swim, world_bike, world_run, world_transition,
                 national_overall, national_swim, national_bike, national_run, national_transition)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ranking_rows,
        )

    print(f"{category} rankings complete: {len(ranking_rows)} athlete-race entries")


class _RankingState:
    """Tracks current ratings for all athletes of one gender, supports fast ranking."""

    def __init__(self):
        self.athlete_ids = []       # ordered list of athlete_ids
        self.id_to_idx = {}         # athlete_id -> index in arrays
        self.ratings = None         # numpy array (n_athletes, 5)
        self.countries = []         # country_full per athlete, same order as athlete_ids

    def update(self, athlete_id, ratings_list, country):
        if athlete_id in self.id_to_idx:
            idx = self.id_to_idx[athlete_id]
            self.ratings[idx] = ratings_list
        else:
            idx = len(self.athlete_ids)
            self.id_to_idx[athlete_id] = idx
            self.athlete_ids.append(athlete_id)
            self.countries.append(country)
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

    def national_rank(self, athlete_id, disc_idx):
        """Count of same-country athletes with strictly higher rating + 1."""
        idx = self.id_to_idx[athlete_id]
        val = self.ratings[idx, disc_idx]
        country = self.countries[idx]
        # Build country mask
        mask = np.array([c == country for c in self.countries], dtype=bool)
        return int((self.ratings[mask, disc_idx] > val).sum()) + 1


def _flush_rankings(race_id, participants, gender_state, ranking_rows, category):
    """Compute and append ranking rows for all participants in a race."""
    for athlete_id, gender in participants:
        state = gender_state[gender]
        world = [state.world_rank(athlete_id, k) for k in range(N_DISCIPLINES)]
        national = [state.national_rank(athlete_id, k) for k in range(N_DISCIPLINES)]
        ranking_rows.append((race_id, athlete_id, category, *world, *national))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ratings-only", action="store_true", help="Recompute ratings only, leave rankings untouched")
    args = parser.parse_args()

    conn = db.get_conn(read_only=False)
    if args.ratings_only:
        print("Recomputing ratings only...")
        load_corrections(conn)
        conn.execute("DELETE FROM ratings")
        for category in ('elite', 'ag'):
            _compute_ratings(conn, category)
    else:
        print("Computing ratings and rankings...")
        compute_all(conn)
    conn.close()
    print("Done.")
