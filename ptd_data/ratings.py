"""
Computes ELO ratings and world/national rankings from race results in DuckDB.

Reimplements the logic from stats/elo.py to work directly on the database.
Processes all races chronologically, computes pairwise log-time-ratio ELO
across 5 disciplines (overall, swim, bike, run, transition), then computes
world and national rankings per gender.

Usage:
    python -m ptd_data.ratings
"""

import math
from collections import defaultdict
from itertools import groupby

import numpy as np
from tqdm import tqdm

from config import ELITE_START_RATING, AG_START_RATING
from ptd_data import db
from ptd_data.db import load_corrections
from ptd_data.ingest import is_valid_program

SCALE = 46175.8
K_FACTOR = 16

# Discipline indices: overall=0, swim=1, bike=2, run=3, transition=4
N_DISCIPLINES = 5

_ELITE_PROGS = {'elite', 'u23', 'junior'}


def _is_elite_prog(prog_name):
    """Elite/U23/Junior programs get elite start rating, everything else is AG."""
    first_word = prog_name.lower().split()[0] if prog_name else ''
    return first_word in _ELITE_PROGS


def compute_all(conn):
    """Full recompute: reload corrections, clear computed tables, ratings, rankings."""
    load_corrections(conn)
    conn.execute("DELETE FROM rankings")
    conn.execute("DELETE FROM ratings")
    _compute_ratings(conn)
    _compute_rankings(conn)


# ---------------------------------------------------------------------------
# Phase 1: ELO ratings
# ---------------------------------------------------------------------------

def _compute_ratings(conn):
    """Process all races chronologically, compute pairwise ELO, write to ratings table."""
    ignored = set(
        r[0] for r in conn.execute("SELECT race_id FROM ignored_races").fetchall()
    )

    races = conn.execute("""
        SELECT race_id, prog_name
        FROM races
        ORDER BY race_date, race_id
    """).fetchall()

    # Running state: athlete_id -> [overall, swim, bike, run, transition]
    current_ratings = {}

    for race_id, prog_name in tqdm(races, desc="Computing ratings", unit="race"):
        if race_id in ignored:
            continue
        if not is_valid_program(prog_name):
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

        # Elite/U23/Junior get elite start rating, everything else (AG, Masters, etc.) gets AG
        start = ELITE_START_RATING if _is_elite_prog(prog_name) else AG_START_RATING

        # Build athlete_data: athlete_id -> (ratings_list, times_list)
        athlete_data = {}
        for athlete_id, overall_s, swim_s, bike_s, run_s, t1_s, t2_s in results:
            if athlete_id not in current_ratings:
                current_ratings[athlete_id] = [float(start)] * N_DISCIPLINES

            transition_s = (t1_s + t2_s) if t1_s > 0 and t2_s > 0 else 0.0
            times = [overall_s, swim_s, bike_s, run_s, transition_s]

            # Zero all splits if overall is 0 (DNF/DNS/DQ)
            if overall_s == 0:
                times = [0.0] * N_DISCIPLINES

            athlete_data[athlete_id] = (current_ratings[athlete_id][:], times)

        # Pairwise ELO
        elo_changes = _pairwise_elo(athlete_data)

        # Apply changes and build rows for bulk insert
        rating_rows = []
        for athlete_id, raw_changes in elo_changes.items():
            old = athlete_data[athlete_id][0]
            new_ratings = [old[k] + raw_changes[k] * K_FACTOR for k in range(N_DISCIPLINES)]
            deltas = [raw_changes[k] * K_FACTOR for k in range(N_DISCIPLINES)]
            current_ratings[athlete_id] = new_ratings
            rating_rows.append((race_id, athlete_id, *new_ratings, *deltas))

        conn.executemany(
            """
            INSERT OR IGNORE INTO ratings
                (race_id, athlete_id, overall, swim, bike, run, transition,
                 overall_change, swim_change, bike_change, run_change, transition_change)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rating_rows,
        )


def _pairwise_elo(athlete_data):
    """Compute pairwise ELO changes for all athletes in a race.

    Returns dict of athlete_id -> [raw_change_overall, ..., raw_change_transition]
    (not yet scaled by K_FACTOR).
    """
    ids = list(athlete_data.keys())
    n = len(ids)
    changes = {aid: [0.0] * N_DISCIPLINES for aid in ids}

    for i in range(n):
        id1 = ids[i]
        ratings1, times1 = athlete_data[id1]

        for j in range(i + 1, n):
            id2 = ids[j]
            ratings2, times2 = athlete_data[id2]

            for k in range(N_DISCIPLINES):
                t1, t2 = times1[k], times2[k]
                if t1 == 0 or t2 == 0:
                    continue
                change = _logtime_elo(ratings1[k], ratings2[k], t1, t2)
                changes[id1][k] += change
                changes[id2][k] -= change

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

def _compute_rankings(conn):
    """Compute world and national rankings from the completed ratings table.

    For each race an athlete participated in, computes their rank among all
    same-gender athletes who have a rating at that point in time.
    """
    # Load athlete metadata
    athlete_info = {}  # athlete_id -> (gender, country_full)
    for athlete_id, gender, country in conn.execute(
        "SELECT athlete_id, gender, country_full FROM athletes"
    ).fetchall():
        athlete_info[athlete_id] = (gender, country)

    # Load all rating entries in chronological order
    entries = conn.execute("""
        SELECT ra.race_id, ra.athlete_id, ra.overall, ra.swim, ra.bike, ra.run, ra.transition
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        ORDER BY r.race_date, ra.race_id
    """).fetchall()

    # Running state per gender
    # For efficient ranking, maintain numpy arrays that grow as athletes appear
    gender_state = {
        'male': _RankingState(),
        'female': _RankingState(),
    }

    n_races = conn.execute("SELECT COUNT(DISTINCT race_id) FROM ratings").fetchone()[0]

    ranking_rows = []
    current_race_id = None
    race_participants = []  # (athlete_id, gender) for current race

    with tqdm(total=n_races, desc="Computing rankings", unit="race") as pbar:
        for race_id, athlete_id, overall, swim, bike, run, transition in entries:
            # When we move to a new race, flush rankings for the previous race
            if race_id != current_race_id:
                if current_race_id is not None:
                    _flush_rankings(current_race_id, race_participants, gender_state, ranking_rows)
                    pbar.update(1)
                current_race_id = race_id
                race_participants = []

            gender, country = athlete_info.get(athlete_id, ('male', ''))
            state = gender_state[gender]
            state.update(athlete_id, [overall, swim, bike, run, transition], country)
            race_participants.append((athlete_id, gender))

        # Flush last race
        if current_race_id is not None:
            _flush_rankings(current_race_id, race_participants, gender_state, ranking_rows)
            pbar.update(1)

    # Bulk insert
    print(f"Inserting {len(ranking_rows)} ranking rows...")
    if ranking_rows:
        conn.executemany(
            """
            INSERT OR IGNORE INTO rankings
                (race_id, athlete_id,
                 world_overall, world_swim, world_bike, world_run, world_transition,
                 national_overall, national_swim, national_bike, national_run, national_transition)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ranking_rows,
        )

    print(f"Rankings complete: {len(ranking_rows)} athlete-race entries")


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


def _flush_rankings(race_id, participants, gender_state, ranking_rows):
    """Compute and append ranking rows for all participants in a race."""
    for athlete_id, gender in participants:
        state = gender_state[gender]
        world = [state.world_rank(athlete_id, k) for k in range(N_DISCIPLINES)]
        national = [state.national_rank(athlete_id, k) for k in range(N_DISCIPLINES)]
        ranking_rows.append((race_id, athlete_id, *world, *national))


if __name__ == "__main__":
    conn = db.get_conn(read_only=False)
    print("Computing ratings and rankings...")
    compute_all(conn)
    conn.close()
    print("Done.")
