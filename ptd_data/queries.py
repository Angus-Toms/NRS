"""
Read-only query functions against the PTD DuckDB database.

All functions return plain dicts/lists - no custom objects, no DataFrames.
Formatting stays in the routers.
"""

import math
import re
import statistics
from ast import literal_eval
from functools import lru_cache

from ptd_data import db
from ptd_data.form import _tier_for
from ptd_data.ratings import STANDARD_K, STANDARD_POS_CAP, standard_denom

# Handlers run in FastAPI's threadpool; db.get_read_cursor gives each
# thread its own cursor over one shared read-only connection.
def _get_conn():
    return db.get_read_cursor()


def _dicts(cols, cur):
    """Map a DuckDB result cursor to a list of dicts keyed by `cols`."""
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# Course → distance-enum values. Used to scope rating/ranking queries so
# that short-course and long-course ratings (which live in the same table
# but are computed independently) don't leak into each other.
# 'relay' sits in the short bucket: mixed relay legs update the short-course
# athlete ELO (damped, see ratings.RELAY_K_MULT), so rating/ranking queries
# must see those rows. Results-joined queries drop relay races naturally
# (relay results live in relay_teams/relay_legs, not results).
COURSE_DISTANCES = {
    'short': ('sprint', 'standard', 'relay'),
    'long':  ('middle', 't100', 'long'),
}


def _course_in(course):
    """SQL literal IN-clause for the distances of a given course ('short' | 'long')."""
    return "(" + ", ".join(f"'{d}'" for d in COURSE_DISTANCES[course]) + ")"




def course_for_distance(distance):
    """Map a distance_enum value to its course bucket ('short' | 'long' | None)."""
    for course, distances in COURSE_DISTANCES.items():
        if distance in distances:
            return course
    return None


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_counts():
    """Return {athletes, races, results} row counts."""
    conn = _get_conn()
    athletes = conn.execute("SELECT COUNT(*) FROM athletes").fetchone()[0]
    races    = conn.execute("SELECT COUNT(*) FROM races").fetchone()[0]
    results  = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
    return {"athletes": athletes, "races": races, "results": results}


# ---------------------------------------------------------------------------
# Athlete search
# ---------------------------------------------------------------------------

_SHORT_IN = _course_in('short')
_LONG_IN  = _course_in('long')

# CTE producing per-athlete program flags used by search endpoints to surface
# SC / LC / AG badges and to scope the compare-second-athlete search.
# Programs are the (course, category) buckets users can actually compare:
# AG ratings only exist for short-course races, so there's no separate ag-long.
_TAGS_CTE = f"""
tags AS (
    SELECT ra.athlete_id,
           BOOL_OR(ra.category = 'elite' AND r.distance IN {_SHORT_IN}) AS has_elite_short,
           BOOL_OR(ra.category = 'elite' AND r.distance IN {_LONG_IN})  AS has_elite_long,
           BOOL_OR(ra.category = 'ag')                                   AS has_ag
    FROM ratings ra
    JOIN races r ON ra.race_id = r.race_id
    GROUP BY ra.athlete_id
)
"""


_PROGRAM_TO_TAG = {
    'elite-short': 'has_elite_short',
    'elite-long':  'has_elite_long',
    'ag':          'has_ag',
}


def search_athletes(query, gender=None, course='all', require_programs=None):
    """
    Substring search by name (case-insensitive), returning athletes with at
    least one rating.

    `course` may be 'all' (default), 'short', or 'long'. When 'short'/'long'
    the results are scoped to athletes with a rating in that course (used
    by the race page to stop e.g. WTCS-only sprinters being predicted onto
    an Ironman).

    `require_programs` is an optional iterable of program names from
    _PROGRAM_TO_TAG. When provided, only athletes with at least one of
    those programs are returned. Used by the compare page to limit the
    second athlete search to someone shareable with the first.
    """
    conn = _get_conn()
    course_filter = "" if course == 'all' else f"WHERE r.distance IN {_course_in(course)}"
    params = [f"%{query}%"]
    gender_clause = ""
    if gender:
        gender_clause = " AND a.gender = ?"
        params.append(gender)

    program_clause = ""
    if require_programs:
        tag_cols = [_PROGRAM_TO_TAG[p] for p in require_programs if p in _PROGRAM_TO_TAG]
        if tag_cols:
            program_clause = " AND (" + " OR ".join(f"t.{c}" for c in tag_cols) + ")"

    cols = ["athlete_id", "name", "year_of_birth", "gender", "country_alpha3",
            "country_full", "rating",
            "has_elite_short", "has_elite_long", "has_ag"]
    return _dicts(cols, conn.execute(f"""
        WITH {_TAGS_CTE}
        SELECT
            a.athlete_id,
            a.name,
            a.year_of_birth,
            a.gender,
            n.alpha3   AS country_alpha3,
            a.country_full,
            latest.overall AS rating,
            COALESCE(t.has_elite_short, FALSE) AS has_elite_short,
            COALESCE(t.has_elite_long,  FALSE) AS has_elite_long,
            COALESCE(t.has_ag,          FALSE) AS has_ag
        FROM athletes a
        JOIN nationalities n ON a.country_full = n.country_full
        JOIN (
            SELECT ra.athlete_id, ra.overall
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            {course_filter}
            QUALIFY ROW_NUMBER() OVER (PARTITION BY ra.athlete_id
                                       ORDER BY r.race_date DESC, ra.race_id DESC) = 1
        ) latest ON a.athlete_id = latest.athlete_id
        LEFT JOIN tags t ON a.athlete_id = t.athlete_id
        WHERE a.name ILIKE ?{gender_clause}{program_clause}
        ORDER BY rating DESC
        LIMIT 50
    """, params))


def search_athletes_full(query, disc="overall", order="top", country=None,
                         yob_start=None, yob_end=None, active_only=False, limit=10,
                         course='all'):
    """
    Name search with filter/sort options for the athletes landing page.
    Returns leaderboard-style dicts with ratings and 1yr disc change.

    `course` may be 'all' (default — latest rating from either course),
    'short', or 'long'. For 'short'/'long', only athletes with a rating
    in that course are returned and the displayed rating is that course's.
    """
    assert disc in _VALID_DISCS and order in _VALID_ORDERS
    conn = _get_conn()
    # When filtering to a single course, restrict the CTE row set so the
    # 'current' and 'year_ago' ratings come from that course. 'all' drops
    # the filter entirely so any athlete's latest rating (any distance) wins.
    course_filter = "" if course == 'all' else f"AND r.distance IN {_course_in(course)}"

    filters = ["a.name ILIKE ?"]
    params  = [f"%{query}%"]
    if country:
        filters.append("a.country_full = ?")
        params.append(country)
    if yob_start:
        filters.append("a.year_of_birth >= ?")
        params.append(int(yob_start))
    if yob_end:
        filters.append("a.year_of_birth <= ?")
        params.append(int(yob_end))
    if active_only:
        filters.append("c.last_race_date >= CURRENT_DATE - INTERVAL 18 MONTHS")

    where = " AND ".join(filters)
    order_clause = (
        f"c.{disc} DESC" if order == "top"
        else f"COALESCE(c.{disc} - ya.{disc}, 0) DESC"
    )

    cols = ["athlete_id", "name", "year_of_birth", "gender", "country_alpha3",
            "country_full", "profile_img",
            "overall_rating", "swim_rating", "bike_rating", "run_rating", "transition_rating",
            "race_starts", "wins",
            "has_elite_short", "has_elite_long", "has_ag"]
    return _dicts(cols, conn.execute(f"""
        WITH current AS (
            SELECT DISTINCT ON (ra.athlete_id)
                   ra.athlete_id,
                   ra.overall, ra.swim, ra.bike, ra.run, ra.transition,
                   r.race_date AS last_race_date
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE TRUE {course_filter}
            ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
        ),
        year_ago AS (
            SELECT DISTINCT ON (ra.athlete_id)
                   ra.athlete_id,
                   ra.overall, ra.swim, ra.bike, ra.run, ra.transition
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE r.race_date <= CURRENT_DATE - INTERVAL 1 YEAR {course_filter}
            ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
        ),
        athlete_stats AS (
            SELECT athlete_id,
                   COUNT(*) AS race_starts,
                   COUNT(CASE WHEN position = 1 THEN 1 END) AS wins
            FROM results
            GROUP BY athlete_id
        ),
        {_TAGS_CTE}
        SELECT
            a.athlete_id,
            a.name,
            a.year_of_birth,
            a.gender,
            n.alpha3    AS country_alpha3,
            a.country_full,
            a.profile_img,
            c.overall, c.swim, c.bike, c.run, c.transition,
            COALESCE(s.race_starts, 0) AS race_starts,
            COALESCE(s.wins, 0)        AS wins,
            COALESCE(t.has_elite_short, FALSE) AS has_elite_short,
            COALESCE(t.has_elite_long,  FALSE) AS has_elite_long,
            COALESCE(t.has_ag,          FALSE) AS has_ag
        FROM athletes a
        JOIN nationalities n ON a.country_full = n.country_full
        JOIN current c       ON a.athlete_id = c.athlete_id
        LEFT JOIN year_ago ya ON a.athlete_id = ya.athlete_id
        LEFT JOIN athlete_stats s ON a.athlete_id = s.athlete_id
        LEFT JOIN tags t ON a.athlete_id = t.athlete_id
        WHERE {where}
        ORDER BY {order_clause}
        LIMIT ?
    """, params + [limit]))


@lru_cache(maxsize=32)
def get_podium(gender, category='elite', course='short'):
    """
    Top 3 athletes by current overall rating for a given gender, category, and course.
    Returns list of dicts.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    cols = ["athlete_id", "name", "country_alpha3",
            "country_full", "year_of_birth", "profile_img", "overall", "overall_rank"]
    return _dicts(cols, conn.execute(f"""
        SELECT
            a.athlete_id,
            a.name,
            n.alpha3   AS country_alpha3,
            a.country_full,
            a.year_of_birth,
            a.profile_img,
            cur.overall,
            rk.world_overall AS overall_rank
        FROM athletes a
        JOIN nationalities n ON a.country_full = n.country_full
        JOIN (
            SELECT DISTINCT ON (rk.athlete_id)
                   rk.athlete_id, rk.world_overall
            FROM rankings rk
            JOIN races r ON rk.race_id = r.race_id
            WHERE r.category = ? AND r.distance IN {course_in}
            ORDER BY rk.athlete_id, r.race_date DESC, rk.race_id DESC
        ) rk ON a.athlete_id = rk.athlete_id
        JOIN (
            SELECT DISTINCT ON (ra.athlete_id)
                   ra.athlete_id, ra.overall
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE r.category = ? AND r.distance IN {course_in}
            ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
        ) cur ON a.athlete_id = cur.athlete_id
        WHERE a.gender = ?
        ORDER BY cur.overall DESC
        LIMIT 3
    """, [category, category, gender]))


# ---------------------------------------------------------------------------
# Race search
# ---------------------------------------------------------------------------

def _fmt_time(seconds):
    # Allow 0 — a joint-fastest podium gap is a real "+0:00", not None.
    # Negatives are still nonsensical here so we drop them.
    if seconds is None or seconds < 0:
        return None
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

def get_recent_events(offset, limit, country=None):
    """Events sorted by start_date desc, paginated, with constituent races.

    Optional `country` filter restricts to events hosted in that country (name).
    """
    conn = _get_conn()
    country_sql, country_params = ("AND e.country = ?", [country]) if country else ("", [])
    event_rows = conn.execute(f"""
        SELECT event_id, name, venue, country, start_date, end_date
        FROM events e
        WHERE EXISTS (SELECT 1 FROM races r WHERE r.event_id = e.event_id)
          {country_sql}
        ORDER BY start_date DESC
        LIMIT ? OFFSET ?
    """, country_params + [limit, offset]).fetchall()
    if not event_rows:
        return []

    event_ids = [r[0] for r in event_rows]
    event_map = {r[0]: {
        "event_id": r[0], "name": r[1], "venue": r[2], "country": r[3],
        "start_date": r[4], "end_date": r[5], "races": []
    } for r in event_rows}

    placeholders = ",".join("?" * len(event_ids))
    race_rows = conn.execute(f"""
        SELECT event_id, race_id, race_title, prog_name, gender
        FROM races
        WHERE event_id IN ({placeholders})
        ORDER BY race_date ASC,
                 CASE WHEN gender = 'male' THEN 0 ELSE 1 END,
                 race_id ASC
    """, event_ids).fetchall()
    for event_id, race_id, race_title, prog_name, gender in race_rows:
        event_map[event_id]["races"].append({
            "race_id": race_id, "race_title": race_title,
            "prog_name": prog_name, "gender": gender, "podium": [],
        })

    # Fetch podiums for the first 2 races of every event in one batch
    podium_race_ids = []
    for event_id in event_ids:
        for race in event_map[event_id]["races"][:2]:
            podium_race_ids.append(race["race_id"])

    if podium_race_ids:
        ph = ",".join("?" * len(podium_race_ids))
        podium_rows = conn.execute(f"""
            SELECT res.race_id, res.position, a.athlete_id, a.name, n.alpha3, res.overall_s, a.profile_img
            FROM results res
            JOIN athletes a ON res.athlete_id = a.athlete_id
            JOIN nationalities n ON a.country_full = n.country_full
            WHERE res.race_id IN ({ph})
              AND res.position IN (1, 2, 3)
              AND res.status = 'Finished'
            ORDER BY res.race_id, res.position
        """, podium_race_ids).fetchall()

        podium_by_race = {}
        for race_id, position, athlete_id, name, alpha3, overall_s, profile_img in podium_rows:
            podium_by_race.setdefault(race_id, []).append(
                {"position": position, "athlete_id": athlete_id, "name": name,
                 "country_alpha3": alpha3, "overall_s": overall_s, "profile_img": profile_img}
            )

        # Relay podiums (results has no rows for relay races)
        relay_podium_rows = conn.execute(f"""
            SELECT rt.race_id, rt.position, rt.country_full, rt.team_num, n.alpha3, rt.total_s
            FROM relay_teams rt
            JOIN nationalities n ON n.country_full = rt.country_full
            WHERE rt.race_id IN ({ph})
              AND rt.position IN (1, 2, 3) AND rt.status = 'Finished'
            ORDER BY rt.race_id, rt.position
        """, podium_race_ids).fetchall()
        for race_id, position, country_full, team_num, alpha3, total_s in relay_podium_rows:
            podium_by_race.setdefault(race_id, []).append(
                {"position": position, "athlete_id": None, "is_relay": True,
                 "name": relay_team_name(country_full, team_num), "country_alpha3": alpha3,
                 "overall_s": total_s, "profile_img": ""}
            )
        # Compute gap (time diff from winner) for 2nd and 3rd
        for entries in podium_by_race.values():
            winner_s = next((e["overall_s"] for e in entries if e["position"] == 1), None)
            for e in entries:
                e["time"] = _fmt_time(e["overall_s"])
                e["gap"] = (f"+{_fmt_time(e['overall_s'] - winner_s)}"
                            if e["position"] != 1 and winner_s and e["overall_s"] else None)
                del e["overall_s"]
        for event in event_map.values():
            for race in event["races"][:2]:
                race["podium"] = podium_by_race.get(race["race_id"], [])

    return [event_map[r[0]] for r in event_rows]


def get_total_events():
    return _get_conn().execute(
        "SELECT COUNT(*) FROM events e WHERE EXISTS (SELECT 1 FROM races r WHERE r.event_id = e.event_id)"
    ).fetchone()[0]


def get_event_countries():
    """Sorted list of all distinct countries that have hosted events."""
    rows = _get_conn().execute(
        "SELECT DISTINCT country FROM events WHERE country IS NOT NULL ORDER BY country"
    ).fetchall()
    return [r[0] for r in rows]


def search_events_full(query, country=None, year_start=None, year_end=None,
                       sort="desc", limit=20):
    """
    Search events by name/venue/country with optional filters.
    Returns events with their constituent races (no podiums - compact search results).
    """
    conn = _get_conn()
    q = f"%{query}%"
    filters = [
        "(e.name ILIKE ? OR e.venue ILIKE ? OR e.country ILIKE ?)",
        "EXISTS (SELECT 1 FROM races r WHERE r.event_id = e.event_id)",
    ]
    params = [q, q, q]
    if country:
        filters.append("e.country = ?")
        params.append(country)
    if year_start:
        filters.append("EXTRACT(YEAR FROM e.start_date) >= ?")
        params.append(int(year_start))
    if year_end:
        filters.append("EXTRACT(YEAR FROM e.start_date) <= ?")
        params.append(int(year_end))

    direction = "ASC" if sort == "asc" else "DESC"
    where = " AND ".join(filters)

    event_rows = conn.execute(f"""
        SELECT event_id, name, venue, country, start_date
        FROM events e
        WHERE {where}
        ORDER BY e.start_date {direction}
        LIMIT ?
    """, params + [limit]).fetchall()
    if not event_rows:
        return []

    event_ids = [r[0] for r in event_rows]
    event_map = {r[0]: {
        "event_id": r[0], "name": r[1], "venue": r[2],
        "country": r[3], "start_date": r[4], "races": []
    } for r in event_rows}

    placeholders = ",".join("?" * len(event_ids))
    race_rows = conn.execute(f"""
        SELECT event_id, race_id, prog_name, distance
        FROM races
        WHERE event_id IN ({placeholders})
        ORDER BY race_date ASC,
                 CASE WHEN gender = 'male' THEN 0 ELSE 1 END,
                 race_id ASC
    """, event_ids).fetchall()
    for event_id, race_id, prog_name, distance in race_rows:
        event_map[event_id]["races"].append({
            "race_id":   race_id,
            "prog_name": prog_name,
            "course":    course_for_distance(distance),
        })

    return [event_map[r[0]] for r in event_rows]


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

_VALID_DISCS = {"overall", "swim", "bike", "run", "transition"}
_VALID_ORDERS = {"top", "hot"}


def get_leaderboard(gender, disc, order, country, yob_start, yob_end,
                    active_only, offset, limit=100, category='elite', course='short',
                    country_alpha3=None):
    """
    Paginated leaderboard with all filters applied in SQL, scoped to a course.

    order='top'  → sort by current world rank for `disc` ASC
    order='hot'  → filter to non-zero 1yr change, sort by change DESC

    Returns list of dicts. Python caller assigns display rank = offset + 1, offset + 2, ...
    """
    assert disc in _VALID_DISCS and order in _VALID_ORDERS

    conn = _get_conn()
    course_in = _course_in(course)

    rank_col   = f"world_{disc}"
    rating_col = disc
    change_col = f"{disc}_change"

    # Build WHERE clause additions
    filters = ["a.gender = ?"]
    params  = [gender]

    if country_alpha3 and country_alpha3 != "all":
        # Country pages: everyone who has *ever* represented this alpha3. Match
        # the athlete's current country OR any nationality-history row mapping to
        # it (covers home-nation Commonwealth entries, mid-career switches, and
        # the ~347 athletes with no history row who only match via current).
        filters.append("""(n.alpha3 = ? OR a.athlete_id IN (
            SELECT h.athlete_id FROM athlete_nationality_history h
            JOIN nationalities hn ON hn.country_full = h.country_full
            WHERE hn.alpha3 = ?))""")
        params.append(country_alpha3)
        params.append(country_alpha3)
    elif country and country != "all":
        filters.append("a.country_full = ?")
        params.append(country)
    # Treat yob=0 as "unknown" and never exclude on it — otherwise the leaderboard
    # silently drops athletes with missing YOB (the profile's active-rankings
    # count doesn't apply this filter, so the two views would disagree).
    if yob_start:
        filters.append("(a.year_of_birth = 0 OR a.year_of_birth >= ?)")
        params.append(yob_start)
    if yob_end:
        filters.append("(a.year_of_birth = 0 OR a.year_of_birth <= ?)")
        params.append(yob_end)
    if active_only:
        filters.append("c.last_race_date >= CURRENT_DATE - INTERVAL 18 MONTHS")
    if order == "hot":
        filters.append(f"COALESCE(c.{rating_col} - ya.{rating_col}, 0) != 0")

    where = " AND ".join(filters)

    order_clause = (
        f"c.{rating_col} DESC, a.athlete_id ASC"
        if order == "top"
        else f"COALESCE(c.{rating_col} - ya.{rating_col}, 0) DESC, a.athlete_id ASC"
    )

    params.insert(0, category)  # prepend for the CTEs (current, current_rank, year_ago, athlete_stats)
    params.insert(1, category)
    params.insert(2, category)
    params.insert(3, category)

    sql = f"""
        WITH current AS (
            SELECT DISTINCT ON (ra.athlete_id)
                   ra.athlete_id,
                   ra.overall, ra.swim, ra.bike, ra.run, ra.transition,
                   r.race_date AS last_race_date
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE ra.category = ? AND r.distance IN {course_in}
            ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
        ),
        current_rank AS (
            SELECT DISTINCT ON (rk.athlete_id)
                   rk.athlete_id,
                   rk.world_overall, rk.world_swim, rk.world_bike,
                   rk.world_run, rk.world_transition,
                   rk.active_world_overall, rk.active_world_swim, rk.active_world_bike,
                   rk.active_world_run, rk.active_world_transition
            FROM rankings rk
            JOIN races r ON rk.race_id = r.race_id
            WHERE rk.category = ? AND r.distance IN {course_in}
            ORDER BY rk.athlete_id, r.race_date DESC, rk.race_id DESC
        ),
        year_ago AS (
            SELECT DISTINCT ON (ra.athlete_id)
                   ra.athlete_id,
                   ra.overall, ra.swim, ra.bike, ra.run, ra.transition
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE ra.category = ? AND r.distance IN {course_in}
              AND r.race_date <= CURRENT_DATE - INTERVAL 1 YEAR
            ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
        ),
        athlete_stats AS (
            -- Count starts/wins for this category+course only so dual-category athletes
            -- show correct per-category stats on each leaderboard
            SELECT res.athlete_id,
                   COUNT(*) AS race_starts,
                   COUNT(CASE WHEN res.position = 1 THEN 1 END) AS wins
            FROM results res
            JOIN races r ON res.race_id = r.race_id
            WHERE r.category = ? AND r.distance IN {course_in}
            GROUP BY res.athlete_id
        )
        SELECT
            a.athlete_id,
            a.name,
            a.year_of_birth,
            n.alpha3    AS country_alpha3,
            a.country_full,
            a.profile_img,
            c.overall, c.swim, c.bike, c.run, c.transition,
            cr.world_overall, cr.world_swim, cr.world_bike, cr.world_run, cr.world_transition,
            cr.active_world_overall, cr.active_world_swim, cr.active_world_bike,
            cr.active_world_run, cr.active_world_transition,
            c.last_race_date >= CURRENT_DATE - INTERVAL 18 MONTHS AS active,
            COALESCE(s.race_starts, 0) AS race_starts,
            COALESCE(s.wins, 0) AS wins,
            COALESCE(c.overall    - ya.overall,    0) AS overall_change,
            COALESCE(c.swim       - ya.swim,       0) AS swim_change,
            COALESCE(c.bike       - ya.bike,       0) AS bike_change,
            COALESCE(c.run        - ya.run,        0) AS run_change,
            COALESCE(c.transition - ya.transition, 0) AS transition_change
        FROM athletes a
        JOIN nationalities n      ON a.country_full = n.country_full
        JOIN current c            ON a.athlete_id = c.athlete_id
        JOIN current_rank cr      ON a.athlete_id = cr.athlete_id
        LEFT JOIN year_ago ya     ON a.athlete_id = ya.athlete_id
        LEFT JOIN athlete_stats s ON a.athlete_id = s.athlete_id
        WHERE {where}
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
    """
    params += [limit, offset]

    cols = [
        "athlete_id", "name", "year_of_birth", "country_alpha3", "country_full",
        "profile_img",
        "overall_rating", "swim_rating", "bike_rating", "run_rating", "transition_rating",
        "world_overall", "world_swim", "world_bike", "world_run", "world_transition",
        "active_world_overall", "active_world_swim", "active_world_bike", "active_world_run", "active_world_transition",
        "active", "race_starts", "wins",
        "overall_change", "swim_change", "bike_change", "run_change", "transition_change",
    ]
    return _dicts(cols, conn.execute(sql, params))


RACE_LEVEL_OPTIONS = {
    'short': [
        ('wtcs',          'WTCS'),
        ('world-cup',     'World Cup'),
        ('conti-cup',     'Continental Cup'),
        ('world-champs',  'World Championships'),
        ('conti-champs',  'Continental Championships'),
        ('olympic',       'Olympic Games'),
        ('u23',           'U23'),
        ('junior',        'Junior / Youth'),
    ],
    'long': [
        ('im',            'Ironman'),
        ('im-703',        'Ironman 70.3'),
        ('t100',          'PTO / T100'),
        ('challenge',     'Challenge'),
        ('long-champs',   'World Championships'),
    ],
    'ag': [
        ('ag-world',      'Age-Group World Champs'),
        ('ag-conti',      'Age-Group Continental Champs'),
    ],
}


def get_relay_leaderboard(disc, order, active_only, offset, limit=100):
    """Country mixed-relay leaderboard: countries ranked by their current
    country-relay {disc} ELO (from country_ratings/country_rankings).

    Relay ratings are a country-level entity, so gender / birth-year / country
    filters don't apply — only discipline, order (top / hot) and active-only do.
    Returns dicts shaped like get_leaderboard rows (name = country, athlete_id
    None) so the leaderboard router/template can render them uniformly."""
    assert disc in _VALID_DISCS and order in _VALID_ORDERS
    conn = _get_conn()
    rating_col = disc
    change_col = f"{disc}_change"
    rank_col   = f"world_{disc}"

    filters = []
    if active_only:
        filters.append("l.last_race_date >= CURRENT_DATE - INTERVAL 18 MONTHS")
    if order == "hot":
        filters.append(f"l.{change_col} != 0")
    where_sql = ("WHERE " + " AND ".join(filters)) if filters else ""
    order_sql = (f"l.{rating_col} DESC, l.country_full ASC" if order == "top"
                 else f"l.{change_col} DESC, l.country_full ASC")

    cols = ["country_full", "country_alpha3", "name", "athlete_id", "year_of_birth",
            "overall_rating", "swim_rating", "bike_rating", "run_rating", "transition_rating",
            "overall_change", "swim_change", "bike_change", "run_change", "transition_change",
            "world_overall", "race_starts", "wins"]
    return _dicts(cols, conn.execute(f"""
        WITH latest AS (
            SELECT DISTINCT ON (cr.country_full)
                   cr.country_full,
                   cr.overall, cr.swim, cr.bike, cr.run, cr.transition,
                   cr.overall_change, cr.swim_change, cr.bike_change,
                   cr.run_change, cr.transition_change,
                   ck.world_overall, ck.{rank_col} AS disc_rank,
                   r.race_date AS last_race_date
            FROM country_ratings cr
            JOIN races r ON cr.race_id = r.race_id
            LEFT JOIN country_rankings ck
              ON ck.race_id = cr.race_id AND ck.country_full = cr.country_full
            ORDER BY cr.country_full, r.race_date DESC, cr.race_id DESC
        ),
        stats AS (
            SELECT rt.country_full,
                   COUNT(*)                             AS race_starts,
                   COUNT(*) FILTER (WHERE rt.position = 1) AS wins
            FROM relay_teams rt
            JOIN races r ON rt.race_id = r.race_id
            WHERE r.sub_category = 'elite' AND rt.status = 'Finished'
            GROUP BY rt.country_full
        )
        SELECT l.country_full, n.alpha3, l.country_full AS name, NULL AS athlete_id, 0 AS year_of_birth,
               l.overall, l.swim, l.bike, l.run, l.transition,
               l.overall_change, l.swim_change, l.bike_change, l.run_change, l.transition_change,
               l.world_overall, COALESCE(s.race_starts, 0), COALESCE(s.wins, 0)
        FROM latest l
        JOIN nationalities n ON n.country_full = l.country_full
        LEFT JOIN stats s ON s.country_full = l.country_full
        {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
    """, [limit, offset]))


def _race_level_filter(level):
    """Returns (extra_joins, where_clause, params) for a level filter, or (None, None, [])."""
    if not level or level == 'all':
        return ("", "", [])
    # Slugs matched against series.slug; tiers against series.tier.
    SERIES_SLUG_LEVELS = {
        'wtcs':         ['wtcs'],
        'world-cup':    ['world-cup', 'dev-regional-cup'],
        'olympic':      ['olympic-games'],
        'world-champs': ['world-championships'],
        'long-champs':  ['im-world-championships', 'im-703-world-championships',
                         'wt-long-distance-championships'],
        'ag-world':     ['ag-world-champs'],
        't100':         ['t100'],
    }
    SERIES_TIER_LEVELS = {
        'conti-cup':    ['continental-cup'],
        'conti-champs': ['continental-championship'],
        'ag-conti':     ['ag-championship'],
    }
    if level in SERIES_SLUG_LEVELS:
        slugs = SERIES_SLUG_LEVELS[level]
        # Join via event_series. T100 also covers brand='t100' to catch events
        # without an explicit series tag.
        ph = ",".join("?" * len(slugs))
        extra = """
            LEFT JOIN event_series es ON es.event_id = r.event_id
            LEFT JOIN series s        ON s.series_id = es.series_id
        """
        if level == 't100':
            return (extra, f"(s.slug IN ({ph}) OR e.brand = 't100')", slugs)
        return (extra, f"s.slug IN ({ph})", slugs)
    if level in SERIES_TIER_LEVELS:
        tiers = SERIES_TIER_LEVELS[level]
        ph = ",".join("?" * len(tiers))
        extra = """
            LEFT JOIN event_series es ON es.event_id = r.event_id
            LEFT JOIN series s        ON s.series_id = es.series_id
        """
        return (extra, f"s.tier IN ({ph})", tiers)
    # Sub-category-based filters (no series join)
    if level == 'junior':
        return ("", "r.sub_category IN ('junior', 'youth')", [])
    if level == 'u23':
        return ("", "r.sub_category = 'u23'", [])
    # Brand-based + distance filters for IM / Challenge
    if level == 'im':
        return ("", "e.brand = 'ironman' AND r.distance = 'long'", [])
    if level == 'im-703':
        return ("", "e.brand = 'ironman' AND r.distance = 'middle'", [])
    if level == 'challenge':
        return ("", "e.brand = 'challenge'", [])
    return ("", "", [])


def get_race_leaderboard(gender, course, disc, year=None, country=None, level=None, offset=0, limit=50):
    """Paginated race leaderboard ranked by standard for `disc` within (gender, course).

    Returns races with race meta, winner, and standards/ranks for all disciplines.
    Races missing a standard for `disc` are excluded from that sort.
    """
    assert disc in _VALID_DISCS
    assert course in ('short', 'long', 'ag')

    conn = _get_conn()
    rank_col = f"{disc}_rank"
    std_col  = f"{disc}_std"

    extra_joins, level_where, level_params = _race_level_filter(level)

    filters = ["rr.gender = ?", "rr.course = ?", f"rr.{std_col} IS NOT NULL"]
    params  = [gender, course]
    if year:
        filters.append("EXTRACT(YEAR FROM r.race_date) = ?")
        params.append(year)
    if country and country != 'all':
        filters.append("e.country = ?")
        params.append(country)
    if level_where:
        filters.append(level_where)
        params.extend(level_params)

    where = " AND ".join(filters)

    # DISTINCT is needed when the optional event_series join is present —
    # a single event may belong to multiple series matching the filter.
    sql = f"""
        SELECT DISTINCT
            r.race_id, r.race_title, r.prog_name, r.race_date, r.gender, r.distance,
            e.venue, e.country,
            n.alpha3 AS event_country_alpha3,
            winner.athlete_id AS winner_id,
            winner.name       AS winner_name,
            winner.country_alpha3 AS winner_country_alpha3,
            rr.overall_std, rr.swim_std, rr.bike_std, rr.run_std, rr.transition_std,
            rr.overall_rank, rr.swim_rank, rr.bike_rank, rr.run_rank, rr.transition_rank
        FROM race_rankings rr
        JOIN races r  ON rr.race_id = r.race_id
        JOIN events e ON r.event_id = e.event_id
        LEFT JOIN nationalities n ON e.country = n.country_full
        {extra_joins}
        LEFT JOIN (
            SELECT res.race_id, a.athlete_id, a.name, nn.alpha3 AS country_alpha3
            FROM results res
            JOIN athletes a       ON res.athlete_id = a.athlete_id
            JOIN nationalities nn ON a.country_full = nn.country_full
            WHERE res.position = 1 AND res.status = 'Finished'
        ) winner ON winner.race_id = r.race_id
        WHERE {where}
        ORDER BY rr.{rank_col} ASC
        LIMIT ? OFFSET ?
    """
    params += [limit, offset]

    cols = [
        "race_id", "race_title", "prog_name", "race_date", "gender", "distance",
        "venue", "country", "event_country_alpha3",
        "winner_id", "winner_name", "winner_country_alpha3",
        "overall_std", "swim_std", "bike_std", "run_std", "transition_std",
        "overall_rank", "swim_rank", "bike_rank", "run_rank", "transition_rank",
    ]
    return _dicts(cols, conn.execute(sql, params))


def get_race_leaderboard_countries(gender, course):
    """Distinct countries hosting races in race_rankings for (gender, course)."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT DISTINCT e.country
        FROM race_rankings rr
        JOIN races r  ON rr.race_id = r.race_id
        JOIN events e ON r.event_id = e.event_id
        WHERE rr.gender = ? AND rr.course = ? AND e.country IS NOT NULL AND e.country <> ''
        ORDER BY e.country
    """, [gender, course]).fetchall()
    return [r[0] for r in rows]


def get_race_leaderboard_years(gender, course):
    """Distinct years present in the race_rankings table for (gender, course)."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT DISTINCT EXTRACT(YEAR FROM r.race_date)::INTEGER AS yr
        FROM race_rankings rr
        JOIN races r ON rr.race_id = r.race_id
        WHERE rr.gender = ? AND rr.course = ?
        ORDER BY yr DESC
    """, [gender, course]).fetchall()
    return [r[0] for r in rows]


def get_race_rankings(race_id):
    """Return race_rankings row for a race, or None."""
    conn = _get_conn()
    row = conn.execute("""
        SELECT gender, course,
               overall_std, swim_std, bike_std, run_std, transition_std,
               overall_rank, swim_rank, bike_rank, run_rank, transition_rank
        FROM race_rankings WHERE race_id = ?
    """, [race_id]).fetchone()
    if not row:
        return None
    cols = ["gender", "course",
            "overall_std", "swim_std", "bike_std", "run_std", "transition_std",
            "overall_rank", "swim_rank", "bike_rank", "run_rank", "transition_rank"]
    return dict(zip(cols, row))


def get_upcoming_race_ranks(gender, course, standards):
    """Live rank for an upcoming race against existing race_rankings (gender, course).

    standards: dict of discipline -> pre-race standard value (or 0/None to skip).
    Returns {disc: rank or None}. Rank = 1 + count of past races with higher std.
    """
    conn = _get_conn()
    out = {}
    for disc in ("overall", "swim", "bike", "run", "transition"):
        val = standards.get(disc)
        if not val:
            out[disc] = None
            continue
        col = f"{disc}_std"
        row = conn.execute(
            f"SELECT COUNT(*) FROM race_rankings "
            f"WHERE gender = ? AND course = ? AND {col} IS NOT NULL AND {col} > ?",
            [gender, course, val],
        ).fetchone()
        out[disc] = (row[0] or 0) + 1
    return out


def get_race_rankings_total(gender, course):
    """Total races in the (gender, course) bucket — used for 'rank X of N' display."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM race_rankings WHERE gender = ? AND course = ?",
        [gender, course],
    ).fetchone()
    return row[0] if row else 0


def get_country_list():
    """Sorted list of all distinct country names in the athletes table."""
    rows = _get_conn().execute(
        "SELECT DISTINCT country_full FROM athletes ORDER BY country_full"
    ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Country pages
# ---------------------------------------------------------------------------

# Tiers that qualify as "championship" medals on the country page.
_CHAMPIONSHIP_TIERS = ["olympic-games", "wtcs", "championship", "im-worlds", "im-703-worlds"]
_CHAMPIONSHIP_LABELS = {
    "olympic-games":  "Olympic Games",
    "wtcs":           "WTCS",
    "championship":   "World Championships",
    "im-worlds":      "Ironman World Championships",
    "im-703-worlds":  "Ironman 70.3 World Championships",
}

# Manual continent overrides. Used when a country hosts no events (so the modal-
# continent-from-events fallback yields 'Other'). Also takes precedence for
# historical/dissolved states whose event history may be miscategorised.
_MANUAL_CONTINENTS = {
    # Europe
    "Czechoslovakia": "Europe", "Moldova": "Europe", "Armenia": "Europe",
    "San Marino": "Europe", "Albania": "Europe", "Andorra": "Europe",
    "Bosnia and Herzegovina": "Europe", "Yugoslavia": "Europe",
    "Soviet Union": "Europe",
    "Federal Republic of Germany": "Europe",
    "Democratic Republic of Germany": "Europe",
    "Greenland": "Europe", "Isle of Man": "Europe",
    # Asia
    "Republic of Korea": "Asia", "Iraq": "Asia", "Kuwait": "Asia",
    "Syria": "Asia", "Lebanon": "Asia", "Myanmar (Burma)": "Asia",
    "Cambodia": "Asia", "Bangladesh": "Asia", "Saudi Arabia": "Asia",
    "Vietnam": "Asia", "Palestine": "Asia", "Maldives": "Asia",
    "Pakistan": "Asia", "Yemen": "Asia",
    "Democratic People's Republic of Korea": "Asia",
    "Afghanistan": "Asia", "Brunei Darussalam": "Asia",
    # Americas
    "Trinidad and Tobago": "Americas", "Paraguay": "Americas",
    "Saint Kitts and Nevis": "Americas", "Belize": "Americas",
    "Netherlands Antilles": "Americas", "Antigua and Barbuda": "Americas",
    "Suriname": "Americas", "Dominica": "Americas", "Guyana": "Americas",
    "Haiti": "Americas", "Saint Vincent and the Grenadines": "Americas",
    # Africa
    "Libya": "Africa", "Nigeria": "Africa", "Cote d'Ivoire": "Africa",
    "Niger": "Africa", "Seychelles": "Africa", "Sudan": "Africa",
    "Djibouti": "Africa", "Burundi": "Africa", "Cameroon": "Africa",
    "Comoros": "Africa", "Sierra Leone": "Africa", "Swaziland": "Africa",
    "Uganda": "Africa",
    "United Republic of Tanzania": "Africa", "Guinea": "Africa",
    "Central African Republic": "Africa", "The Gambia": "Africa",
    "Togo": "Africa",
    # Oceania
    "Norfolk Island": "Oceania", "American Samoa": "Oceania",
    "Northern Mariana Islands": "Oceania", "Tonga": "Oceania",
    "Kiribati": "Oceania", "Vanuatu": "Oceania",
    # World Triathlon is a federation banner, not a country - leave in Other.
}


def get_alpha3_for_country(country_full):
    """alpha3 string for a country name, or None."""
    row = _get_conn().execute(
        "SELECT alpha3 FROM nationalities WHERE country_full = ?", [country_full]
    ).fetchone()
    return row[0] if row else None


def get_country_by_alpha3(alpha3):
    """Return {country_full, alpha3, athlete_count, race_host_count} or None.

    athlete_count counts everyone who has *ever* represented this alpha3 (current
    country or any nationality-history row), matching the leaderboard. Several
    country_full spellings can share one alpha3, so we pick the most-used spelling
    as the display name and aggregate host events across all of them.
    """
    conn = _get_conn()
    row = conn.execute("""
        WITH spellings AS (SELECT country_full FROM nationalities WHERE alpha3 = ?)
        SELECT
            (SELECT country_full FROM athletes
              WHERE country_full IN (SELECT country_full FROM spellings)
              GROUP BY country_full ORDER BY COUNT(*) DESC LIMIT 1) AS by_athletes,
            (SELECT country_full FROM spellings ORDER BY country_full LIMIT 1) AS any_spelling,
            (SELECT COUNT(DISTINCT aid) FROM (
                SELECT a.athlete_id AS aid FROM athletes a
                JOIN nationalities n ON a.country_full = n.country_full WHERE n.alpha3 = ?
                UNION
                SELECT h.athlete_id AS aid FROM athlete_nationality_history h
                JOIN nationalities n ON h.country_full = n.country_full WHERE n.alpha3 = ?
            )) AS athlete_count,
            (SELECT COUNT(*) FROM events e WHERE e.country IN (SELECT country_full FROM spellings)) AS race_host_count
    """, [alpha3, alpha3, alpha3]).fetchone()
    if not row or (row[0] is None and row[1] is None):
        return None
    cols = ["country_full", "athlete_count", "race_host_count"]
    result = dict(zip(cols, (row[0] or row[1], row[2], row[3])))
    result["alpha3"] = alpha3
    return result


def get_countries_with_counts():
    """Countries with athlete and race-host counts plus modal event-continent.

    One row per row in `nationalities`. Countries with zero athletes are included.
    Continent is taken from the most common `events.continent` for races hosted
    in that country; countries that host no events fall back to 'Other'.

    Top-athlete headline picks the best athlete per country + gender looking at
    short and long course together:
      1. Active on either course (last raced within 18 months). Pick the
         athlete with the lowest `active_world_overall`; record that course
         for the UI badge.
      2. No active athletes. Fallback to the highest current-rating athlete
         across either course. No rank is emitted so the UI omits "WR #N".
    """
    conn = _get_conn()
    rows = conn.execute("""
        WITH ath AS (
            SELECT country_full, COUNT(*) AS athlete_count
            FROM athletes
            GROUP BY country_full
        ),
        ev AS (
            SELECT country, COUNT(*) AS race_host_count
            FROM events
            GROUP BY country
        ),
        cont AS (
            SELECT country, continent
            FROM (
                SELECT country, continent, COUNT(*) AS n,
                       ROW_NUMBER() OVER (PARTITION BY country ORDER BY COUNT(*) DESC) AS rn
                FROM events
                GROUP BY country, continent
            )
            WHERE rn = 1
        ),
        -- Latest elite rating per athlete per course (short vs long).
        latest_rating AS (
            SELECT DISTINCT ON (ra.athlete_id, course)
                   ra.athlete_id,
                   CASE WHEN rc.distance IN ('long','middle','t100') THEN 'long' ELSE 'short' END AS course,
                   ra.overall,
                   rc.race_date AS last_race_date
            FROM ratings ra
            JOIN races rc ON rc.race_id = ra.race_id
            WHERE ra.category = 'elite'
            ORDER BY ra.athlete_id, course, rc.race_date DESC, ra.race_id DESC
        ),
        -- Latest world rank per athlete per course.
        latest_rank AS (
            SELECT DISTINCT ON (rk.athlete_id, course)
                   rk.athlete_id,
                   CASE WHEN rc.distance IN ('long','middle','t100') THEN 'long' ELSE 'short' END AS course,
                   rk.world_overall, rk.active_world_overall
            FROM rankings rk
            JOIN races rc ON rc.race_id = rk.race_id
            WHERE rc.category = 'elite'
            ORDER BY rk.athlete_id, course, rc.race_date DESC, rk.race_id DESC
        ),
        -- Active candidates: ranked, recent race in last 18 months.
        active_cands AS (
            SELECT a.athlete_id, a.country_full, a.gender, a.name, a.profile_img,
                   lk.course, lk.active_world_overall AS rank_val, lr.overall
            FROM athletes a
            JOIN latest_rank   lk ON lk.athlete_id = a.athlete_id
            JOIN latest_rating lr ON lr.athlete_id = a.athlete_id AND lr.course = lk.course
            WHERE a.gender IN ('male', 'female')
              AND lk.active_world_overall IS NOT NULL
              AND lr.last_race_date >= CURRENT_DATE - INTERVAL 18 MONTHS
        ),
        active_pick AS (
            SELECT country_full, gender, athlete_id, name, profile_img,
                   course, rank_val, overall
            FROM (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY country_full, gender
                           ORDER BY rank_val ASC
                       ) AS rn
                FROM active_cands
            )
            WHERE rn = 1
        ),
        -- Non-active fallback: highest rating across either course.
        inactive_cands AS (
            SELECT a.athlete_id, a.country_full, a.gender, a.name, a.profile_img,
                   lr.course, lr.overall
            FROM athletes a
            JOIN latest_rating lr ON lr.athlete_id = a.athlete_id
            WHERE a.gender IN ('male', 'female')
        ),
        inactive_pick AS (
            SELECT country_full, gender, athlete_id, name, profile_img, course, overall
            FROM (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY country_full, gender
                           ORDER BY overall DESC
                       ) AS rn
                FROM inactive_cands
            )
            WHERE rn = 1
        ),
        -- Merge: prefer the active pick; fall back to the inactive pick.
        topath AS (
            SELECT COALESCE(ap.country_full, ip.country_full) AS country_full,
                   COALESCE(ap.gender, ip.gender)             AS gender,
                   COALESCE(ap.athlete_id, ip.athlete_id)     AS athlete_id,
                   COALESCE(ap.name, ip.name)                 AS name,
                   COALESCE(ap.profile_img, ip.profile_img)   AS profile_img,
                   COALESCE(ap.course, ip.course)             AS course,
                   COALESCE(ap.overall, ip.overall)           AS overall,
                   ap.rank_val                                AS world_overall
            FROM active_pick ap
            FULL OUTER JOIN inactive_pick ip
              ON ap.country_full = ip.country_full AND ap.gender = ip.gender
        )
        SELECT n.country_full, n.alpha3,
               COALESCE(ath.athlete_count, 0),
               COALESCE(ev.race_host_count, 0),
               COALESCE(cont.continent, 'Other'),
               tm.athlete_id, tm.name, tm.profile_img, tm.overall, tm.world_overall, tm.course,
               tf.athlete_id, tf.name, tf.profile_img, tf.overall, tf.world_overall, tf.course
        FROM nationalities n
        LEFT JOIN ath    ON ath.country_full    = n.country_full
        LEFT JOIN ev     ON ev.country          = n.country_full
        LEFT JOIN cont   ON cont.country        = n.country_full
        LEFT JOIN topath tm ON tm.country_full  = n.country_full AND tm.gender = 'male'
        LEFT JOIN topath tf ON tf.country_full  = n.country_full AND tf.gender = 'female'
        ORDER BY COALESCE(ath.athlete_count, 0) DESC, n.country_full
    """).fetchall()
    cols = ["country_full", "alpha3",
            "athlete_count", "race_host_count", "continent",
            "top_male_id",   "top_male_name",   "top_male_img",   "top_male_rating",   "top_male_rank",   "top_male_course",
            "top_female_id", "top_female_name", "top_female_img", "top_female_rating", "top_female_rank", "top_female_course"]
    out = [dict(zip(cols, r)) for r in rows]
    for d in out:
        if d["continent"] in ("", "Other"):
            d["continent"] = _MANUAL_CONTINENTS.get(d["country_full"], "Other")
    return out


def get_country_leaderboard(alpha3, gender, discipline="overall",
                            limit=20, offset=0, active_only=True, category="elite", course='short'):
    """Top athletes who have *ever* represented this country (by alpha3).

    Thin wrapper over `get_leaderboard` that pre-sets `country_alpha3`,
    `order='top'`, and no year-of-birth filters. Keyed on alpha3 (not a single
    country_full spelling) so home nations and mid-career switches all resolve.
    """
    assert discipline in _VALID_DISCS
    return get_leaderboard(
        gender=gender, disc=discipline, order="top",
        country=None, country_alpha3=alpha3, yob_start=None, yob_end=None,
        active_only=active_only, offset=offset, limit=limit, category=category, course=course,
    )


def get_country_relay_summary(country_full):
    """Current mixed relay rating/ranking snapshot for a country, plus career
    stats (races, wins, podiums). None if the country has no relay history."""
    conn = _get_conn()
    row = conn.execute("""
        SELECT cr.overall, cr.swim, cr.bike, cr.run, cr.transition,
               ck.world_overall, ck.active_world_overall, r.race_date
        FROM country_ratings cr
        JOIN races r ON cr.race_id = r.race_id
        LEFT JOIN country_rankings ck
          ON ck.race_id = cr.race_id AND ck.country_full = cr.country_full
        WHERE cr.country_full = ?
        ORDER BY r.race_date DESC, cr.race_id DESC
        LIMIT 1
    """, [country_full]).fetchone()
    if not row:
        return None

    stats = conn.execute("""
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE position = 1),
               COUNT(*) FILTER (WHERE position IN (1, 2, 3))
        FROM relay_teams rt
        JOIN races r ON rt.race_id = r.race_id
        WHERE rt.country_full = ? AND r.sub_category = 'elite'
          AND rt.status = 'Finished'
    """, [country_full]).fetchone()

    return {
        "overall_rating": row[0], "swim_rating": row[1], "bike_rating": row[2],
        "run_rating": row[3], "transition_rating": row[4],
        "world_overall": row[5], "active_world_overall": row[6],
        "last_race_date": row[7],
        "race_count": stats[0], "wins": stats[1], "podiums": stats[2],
    }


def get_country_relay_results(country_full, limit=8):
    """The country's most recent elite mixed relay team results."""
    conn = _get_conn()
    cols = ["race_id", "race_title", "race_handle", "race_date", "team_title",
            "team_num", "position", "status", "total_s"]
    rows = _dicts(cols, conn.execute("""
        SELECT rt.race_id, r.race_title, r.race_handle, r.race_date,
               rt.team_title, rt.team_num, rt.position, rt.status, rt.total_s
        FROM relay_teams rt
        JOIN races r ON rt.race_id = r.race_id
        WHERE rt.country_full = ? AND r.sub_category = 'elite'
        ORDER BY r.race_date DESC, rt.team_num
        LIMIT ?
    """, [country_full, limit]))
    for r in rows:
        r["team_name"] = relay_team_name(country_full, r["team_num"])
    return rows


def get_country_hosted_race_locations(country_full):
    """Events hosted in this country with coords. For the country page map."""
    conn = _get_conn()
    cols = ["event_id", "event_name", "venue", "latitude", "longitude", "start_date"]
    return _dicts(cols, conn.execute("""
        SELECT event_id, name, venue, latitude, longitude, start_date
        FROM events
        WHERE country = ?
          AND latitude <> 0 AND longitude <> 0
        ORDER BY start_date DESC
    """, [country_full]))


def get_country_championship_medals(country_full):
    """Gold/silver/bronze counts for this country's athletes at championship series.

    Buckets: olympic-games, commonwealth-games, wtcs, championship (= World Champs).
    Returns list of dicts in display order; rows with zero medals are filtered out.
    """
    conn = _get_conn()
    tier_ph = ",".join(["?"] * len(_CHAMPIONSHIP_TIERS))
    rows = conn.execute(f"""
        SELECT s.tier,
               COUNT(CASE WHEN res.position = 1 THEN 1 END) AS gold,
               COUNT(CASE WHEN res.position = 2 THEN 1 END) AS silver,
               COUNT(CASE WHEN res.position = 3 THEN 1 END) AS bronze
        FROM results res
        JOIN athletes a      ON a.athlete_id = res.athlete_id
        JOIN races r         ON r.race_id = res.race_id
        JOIN event_series es ON es.event_id = r.event_id
        JOIN series s        ON s.series_id = es.series_id
        WHERE a.country_full = ?
          AND res.status = 'Finished'
          AND res.position IN (1, 2, 3)
          AND r.sub_category = 'elite'
          AND s.tier IN ({tier_ph})
        GROUP BY s.tier
    """, [country_full] + _CHAMPIONSHIP_TIERS).fetchall()

    by_tier = {r[0]: (r[1], r[2], r[3]) for r in rows}
    medals = []
    for tier in _CHAMPIONSHIP_TIERS:
        counts = by_tier.get(tier)
        if not counts or not any(counts):
            continue
        gold, silver, bronze = counts
        medals.append({
            "tier":   tier,
            "label":  _CHAMPIONSHIP_LABELS[tier],
            "gold":   gold,
            "silver": silver,
            "bronze": bronze,
            "total":  gold + silver + bronze,
        })
    return medals


# ---------------------------------------------------------------------------
# Athlete page
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4096)
def get_athlete_info(athlete_id):
    """Basic athlete info: name, country, yob, gender, profile_img.

    `country_full` here is derived from athlete_nationality_history (latest
    non-home-nation row) rather than the athletes.country_full cache, which
    DuckDB FK constraints prevent us from reliably updating after merges or
    nationality switches. England / Scotland / Wales are skipped — those are
    one-off Commonwealth Games observations, not the athlete's real federation.
    """
    conn = _get_conn()
    row = conn.execute("""
        WITH latest AS (
            SELECT athlete_id, country_full,
                   ROW_NUMBER() OVER (PARTITION BY athlete_id ORDER BY start_date DESC) AS rn
            FROM athlete_nationality_history
            WHERE country_full NOT IN ('England', 'Scotland', 'Wales')
        )
        SELECT a.athlete_id, a.name,
               COALESCE(l.country_full, a.country_full) AS country_full,
               a.year_of_birth, a.gender, a.profile_img,
               n.alpha3 AS country_alpha3,
               a.height_cm, a.weight_kg, a.nickname
        FROM athletes a
        LEFT JOIN latest l ON l.athlete_id = a.athlete_id AND l.rn = 1
        JOIN nationalities n ON n.country_full = COALESCE(l.country_full, a.country_full)
        WHERE a.athlete_id = ?
    """, [athlete_id]).fetchone()
    if not row:
        return None
    cols = ["athlete_id", "name", "country_full", "year_of_birth",
            "gender", "profile_img", "country_alpha3",
            "height_cm", "weight_kg", "nickname"]
    return dict(zip(cols, row))


def get_athlete_categories(athlete_id, course='short'):
    """Return list of categories this athlete has ratings for in the given course."""
    course_in = _course_in(course)
    rows = _get_conn().execute(f"""
        SELECT DISTINCT ra.category
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        WHERE ra.athlete_id = ? AND r.distance IN {course_in}
        ORDER BY ra.category
    """, [athlete_id]).fetchall()
    return [r[0] for r in rows]


def get_athlete_courses(athlete_id):
    """Return ordered list of courses this athlete has ratings for, e.g. ['short'], ['long'], ['short','long']."""
    rows = _get_conn().execute("""
        SELECT DISTINCT CASE
                 WHEN r.distance IN ('sprint','standard')      THEN 'short'
                 WHEN r.distance IN ('middle','t100','long')   THEN 'long'
               END AS course
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        WHERE ra.athlete_id = ?
    """, [athlete_id]).fetchall()
    courses = {r[0] for r in rows if r[0]}
    # Stable order: short first, long second
    return [c for c in ('short', 'long') if c in courses]


def get_athlete_last_race_per_course(athlete_id):
    """Return {course: last_race_date} across both courses (any category).

    Used to pick the default course when an athlete profile is opened without
    an explicit `course=` query param: prefer whichever course they raced in
    most recently.
    """
    rows = _get_conn().execute("""
        SELECT
            CASE
                WHEN r.distance IN ('sprint','standard')    THEN 'short'
                WHEN r.distance IN ('middle','t100','long') THEN 'long'
            END AS course,
            MAX(r.race_date) AS last_race_date
        FROM results res
        JOIN races r ON r.race_id = res.race_id
        WHERE res.athlete_id = ?
        GROUP BY 1
    """, [athlete_id]).fetchall()
    return {c: d for (c, d) in rows if c is not None}


def get_athlete_programs(athlete_id):
    """Return the ordered list of programs an athlete has ratings for.

    Programs are the comparable (course, category) combos: 'elite-short',
    'elite-long', 'ag'. Order is stable so callers can display predictably.
    """
    row = _get_conn().execute(f"""
        WITH {_TAGS_CTE}
        SELECT has_elite_short, has_elite_long, has_ag
        FROM tags
        WHERE athlete_id = ?
    """, [athlete_id]).fetchone()
    if not row:
        return []
    has_elite_short, has_elite_long, has_ag = row
    programs = []
    if has_elite_short: programs.append('elite-short')
    if has_elite_long:  programs.append('elite-long')
    if has_ag:          programs.append('ag')
    return programs


def get_athlete_doping_ban(athlete_id):
    """The athlete's doping sanction record, or None.

    Source is data/doping_bans.csv (loaded into doping_bans). Returns the raw
    summary/evidence fields plus a display-ready `period` string (year range,
    or single year, or empty) for the banner.
    """
    row = _get_conn().execute("""
        SELECT substance, sanction_start, sanction_end, summary, evidence_url, source
        FROM doping_bans
        WHERE athlete_id = ?
    """, [athlete_id]).fetchone()
    if not row:
        return None
    substance, start, end, summary, evidence_url, source = row
    start_yr = start.year if start else None
    end_yr = end.year if end else None
    if start_yr and end_yr and start_yr != end_yr:
        period = f"{start_yr}–{end_yr}"
    elif start_yr:
        period = str(start_yr)
    else:
        period = ""
    return {
        "substance":    substance,
        "period":       period,
        "summary":      summary,
        "evidence_url": evidence_url,
        "source":       source,
    }


def get_athlete_nationality_history(athlete_id):
    """Ordered list of countries the athlete has represented, with date ranges.

    Returns most-recent-first. The current country has end_date = None.
    Joins nationalities so the UI has the alpha3 for the flag + country link.

    Excludes England / Scotland / Wales: those entries come from one-off
    Commonwealth Games observations rather than permanent federation
    transfers, and clutter the displayed timeline. Rows stay in the DB.

    After filtering, adjacent same-country runs are merged so we don't
    show e.g. two "Great Britain" entries either side of a removed home-
    nation slice.
    """
    rows = _get_conn().execute("""
        SELECT h.country_full, n.alpha3, h.start_date, h.end_date
        FROM athlete_nationality_history h
        JOIN nationalities n ON h.country_full = n.country_full
        WHERE h.athlete_id = ?
          AND h.country_full NOT IN ('England', 'Scotland', 'Wales')
        ORDER BY h.start_date ASC
    """, [athlete_id]).fetchall()

    merged = []
    for cf, a3, sd, ed in rows:
        if merged and merged[-1][0] == cf:
            prev_cf, prev_a3, prev_sd, prev_ed = merged[-1]
            new_ed = None if (prev_ed is None or ed is None) else max(prev_ed, ed)
            merged[-1] = (prev_cf, prev_a3, prev_sd, new_ed)
        else:
            merged.append((cf, a3, sd, ed))

    cols = ["country_full", "country_alpha3", "start_date", "end_date"]
    return [dict(zip(cols, r)) for r in reversed(merged)]


@lru_cache(maxsize=8192)
def get_athlete_current_ratings(athlete_id, category='elite', course='short'):
    """Latest rating + world/national ranking for all 5 disciplines, scoped to course."""
    conn = _get_conn()
    course_in = _course_in(course)
    rating = conn.execute(f"""
        SELECT ra.overall, ra.swim, ra.bike, ra.run, ra.transition
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        WHERE ra.athlete_id = ? AND ra.category = ? AND r.distance IN {course_in}
        ORDER BY r.race_date DESC, ra.race_id DESC
        LIMIT 1
    """, [athlete_id, category]).fetchone()

    ranking = conn.execute(f"""
        SELECT rk.world_overall, rk.world_swim, rk.world_bike, rk.world_run, rk.world_transition,
               rk.national_overall, rk.national_swim, rk.national_bike, rk.national_run, rk.national_transition
        FROM rankings rk
        JOIN races r ON rk.race_id = r.race_id
        WHERE rk.athlete_id = ? AND rk.category = ? AND r.distance IN {course_in}
        ORDER BY r.race_date DESC, rk.race_id DESC
        LIMIT 1
    """, [athlete_id, category]).fetchone()

    if not rating:
        return None

    result = {
        "overall_rating":    rating[0],
        "swim_rating":       rating[1],
        "bike_rating":       rating[2],
        "run_rating":        rating[3],
        "transition_rating": rating[4],
    }
    if ranking:
        result.update({
            "world_overall":    ranking[0], "world_swim":    ranking[1],
            "world_bike":       ranking[2], "world_run":     ranking[3],
            "world_transition": ranking[4],
            "national_overall": ranking[5], "national_swim": ranking[6],
            "national_bike":    ranking[7], "national_run":  ranking[8],
            "national_transition": ranking[9],
        })
    return result


@lru_cache(maxsize=8192)
def get_athlete_active_rankings(athlete_id, category='elite', course='short'):
    """
    Rank among currently active athletes (raced in last 18 months), same gender and category.
    Returns None if the athlete themselves is not active. Course-scoped.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    row = conn.execute(f"""
        WITH current AS (
            SELECT DISTINCT ON (ra.athlete_id)
                   ra.athlete_id,
                   ra.overall, ra.swim, ra.bike, ra.run, ra.transition,
                   r.race_date AS last_race_date
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE ra.category = ? AND r.distance IN {course_in}
            ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
        ),
        active AS (
            SELECT c.athlete_id, c.overall, c.swim, c.bike, c.run, c.transition,
                   a.country_full
            FROM current c
            JOIN athletes a ON c.athlete_id = a.athlete_id
            WHERE c.last_race_date >= CURRENT_DATE - INTERVAL 18 MONTHS
              AND a.gender = (SELECT gender FROM athletes WHERE athlete_id = ?)
        ),
        me AS (SELECT * FROM active WHERE athlete_id = ?)
        SELECT
            (SELECT COUNT(*) + 1 FROM active aa WHERE aa.overall    > me.overall)    AS world_overall,
            (SELECT COUNT(*) + 1 FROM active aa WHERE aa.swim       > me.swim)       AS world_swim,
            (SELECT COUNT(*) + 1 FROM active aa WHERE aa.bike       > me.bike)       AS world_bike,
            (SELECT COUNT(*) + 1 FROM active aa WHERE aa.run        > me.run)        AS world_run,
            (SELECT COUNT(*) + 1 FROM active aa WHERE aa.transition > me.transition) AS world_transition,
            (SELECT COUNT(*) + 1 FROM active aa WHERE aa.overall    > me.overall    AND aa.country_full = me.country_full) AS national_overall,
            (SELECT COUNT(*) + 1 FROM active aa WHERE aa.swim       > me.swim       AND aa.country_full = me.country_full) AS national_swim,
            (SELECT COUNT(*) + 1 FROM active aa WHERE aa.bike       > me.bike       AND aa.country_full = me.country_full) AS national_bike,
            (SELECT COUNT(*) + 1 FROM active aa WHERE aa.run        > me.run        AND aa.country_full = me.country_full) AS national_run,
            (SELECT COUNT(*) + 1 FROM active aa WHERE aa.transition > me.transition AND aa.country_full = me.country_full) AS national_transition
        FROM me
    """, [category, athlete_id, athlete_id]).fetchone()

    if not row:
        return None
    cols = ["world_overall", "world_swim", "world_bike", "world_run", "world_transition",
            "national_overall", "national_swim", "national_bike", "national_run", "national_transition"]
    return dict(zip(cols, row))


def get_athlete_peak_rankings(athlete_id, category='elite', course='short'):
    """
    Best (lowest-number) world ranking the athlete ever held in each discipline,
    based on per-race rankings rows. Used for retired athletes where current
    active-rankings would return None. Course-scoped.

    Returns None if the athlete has no ranking history.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    row = conn.execute(f"""
        SELECT MIN(rk.world_overall)    AS world_overall,
               MIN(rk.world_swim)       AS world_swim,
               MIN(rk.world_bike)       AS world_bike,
               MIN(rk.world_run)        AS world_run,
               MIN(rk.world_transition) AS world_transition
        FROM rankings rk
        JOIN races r ON rk.race_id = r.race_id
        WHERE rk.athlete_id = ? AND rk.category = ? AND r.distance IN {course_in}
    """, [athlete_id, category]).fetchone()

    if not row or row[0] is None:
        return None
    cols = ["world_overall", "world_swim", "world_bike", "world_run", "world_transition"]
    return dict(zip(cols, row))


def get_athlete_1yr_changes(athlete_id, category='elite', course='short'):
    """Rating change over the past year per discipline. None if no data. Course-scoped."""
    conn = _get_conn()
    course_in = _course_in(course)
    current = conn.execute(f"""
        SELECT ra.overall, ra.swim, ra.bike, ra.run, ra.transition
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        WHERE ra.athlete_id = ? AND ra.category = ? AND r.distance IN {course_in}
        ORDER BY r.race_date DESC, ra.race_id DESC
        LIMIT 1
    """, [athlete_id, category]).fetchone()

    if not current:
        return {k: None for k in
                ["overall_change_1yr", "swim_change_1yr", "bike_change_1yr",
                 "run_change_1yr", "transition_change_1yr"]}

    year_ago = conn.execute(f"""
        SELECT ra.overall, ra.swim, ra.bike, ra.run, ra.transition
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        WHERE ra.athlete_id = ? AND ra.category = ? AND r.distance IN {course_in}
          AND r.race_date <= CURRENT_DATE - INTERVAL 1 YEAR
        ORDER BY r.race_date DESC, ra.race_id DESC
        LIMIT 1
    """, [athlete_id, category]).fetchone()

    if not year_ago:
        return {k: None for k in
                ["overall_change_1yr", "swim_change_1yr", "bike_change_1yr",
                 "run_change_1yr", "transition_change_1yr"]}

    return {
        "overall_change_1yr":    current[0] - year_ago[0],
        "swim_change_1yr":       current[1] - year_ago[1],
        "bike_change_1yr":       current[2] - year_ago[2],
        "run_change_1yr":        current[3] - year_ago[3],
        "transition_change_1yr": current[4] - year_ago[4],
    }


def get_athlete_peak_ratings(athlete_id, category='elite', course='short'):
    """Max rating per discipline + race handle + race_id where it was achieved. Course-scoped."""
    conn = _get_conn()
    course_in = _course_in(course)
    discs = ["overall", "swim", "bike", "run", "transition"]
    result = {}
    for disc in discs:
        row = conn.execute(f"""
            SELECT ra.{disc}, r.race_handle, r.race_id
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE ra.athlete_id = ? AND ra.category = ? AND r.distance IN {course_in}
            ORDER BY ra.{disc} DESC
            LIMIT 1
        """, [athlete_id, category]).fetchone()
        result[f"max_{disc}"]         = row[0] if row else 0
        result[f"max_{disc}_race"]    = row[1] if row else ""
        result[f"max_{disc}_race_id"] = row[2] if row else 0
    return result


def get_athlete_best_performances(athlete_id, category='elite', course='short'):
    """
    Best rating change per discipline + the race handle. Course-scoped.
    Prefers the largest positive change; falls back to the least negative
    change if the athlete has never had a positive one for that discipline.

    Excludes races where the athlete had no recorded time for the relevant
    discipline - a missing split can't be a "best performance" even if the
    ratings row carries a non-zero change rolled in from elsewhere. For the
    transition discipline both T1 and T2 must be present.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    # Per-discipline split-time gate. The ratings table doesn't know whether
    # a split was real or zero-filled, so we cross-check against results.
    DISC_GATE = {
        "overall":    "res.overall_s > 0",
        "swim":       "res.swim_s    > 0",
        "bike":       "res.bike_s    > 0",
        "run":        "res.run_s     > 0",
        "transition": "res.t1_s > 0 AND res.t2_s > 0",
    }
    result = {}
    for disc, gate in DISC_GATE.items():
        row = conn.execute(f"""
            SELECT ra.{disc}_change, r.race_handle, ra.race_id
            FROM ratings ra
            JOIN races   r   ON r.race_id  = ra.race_id
            JOIN results res ON res.race_id = ra.race_id AND res.athlete_id = ra.athlete_id
            WHERE ra.athlete_id = ? AND ra.category = ? AND r.distance IN {course_in}
              AND ra.{disc}_change IS NOT NULL
              AND ra.{disc}_change <> 0
              AND {gate}
            ORDER BY ra.{disc}_change DESC
            LIMIT 1
        """, [athlete_id, category]).fetchone()
        if row:
            result[f"{disc}_change"]    = row[0]
            result[f"{disc}_race"]      = row[1]
            result[f"{disc}_race_id"]   = row[2]
        else:
            result[f"{disc}_change"]    = None
            result[f"{disc}_race"]      = ""
            result[f"{disc}_race_id"]   = None
    return result


# Notable result category IDs (from WorldTriathlon API)
_NOTABLE_CAT_IDS = {624, 348, 351, 349, 341, 343}
_AG_CAT_ID = 483


def get_athlete_notable_results(athlete_id):
    """
    Short-course results at Olympic / WC / WTCS / World Cup / Continental Cup races.
    Returns list of dicts: {tier, position, race_id, race_handle, race_date, age_group}
    age_group is "U23" / "Junior" for non-Elite world champs, else None.
    Only short-course races are considered; long-course palmares comes from
    `get_athlete_long_course_notable_results`.
    """
    conn = _get_conn()
    # Exclude ignored_races so multi-stage events only contribute their combined
    # Elite Men/Women rollup row — winning a semifinal AND the final must count
    # as a single win, not two.
    rows = conn.execute("""
        SELECT res.race_id, res.position, r.race_title, r.race_handle, r.cat_ids, r.race_date, r.prog_name
        FROM results res
        JOIN races r ON res.race_id = r.race_id
        WHERE res.athlete_id = ?
          AND res.status = 'Finished'
          AND res.position IS NOT NULL
          AND r.distance IN ('sprint', 'standard')
          AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
        ORDER BY res.position
    """, [athlete_id]).fetchall()

    notable = []
    for race_id, position, race_title, race_handle, cat_ids_str, race_date, prog_name in rows:
        try:
            cat_ids = set(literal_eval(cat_ids_str))
        except (ValueError, SyntaxError):
            continue

        # AG events are handled separately
        if _AG_CAT_ID in cat_ids:
            continue

        # French Grand Prix (FFTRI national club series) — no WT cat_ids, so
        # identified by its event title. Displayed like a continental cup but
        # capped at top-10 (see _TIER_POS_CAPS).
        if "French Grand Prix" in race_title:
            notable.append({"tier": "french_grand_prix", "position": position,
                             "race_id": race_id, "race_handle": race_handle,
                             "race_date": race_date})
            continue

        title_lower = race_title.lower()

        # Major Games (343): Olympic only if title contains "olympic" but NOT "youth"
        if 343 in cat_ids:
            if "olympic" in title_lower and "youth" not in title_lower:
                notable.append({"tier": "olympic", "position": position,
                                 "race_id": race_id, "race_handle": race_handle, "race_date": race_date})
                continue
            else:
                cat_ids.discard(343)
                cat_ids.add(340)

        if 624 in cat_ids or 348 in cat_ids:
            # Derive age group from prog_name so we can label "U23 World Champion" etc.
            prog = prog_name or ""
            if prog.startswith("U23"):
                age_group = "U23"
            elif prog.startswith("Junior"):
                age_group = "Junior"
            else:
                age_group = None  # Elite - no prefix
            notable.append({"tier": "world_champs", "position": position,
                             "race_id": race_id, "race_handle": race_handle,
                             "race_date": race_date, "age_group": age_group})
        elif 351 in cat_ids:
            notable.append({"tier": "wtcs", "position": position,
                             "race_id": race_id, "race_handle": race_handle, "race_date": race_date})
        elif 349 in cat_ids:
            notable.append({"tier": "world_cup", "position": position,
                             "race_id": race_id, "race_handle": race_handle, "race_date": race_date})
        elif 341 in cat_ids:
            notable.append({"tier": "continental_cup", "position": position,
                             "race_id": race_id, "race_handle": race_handle, "race_date": race_date})

    return notable


def get_athlete_ag_notable_results(athlete_id):
    """AG palmares: world + continental championship results.

    Tiers:
      ag_world_champs        - cat 624/348 (worlds) AND cat 483 (AG)
      ag_continental_champs  - cat 340 (continental champs) AND cat 483 (AG)
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT res.race_id, res.position, r.race_title, r.race_handle, r.cat_ids, r.race_date
        FROM results res
        JOIN races r ON res.race_id = r.race_id
        WHERE res.athlete_id = ?
          AND res.status = 'Finished'
          AND res.position IS NOT NULL
        ORDER BY res.position
    """, [athlete_id]).fetchall()

    notable = []
    for race_id, position, race_title, race_handle, cat_ids_str, race_date in rows:
        try:
            cat_ids = set(literal_eval(cat_ids_str))
        except (ValueError, SyntaxError):
            continue

        if _AG_CAT_ID not in cat_ids:
            continue

        title_lower = (race_title or "").lower()
        common = {"position": position, "race_id": race_id,
                  "race_handle": race_handle, "race_date": race_date}

        # World champs first (worlds outranks continental if both somehow apply)
        if (624 in cat_ids or 348 in cat_ids
                or ("world" in title_lower and "championship" in title_lower)):
            notable.append({"tier": "ag_world_champs", **common})
            continue

        # Continental champs: cat 340. Also accept name-based fallback for
        # the ETU/CAMTRI/ASTC-era titles missing the cat in source data.
        is_continental = (340 in cat_ids
                          or ("european championship" in title_lower)
                          or ("americas championship" in title_lower)
                          or ("asian championship"    in title_lower)
                          or ("oceania championship"  in title_lower)
                          or ("african championship"  in title_lower))
        if is_continental:
            notable.append({"tier": "ag_continental_champs", **common})

    return notable


def get_athlete_long_course_notable_results(athlete_id):
    """
    Long-course results tiered by series/brand.

    Tiers (in display order):
      - im_world_champs      Ironman World Championship (long)
      - im_703_world_champs  Ironman 70.3 World Championship (middle)
      - im                   any other full Ironman
      - t100                 T100 races
      - im_703               any other Ironman 70.3
      - challenge            Challenge series
    Independent long-course events are ignored.

    Returns list of dicts: {tier, position, race_id, race_handle, race_date}.
    Position caps are applied by the router — this function returns every
    categorisable finish.
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT res.race_id, res.position, r.race_title, r.race_handle, r.race_date,
               r.distance, e.brand
        FROM results res
        JOIN races  r ON res.race_id = r.race_id
        JOIN events e ON r.event_id  = e.event_id
        WHERE res.athlete_id = ?
          AND res.status = 'Finished'
          AND res.position IS NOT NULL
          AND r.distance IN ('middle', 't100', 'long')
          AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
        ORDER BY res.position
    """, [athlete_id]).fetchall()

    notable = []
    for race_id, position, race_title, race_handle, race_date, distance, brand in rows:
        title_lower = (race_title or "").lower()
        # Pre-2023 the full IM World Championship was held in Kona under the
        # plain "Ironman Hawaii" name (no "World Championship" suffix). Treat
        # that title as worlds regardless of year — modern Konas under the
        # same name continue to be a world-championship round.
        is_worlds = (
            "world championship" in title_lower
            or "ironman hawaii" in title_lower
        )

        tiers = []
        if brand == "ironman":
            if distance == "long"   and is_worlds:   tiers = ["im_world_champs", "im"]
            elif distance == "middle" and is_worlds: tiers = ["im_703_world_champs", "im_703"]
            elif distance == "long":   tiers = ["im"]
            elif distance == "middle": tiers = ["im_703"]
            # An Ironman-branded T100 shouldn't happen but if the data is weird, skip it.
        elif brand == "t100":
            tiers = ["t100"]
        elif brand == "challenge":
            tiers = ["challenge"]
        # else: independent event, not palmares-worthy

        # World-championship rounds are still Ironmans / 70.3s, so they also
        # contribute to the generic tier counter (a Kona win counts toward
        # "IM Wins" as well as showing up under "Kona Win").
        for tier in tiers:
            notable.append({
                "tier":        tier,
                "position":    position,
                "race_id":     race_id,
                "race_handle": race_handle,
                "race_date":   race_date,
            })

    return notable


@lru_cache(maxsize=8192)
def get_athlete_stats(athlete_id, category=None, course='short'):
    """race_starts, podiums (pos 1-3), wins (pos 1), last_race_date. Course-scoped.
    Pass category='elite' or 'ag' to restrict to that category."""
    conn = _get_conn()
    course_in = _course_in(course)
    cat_filter = "AND r.category = ?" if category else ""
    params = [athlete_id, category] if category else [athlete_id]
    row = conn.execute(f"""
        SELECT
            COUNT(*)                                             AS race_starts,
            COUNT(CASE WHEN res.position <= 3 THEN 1 END)       AS podiums,
            COUNT(CASE WHEN res.position = 1  THEN 1 END)       AS wins,
            MAX(r.race_date)                                     AS last_race_date
        FROM results res
        JOIN races r ON res.race_id = r.race_id
        WHERE res.athlete_id = ?
          AND r.distance IN {course_in}
          {cat_filter}
    """, params).fetchone()
    return {"race_starts": row[0], "podiums": row[1], "wins": row[2], "last_race_date": row[3]}


def get_athlete_race_history(athlete_id, category='elite', course='short'):
    """
    All race results for an athlete with splits and behind-leader times. Course-scoped.
    Splits and behind times are computed on *corrected* values (manual then auto
    from the corrections table fall back to the raw results), mirroring the
    behaviour of get_race_results / ratings.py. Without this the athlete page
    showed raw times that disagreed with the race page for races that had
    manual fixes applied.
    Returns list of dicts ordered by race_date desc.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    cols = [
        "race_id", "race_title", "race_date", "program", "gender", "position", "status",
        "overall_s", "swim_s", "bike_s", "run_s", "t1_s", "t2_s",
        "overall_behind_s", "swim_behind_s", "bike_behind_s", "run_behind_s",
        "t1_behind_s", "t2_behind_s", "overall_std", "is_ignored", "parent_race_id",
        "is_multi_stage", "event_id",
    ]
    return _dicts(cols, conn.execute(f"""
        WITH corr AS (
            SELECT race_id, athlete_id, discipline,
                   COALESCE(MAX(value) FILTER (WHERE source='manual'),
                            MAX(value) FILTER (WHERE source='auto')) AS value
            FROM corrections
            GROUP BY race_id, athlete_id, discipline
        ),
        corr_wide AS (
            SELECT race_id, athlete_id,
                   MAX(value) FILTER (WHERE discipline='overall') AS overall,
                   MAX(value) FILTER (WHERE discipline='swim')    AS swim,
                   MAX(value) FILTER (WHERE discipline='bike')    AS bike,
                   MAX(value) FILTER (WHERE discipline='run')     AS run,
                   MAX(value) FILTER (WHERE discipline='t1')      AS t1,
                   MAX(value) FILTER (WHERE discipline='t2')      AS t2
            FROM corr GROUP BY race_id, athlete_id
        ),
        corrected AS (
            SELECT
                res.race_id,
                res.athlete_id,
                res.position,
                res.status,
                COALESCE(c.overall, res.overall_s) AS overall_s,
                COALESCE(c.swim,    res.swim_s)    AS swim_s,
                COALESCE(c.bike,    res.bike_s)    AS bike_s,
                COALESCE(c.run,     res.run_s)     AS run_s,
                COALESCE(c.t1,      res.t1_s)      AS t1_s,
                COALESCE(c.t2,      res.t2_s)      AS t2_s
            FROM results res
            LEFT JOIN corr_wide c
              ON res.athlete_id = c.athlete_id AND res.race_id = c.race_id
        )
        SELECT
            res.race_id,
            r.race_title,
            r.race_date,
            r.prog_name,
            r.gender,
            res.position,
            res.status,
            res.overall_s,
            res.swim_s,
            res.bike_s,
            res.run_s,
            res.t1_s,
            res.t2_s,
            -- behind times: subtract per-race winner time computed across ALL athletes (corrected)
            CASE WHEN res.overall_s > 0 THEN res.overall_s - w.min_overall END AS overall_behind_s,
            CASE WHEN res.swim_s    > 0 THEN res.swim_s    - w.min_swim    END AS swim_behind_s,
            CASE WHEN res.bike_s    > 0 THEN res.bike_s    - w.min_bike    END AS bike_behind_s,
            CASE WHEN res.run_s     > 0 THEN res.run_s     - w.min_run     END AS run_behind_s,
            CASE WHEN res.t1_s      > 0 THEN res.t1_s      - w.min_t1      END AS t1_behind_s,
            CASE WHEN res.t2_s      > 0 THEN res.t2_s      - w.min_t2      END AS t2_behind_s,
            std.overall_std,
            ig.race_id IS NOT NULL AS is_ignored,
            ig.parent_race_id,
            r.is_multi_stage,
            r.event_id
        FROM corrected res
        JOIN races r ON res.race_id = r.race_id
        JOIN (
            SELECT race_id,
                   MIN(CASE WHEN overall_s > 0 THEN overall_s END) AS min_overall,
                   MIN(CASE WHEN swim_s    > 0 THEN swim_s    END) AS min_swim,
                   MIN(CASE WHEN bike_s    > 0 THEN bike_s    END) AS min_bike,
                   MIN(CASE WHEN run_s     > 0 THEN run_s     END) AS min_run,
                   MIN(CASE WHEN t1_s      > 0 THEN t1_s      END) AS min_t1,
                   MIN(CASE WHEN t2_s      > 0 THEN t2_s      END) AS min_t2
            FROM corrected
            GROUP BY race_id
        ) w ON res.race_id = w.race_id
        -- Pre-computed top-K standard per race; just a JOIN to race_rankings.
        LEFT JOIN race_rankings std ON std.race_id = res.race_id
        LEFT JOIN ignored_races ig ON ig.race_id = res.race_id
        WHERE res.athlete_id = ? AND r.category = ? AND r.distance IN {course_in}
        ORDER BY r.race_date DESC, res.race_id DESC
    """, [athlete_id, category]))


def get_athlete_relay_history(athlete_id):
    """The athlete's mixed relay legs, shaped like get_athlete_race_history rows.

    overall_* is the leg total; behind-times are vs the fastest same-numbered
    leg in that race (legs differ in length by position, so cross-leg gaps
    would mislead). Extra keys: leg_num and the team's finish position.
    """
    conn = _get_conn()
    cols = [
        "race_id", "race_title", "race_date", "program", "gender", "position", "status",
        "overall_s", "swim_s", "bike_s", "run_s", "t1_s", "t2_s",
        "overall_behind_s", "swim_behind_s", "bike_behind_s", "run_behind_s",
        "t1_behind_s", "t2_behind_s", "overall_std", "is_ignored", "parent_race_id",
        "is_multi_stage", "event_id", "leg_num", "team_title",
    ]
    return _dicts(cols, conn.execute("""
        WITH leg_leader AS (
            SELECT race_id, leg_num,
                   MIN(CASE WHEN leg_s  > 0 THEN leg_s  END) AS min_leg,
                   MIN(CASE WHEN swim_s > 0 THEN swim_s END) AS min_swim,
                   MIN(CASE WHEN bike_s > 0 THEN bike_s END) AS min_bike,
                   MIN(CASE WHEN run_s  > 0 THEN run_s  END) AS min_run,
                   MIN(CASE WHEN t1_s   > 0 THEN t1_s   END) AS min_t1,
                   MIN(CASE WHEN t2_s   > 0 THEN t2_s   END) AS min_t2
            FROM relay_legs
            GROUP BY race_id, leg_num
        )
        SELECT
            l.race_id, r.race_title, r.race_date, r.prog_name, r.gender,
            rt.position, rt.status,
            l.leg_s, l.swim_s, l.bike_s, l.run_s, l.t1_s, l.t2_s,
            CASE WHEN l.leg_s  > 0 THEN l.leg_s  - w.min_leg  END,
            CASE WHEN l.swim_s > 0 THEN l.swim_s - w.min_swim END,
            CASE WHEN l.bike_s > 0 THEN l.bike_s - w.min_bike END,
            CASE WHEN l.run_s  > 0 THEN l.run_s  - w.min_run  END,
            CASE WHEN l.t1_s   > 0 THEN l.t1_s   - w.min_t1   END,
            CASE WHEN l.t2_s   > 0 THEN l.t2_s   - w.min_t2   END,
            NULL AS overall_std,
            ig.race_id IS NOT NULL AS is_ignored,
            NULL AS parent_race_id,
            FALSE AS is_multi_stage,
            r.event_id,
            l.leg_num,
            rt.team_title
        FROM relay_legs l
        JOIN relay_teams rt ON rt.race_id = l.race_id AND rt.team_id = l.team_id
        JOIN races r ON r.race_id = l.race_id
        JOIN leg_leader w ON w.race_id = l.race_id AND w.leg_num = l.leg_num
        LEFT JOIN ignored_races ig ON ig.race_id = l.race_id
        WHERE l.athlete_id = ?
        ORDER BY r.race_date DESC, l.race_id DESC
    """, [athlete_id]))


def get_athlete_rating_history(athlete_id, category='elite', course='short'):
    """
    All rating entries for an athlete with race info and finish position.
    Returns list of dicts ordered by race_date desc. Course-scoped.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    cols = [
        "race_id", "race_date", "race_title", "race_program", "position", "status",
        "overall_rating",    "overall_change",
        "swim_rating",       "swim_change",
        "bike_rating",       "bike_change",
        "run_rating",        "run_change",
        "transition_rating", "transition_change",
        "leg_num", "is_relay",
    ]
    return _dicts(cols, conn.execute(f"""
        SELECT
            ra.race_id,
            r.race_date,
            r.race_title,
            r.prog_name,
            COALESCE(res.position, rl.position) AS position,
            COALESCE(res.status,   rl.status)   AS status,
            ra.overall,    ra.overall_change,
            ra.swim,       ra.swim_change,
            ra.bike,       ra.bike_change,
            ra.run,        ra.run_change,
            ra.transition, ra.transition_change,
            rl.leg_num,
            r.distance = 'relay' AS is_relay
        FROM ratings ra
        JOIN races r   ON ra.race_id   = r.race_id
        LEFT JOIN results res ON ra.race_id = res.race_id AND ra.athlete_id = res.athlete_id
        -- Relay rating rows have no results row; position/status/leg come from
        -- the athlete's team result instead.
        LEFT JOIN (
            SELECT l.race_id, l.athlete_id, l.leg_num, rt.position, rt.status
            FROM relay_legs l
            JOIN relay_teams rt ON rt.race_id = l.race_id AND rt.team_id = l.team_id
        ) rl ON rl.race_id = ra.race_id AND rl.athlete_id = ra.athlete_id
        WHERE ra.athlete_id = ? AND ra.category = ? AND r.distance IN {course_in}
          AND (res.athlete_id IS NOT NULL OR r.distance = 'relay')
        ORDER BY r.race_date DESC, ra.race_id DESC
    """, [athlete_id, category]))


def get_athlete_times_data(athlete_id):
    """
    Corrected times + pct-behind-leader per race, for chart rendering.
    pct_behind = (time - fastest) / fastest, or None if time is 0.
    Splits and per-race mins both use auto-corrected values.
    Returns list of dicts ordered by race_date asc (chronological for charts).
    """
    conn = _get_conn()
    cols = [
        "race_id", "race_date", "race_title",
        "overall_s", "swim_s", "bike_s", "run_s",
        "overall_pct_behind", "swim_pct_behind", "bike_pct_behind", "run_pct_behind",
    ]
    return _dicts(cols, conn.execute("""
        WITH corr AS (
            SELECT race_id, athlete_id, discipline,
                   MAX(value) FILTER (WHERE source='auto') AS value
            FROM corrections
            GROUP BY race_id, athlete_id, discipline
        ),
        corr_wide AS (
            SELECT race_id, athlete_id,
                   MAX(value) FILTER (WHERE discipline='overall') AS overall,
                   MAX(value) FILTER (WHERE discipline='swim')    AS swim,
                   MAX(value) FILTER (WHERE discipline='bike')    AS bike,
                   MAX(value) FILTER (WHERE discipline='run')     AS run
            FROM corr GROUP BY race_id, athlete_id
        ),
        corrected AS (
            SELECT res.race_id, res.athlete_id,
                   COALESCE(cw.overall, res.overall_s) AS overall_s,
                   COALESCE(cw.swim,    res.swim_s)    AS swim_s,
                   COALESCE(cw.bike,    res.bike_s)    AS bike_s,
                   COALESCE(cw.run,     res.run_s)     AS run_s
            FROM results res
            LEFT JOIN corr_wide cw
              ON cw.race_id = res.race_id AND cw.athlete_id = res.athlete_id
        )
        SELECT
            res.race_id,
            r.race_date,
            r.race_title,
            res.overall_s,
            res.swim_s,
            res.bike_s,
            res.run_s,
            -- pct behind leader - computed against all athletes in each race
            CASE WHEN res.overall_s > 0 THEN (res.overall_s - w.min_overall) / w.min_overall END AS overall_pct_behind,
            CASE WHEN res.swim_s    > 0 THEN (res.swim_s    - w.min_swim)    / w.min_swim    END AS swim_pct_behind,
            CASE WHEN res.bike_s    > 0 THEN (res.bike_s    - w.min_bike)    / w.min_bike    END AS bike_pct_behind,
            CASE WHEN res.run_s     > 0 THEN (res.run_s     - w.min_run)     / w.min_run     END AS run_pct_behind
        FROM corrected res
        JOIN races r ON res.race_id = r.race_id
        JOIN (
            SELECT race_id,
                   MIN(CASE WHEN overall_s > 0 THEN overall_s END) AS min_overall,
                   MIN(CASE WHEN swim_s    > 0 THEN swim_s    END) AS min_swim,
                   MIN(CASE WHEN bike_s    > 0 THEN bike_s    END) AS min_bike,
                   MIN(CASE WHEN run_s     > 0 THEN run_s     END) AS min_run
            FROM corrected
            GROUP BY race_id
        ) w ON res.race_id = w.race_id
        WHERE res.athlete_id = ?
        ORDER BY r.race_date ASC, res.race_id ASC
    """, [athlete_id]))


def get_athlete_ratings_data(athlete_id, category='elite', course='short'):
    """
    Raw ratings per race for chart rendering (chronological order). Course-scoped.
    Includes discipline times, diffs from race leader, and world rankings.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    cols = [
        "race_id", "race_date", "race_title",
        "overall_rating", "swim_rating", "bike_rating", "run_rating", "transition_rating",
        "overall_change", "swim_change", "bike_change", "run_change", "transition_change",
        "overall_s", "swim_s", "bike_s", "run_s", "t1_s", "t2_s",
        "overall_diff", "swim_diff", "bike_diff", "run_diff", "t1_diff", "t2_diff",
        "world_overall", "world_swim", "world_bike", "world_run", "world_transition",
        "status",
    ]
    return _dicts(cols, conn.execute(f"""
        WITH leader AS (
            SELECT race_id,
                MIN(CASE WHEN overall_s > 0 THEN overall_s END) AS overall_s,
                MIN(CASE WHEN swim_s    > 0 THEN swim_s    END) AS swim_s,
                MIN(CASE WHEN bike_s    > 0 THEN bike_s    END) AS bike_s,
                MIN(CASE WHEN run_s     > 0 THEN run_s     END) AS run_s,
                MIN(CASE WHEN t1_s      > 0 THEN t1_s      END) AS t1_s,
                MIN(CASE WHEN t2_s      > 0 THEN t2_s      END) AS t2_s
            FROM results
            GROUP BY race_id
        )
        SELECT
            ra.race_id,
            r.race_date,
            r.race_title,
            ra.overall,    ra.swim,    ra.bike,    ra.run,    ra.transition,
            ra.overall_change, ra.swim_change, ra.bike_change, ra.run_change, ra.transition_change,
            res.overall_s, res.swim_s, res.bike_s, res.run_s, res.t1_s, res.t2_s,
            res.overall_s - l.overall_s AS overall_diff,
            res.swim_s    - l.swim_s    AS swim_diff,
            res.bike_s    - l.bike_s    AS bike_diff,
            res.run_s     - l.run_s     AS run_diff,
            res.t1_s      - l.t1_s      AS t1_diff,
            res.t2_s      - l.t2_s      AS t2_diff,
            rk.active_world_overall, rk.active_world_swim, rk.active_world_bike, rk.active_world_run, rk.active_world_transition,
            res.status
        FROM ratings ra
        JOIN races r    ON ra.race_id  = r.race_id
        LEFT JOIN results  res ON res.race_id = ra.race_id AND res.athlete_id = ra.athlete_id
        LEFT JOIN leader   l   ON l.race_id   = ra.race_id
        LEFT JOIN rankings rk  ON rk.race_id  = ra.race_id AND rk.athlete_id = ra.athlete_id
                                AND rk.category = ra.category
        WHERE ra.athlete_id = ? AND ra.category = ? AND r.distance IN {course_in}
        ORDER BY r.race_date ASC, ra.race_id ASC
    """, [athlete_id, category]))


# ---------------------------------------------------------------------------
# Event page
# ---------------------------------------------------------------------------

def get_event_info(event_id):
    """Basic event info."""
    conn = _get_conn()
    row = conn.execute("""
        SELECT event_id, name, venue, country, start_date, end_date
        FROM events WHERE event_id = ?
    """, [event_id]).fetchone()
    if not row:
        return None
    cols = ["event_id", "name", "venue", "country", "start_date", "end_date"]
    return dict(zip(cols, row))


def get_races_by_event(event_id):
    """All races for an event, ordered by race date then female-first within date."""
    conn = _get_conn()
    cols = ["race_id", "race_title", "prog_name", "race_date", "gender", "is_multi_stage", "distance"]
    return _dicts(cols, conn.execute("""
        SELECT race_id, race_title, prog_name, race_date, gender, is_multi_stage, distance
        FROM races
        WHERE event_id = ?
        ORDER BY race_date ASC,
                 CASE WHEN gender = 'male' THEN 0 ELSE 1 END,
                 race_id ASC
    """, [event_id]))


def get_event_races_detail(event_id):
    """All races for an event with podium (top 3 + time gaps) and overall race standard."""
    conn = _get_conn()
    race_rows = conn.execute("""
        SELECT race_id, race_title, prog_name, race_date, gender, distance
        FROM races
        WHERE event_id = ?
        ORDER BY race_date ASC,
                 CASE WHEN gender = 'male' THEN 0 ELSE 1 END,
                 race_id ASC
    """, [event_id]).fetchall()
    if not race_rows:
        return []

    races = [{"race_id": r[0], "race_title": r[1], "prog_name": r[2],
              "race_date": r[3], "gender": r[4], "distance": r[5],
              "podium": [], "standard": None}
             for r in race_rows]
    race_ids = [r["race_id"] for r in races]
    ph = ",".join("?" * len(race_ids))

    # Include split-time columns so the event page can render a wide
    # podium-with-splits table (matching the series-page widget). Raw
    # seconds are returned alongside formatted strings so the template +
    # any JS can compute leg gaps without re-parsing.
    podium_rows = conn.execute(f"""
        SELECT res.race_id, res.position, a.athlete_id, a.name, n.alpha3,
               res.overall_s, res.swim_s, res.t1_s, res.bike_s, res.t2_s, res.run_s,
               a.profile_img
        FROM results res
        JOIN athletes a ON res.athlete_id = a.athlete_id
        JOIN nationalities n ON a.country_full = n.country_full
        WHERE res.race_id IN ({ph})
          AND res.position IN (1, 2, 3)
          AND res.status = 'Finished'
        ORDER BY res.race_id, res.position
    """, race_ids).fetchall()

    podium_by_race = {}
    for (race_id, pos, athlete_id, name, alpha3,
         overall_s, swim_s, t1_s, bike_s, t2_s, run_s, profile_img) in podium_rows:
        podium_by_race.setdefault(race_id, []).append({
            "position":   pos,
            "athlete_id": athlete_id,
            "name":       name,
            "country_alpha3": alpha3,
            "overall_s":  overall_s,
            "swim_s":     swim_s,
            "t1_s":       t1_s,
            "bike_s":     bike_s,
            "t2_s":       t2_s,
            "run_s":      run_s,
            "profile_img": profile_img,
        })

    # Relay podiums come from relay_teams (results has no rows for relay
    # races). Splits shown are the team's four leg splits summed; zero when
    # any leg is missing the split. athlete_id stays None - the template
    # links relay podium entries to the country page instead.
    relay_ids = [r["race_id"] for r in races if r["distance"] == "relay"]
    # Per-leg leg totals for relay podium teams, plus the field-fastest leg
    # total per (race, leg_num), so the event page can render Leg 1-4 columns
    # with gap-to-fastest annotations (mirroring the individual splits table).
    relay_legs_by_team = {}      # (race_id, team_id) -> {leg_num: leg_s}
    relay_ff_leg = {}            # race_id -> {leg_num: fastest_leg_s}
    if relay_ids:
        rph = ",".join("?" * len(relay_ids))
        relay_rows = conn.execute(f"""
            SELECT rt.race_id, rt.team_id, rt.position, rt.country_full, rt.team_num,
                   n.alpha3, rt.total_s
            FROM relay_teams rt
            JOIN nationalities n ON n.country_full = rt.country_full
            WHERE rt.race_id IN ({rph})
              AND rt.position IN (1, 2, 3) AND rt.status = 'Finished'
            ORDER BY rt.race_id, rt.position
        """, relay_ids).fetchall()
        for (race_id, team_id, pos, country_full, team_num, alpha3, total_s) in relay_rows:
            podium_by_race.setdefault(race_id, []).append({
                "position":   pos,
                "athlete_id": None,
                "is_relay":   True,
                "team_id":    team_id,
                "name":       relay_team_name(country_full, team_num),
                "country_alpha3": alpha3,
                "overall_s":  total_s,
                "profile_img": "",
            })

        leg_rows = conn.execute(f"""
            SELECT race_id, team_id, leg_num, leg_s
            FROM relay_legs
            WHERE race_id IN ({rph})
        """, relay_ids).fetchall()
        for race_id, team_id, leg_num, leg_s in leg_rows:
            relay_legs_by_team.setdefault((race_id, team_id), {})[leg_num] = leg_s
            if leg_s and leg_s > 0:
                ff = relay_ff_leg.setdefault(race_id, {})
                if leg_num not in ff or leg_s < ff[leg_num]:
                    ff[leg_num] = leg_s

    # Field-fastest per leg (whoever in the field had the quickest split,
    # not just the podium). Drives the "fastest" tag + gap-to-fastest
    # annotations on the wide podium table.
    ff_rows = conn.execute(f"""
        SELECT race_id,
               MIN(NULLIF(swim_s, 0)) AS swim,
               MIN(NULLIF(t1_s,   0)) AS t1,
               MIN(NULLIF(bike_s, 0)) AS bike,
               MIN(NULLIF(t2_s,   0)) AS t2,
               MIN(NULLIF(run_s,  0)) AS run
        FROM results
        WHERE race_id IN ({ph}) AND status = 'Finished'
        GROUP BY race_id
    """, race_ids).fetchall()
    field_fastest = {
        r[0]: {"swim": r[1], "t1": r[2], "bike": r[3], "t2": r[4], "run": r[5]}
        for r in ff_rows
    }

    std_rows = conn.execute(f"""
        SELECT race_id, overall_std, swim_std, bike_std, run_std, transition_std
        FROM race_rankings
        WHERE race_id IN ({ph})
    """, race_ids).fetchall()
    std_by_race = {
        r[0]: {"overall": r[1], "swim": r[2], "bike": r[3], "run": r[4], "transition": r[5]}
        for r in std_rows
    }

    for race in races:
        rid = race["race_id"]
        raw = podium_by_race.get(rid, [])
        winner_s = raw[0]["overall_s"] if raw else None
        ff = field_fastest.get(rid, {})

        def _leg(p, val_key, leg_key):
            """Return {fmt, fastest, gap} for one leg of one podium athlete."""
            v = p[val_key]
            if not v or v <= 0:
                return {"fmt": None, "fastest": False, "gap": None}
            best = ff.get(leg_key)
            if best and v == best:
                return {"fmt": _fmt_time(v), "fastest": True, "gap": None}
            gap = (v - best) if best else None
            return {
                "fmt":     _fmt_time(v),
                "fastest": False,
                "gap":     f"+{_fmt_time(gap)}" if gap else None,
            }

        def _relay_legs(p):
            """Leg 1-4 cells for a relay team: {fmt, fastest, gap} each, using
            the field-fastest leg total per leg number as the reference."""
            team_legs = relay_legs_by_team.get((rid, p["team_id"]), {})
            ff = relay_ff_leg.get(rid, {})
            cells = []
            for leg_num in (1, 2, 3, 4):
                v = team_legs.get(leg_num)
                if not v or v <= 0:
                    cells.append({"leg_num": leg_num, "fmt": None, "fastest": False, "gap": None})
                    continue
                best = ff.get(leg_num)
                if best and v == best:
                    cells.append({"leg_num": leg_num, "fmt": _fmt_time(v), "fastest": True, "gap": None})
                else:
                    gap = (v - best) if best else None
                    cells.append({
                        "leg_num": leg_num,
                        "fmt":     _fmt_time(v),
                        "fastest": False,
                        "gap":     f"+{_fmt_time(gap)}" if gap else None,
                    })
            return cells

        podium = []
        for p in raw:
            entry = {
                **p,
                "time": _fmt_time(p["overall_s"]),
                "gap":  (f"+{_fmt_time(p['overall_s'] - winner_s)}"
                         if p["position"] != 1 and p["overall_s"] and winner_s else None),
            }
            if p.get("is_relay"):
                entry["legs"] = _relay_legs(p)
            else:
                entry["swim"] = _leg(p, "swim_s", "swim")
                entry["t1"]   = _leg(p, "t1_s",   "t1")
                entry["bike"] = _leg(p, "bike_s", "bike")
                entry["t2"]   = _leg(p, "t2_s",   "t2")
                entry["run"]  = _leg(p, "run_s",  "run")
            podium.append(entry)
        race["podium"] = podium
        race["standards_raw"] = std_by_race.get(rid)

    return races


# ---------------------------------------------------------------------------
# Race page
# ---------------------------------------------------------------------------

def get_race_category(race_id) -> str | None:
    """Return the category ('elite' or 'ag') for a race, or None if not found."""
    row = _get_conn().execute(
        "SELECT category FROM races WHERE race_id = ?", [race_id]
    ).fetchone()
    return row[0] if row else None


def get_race_info(race_id):
    """Basic race info."""
    conn = _get_conn()
    row = conn.execute("""
        SELECT r.race_id, r.race_title, r.prog_name, r.race_date,
               e.venue AS location, e.country, r.gender, r.sub_category,
               r.race_handle, r.event_id, r.is_multi_stage, r.distance
        FROM races r
        JOIN events e ON r.event_id = e.event_id
        WHERE r.race_id = ?
    """, [race_id]).fetchone()
    if not row:
        return None
    cols = ["race_id", "race_title", "prog_name", "race_date",
            "location", "country", "gender", "sub_category",
            "race_handle", "event_id", "is_multi_stage", "distance"]
    return dict(zip(cols, row))


_ROMAN_SUFFIX = {2: 'II', 3: 'III', 4: 'IV', 5: 'V', 6: 'VI', 7: 'VII', 8: 'VIII'}


def relay_team_name(country_full, team_num):
    """Display name for a relay team: just the country, with a roman-numeral
    suffix only for second/third teams (e.g. "Australia", "Australia II")."""
    if team_num and team_num > 1:
        return f"{country_full} {_ROMAN_SUFFIX.get(team_num, team_num)}"
    return country_full


def get_relay_teams(race_id):
    """All teams of a mixed relay race with their legs nested.

    Returns a list of team dicts ordered by position (non-finishers last),
    each with a 'legs' list of 4 member dicts in leg order (possibly empty
    for teams with no member-level data).
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT rt.team_id, rt.team_title, rt.team_num, rt.country_full, n.alpha3,
               rt.position, rt.status, rt.start_num, rt.total_s,
               l.leg_num, l.athlete_id, a.name, a.gender, a.profile_img,
               l.leg_s, l.swim_s, l.t1_s, l.bike_s, l.t2_s, l.run_s
        FROM relay_teams rt
        JOIN nationalities n ON rt.country_full = n.country_full
        LEFT JOIN relay_legs l ON l.race_id = rt.race_id AND l.team_id = rt.team_id
        LEFT JOIN athletes a ON a.athlete_id = l.athlete_id
        WHERE rt.race_id = ?
        ORDER BY CASE rt.status
                     WHEN 'Finished' THEN 0 WHEN 'NC' THEN 1 WHEN 'LAP' THEN 2
                     WHEN 'DNF' THEN 3 WHEN 'DQ' THEN 4 WHEN 'DNS' THEN 5 ELSE 6
                 END,
                 rt.position NULLS LAST, rt.total_s, rt.team_id, l.leg_num
    """, [race_id]).fetchall()

    teams = {}
    order = []
    for (team_id, team_title, team_num, country_full, alpha3, position, status,
         start_num, total_s, leg_num, athlete_id, name, gender, profile_img,
         leg_s, swim_s, t1_s, bike_s, t2_s, run_s) in rows:
        if team_id not in teams:
            teams[team_id] = {
                "team_id": team_id, "team_title": team_title, "team_num": team_num,
                "team_name": relay_team_name(country_full, team_num),
                "country_full": country_full, "country_alpha3": alpha3,
                "position": position, "status": status, "start_num": start_num,
                "total_s": total_s, "legs": [],
            }
            order.append(team_id)
        if leg_num is not None:
            teams[team_id]["legs"].append({
                "leg_num": leg_num, "athlete_id": athlete_id, "name": name,
                "gender": gender, "profile_img": profile_img,
                "leg_s": leg_s, "swim_s": swim_s, "t1_s": t1_s,
                "bike_s": bike_s, "t2_s": t2_s, "run_s": run_s,
            })
    return [teams[tid] for tid in order]


def get_relay_country_ratings(race_id):
    """Country rating rows (post-race values + changes) for a relay race."""
    conn = _get_conn()
    cols = ["country_full", "country_alpha3",
            "overall_rating", "swim_rating", "bike_rating", "run_rating", "transition_rating",
            "overall_change", "swim_change", "bike_change", "run_change", "transition_change"]
    return _dicts(cols, conn.execute("""
        SELECT cr.country_full, n.alpha3,
               cr.overall, cr.swim, cr.bike, cr.run, cr.transition,
               cr.overall_change, cr.swim_change, cr.bike_change, cr.run_change, cr.transition_change
        FROM country_ratings cr
        JOIN nationalities n ON cr.country_full = n.country_full
        WHERE cr.race_id = ?
        ORDER BY cr.overall DESC
    """, [race_id]))


def get_race_results(race_id):
    """
    All results for a race with athlete info and behind-leader times.
    Corrections are applied inline — corrected splits are used for all times and
    behind-leader calculations, matching the behaviour in ratings.py.
    Returns list of dicts ordered by position (nulls last for DNFs).
    """
    conn = _get_conn()
    cols = [
        "athlete_id", "position", "status",
        "overall_s", "swim_s", "bike_s", "run_s", "t1_s", "t2_s",
        "name", "year_of_birth", "profile_img", "country_alpha3",
        "overall_behind_s", "swim_behind_s", "bike_behind_s", "run_behind_s",
        "t1_behind_s", "t2_behind_s",
    ]
    return _dicts(cols, conn.execute("""
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
        ),
        corrected AS (
            SELECT
                res.athlete_id,
                res.position,
                res.status,
                COALESCE(c.overall, res.overall_s) AS overall_s,
                COALESCE(c.swim,    res.swim_s)    AS swim_s,
                COALESCE(c.bike,    res.bike_s)    AS bike_s,
                COALESCE(c.run,     res.run_s)     AS run_s,
                COALESCE(c.t1,      res.t1_s)      AS t1_s,
                COALESCE(c.t2,      res.t2_s)      AS t2_s
            FROM results res
            LEFT JOIN corr_wide c ON res.athlete_id = c.athlete_id
            WHERE res.race_id = ?
        )
        SELECT
            cr.athlete_id,
            cr.position,
            cr.status,
            cr.overall_s, cr.swim_s, cr.bike_s, cr.run_s, cr.t1_s, cr.t2_s,
            a.name, a.year_of_birth, a.profile_img,
            n.alpha3 AS country_alpha3,
            -- behind times computed on corrected splits
            CASE WHEN cr.overall_s > 0 THEN
                cr.overall_s - MIN(CASE WHEN cr.overall_s > 0 THEN cr.overall_s END) OVER ()
            END AS overall_behind_s,
            CASE WHEN cr.swim_s > 0 THEN
                cr.swim_s - MIN(CASE WHEN cr.swim_s > 0 THEN cr.swim_s END) OVER ()
            END AS swim_behind_s,
            CASE WHEN cr.bike_s > 0 THEN
                cr.bike_s - MIN(CASE WHEN cr.bike_s > 0 THEN cr.bike_s END) OVER ()
            END AS bike_behind_s,
            CASE WHEN cr.run_s > 0 THEN
                cr.run_s - MIN(CASE WHEN cr.run_s > 0 THEN cr.run_s END) OVER ()
            END AS run_behind_s,
            CASE WHEN cr.t1_s > 0 THEN
                cr.t1_s - MIN(CASE WHEN cr.t1_s > 0 THEN cr.t1_s END) OVER ()
            END AS t1_behind_s,
            CASE WHEN cr.t2_s > 0 THEN
                cr.t2_s - MIN(CASE WHEN cr.t2_s > 0 THEN cr.t2_s END) OVER ()
            END AS t2_behind_s
        FROM corrected cr
        JOIN athletes a      ON cr.athlete_id = a.athlete_id
        JOIN nationalities n ON a.country_full = n.country_full
        ORDER BY
            CASE cr.status
                WHEN 'Finished' THEN 0
                WHEN 'NC'       THEN 1
                WHEN 'LAP'      THEN 2
                WHEN 'DNF'      THEN 3
                WHEN 'DQ'       THEN 4
                WHEN 'DNS'      THEN 5
                ELSE 6
            END,
            cr.position NULLS LAST
    """, [race_id, race_id]))


def get_race_corrections(race_id):
    """
    Returns correction records for a race, paired with their original result values.
    Only returns athletes that actually have a correction for this race. Manual
    rows win over auto in the reported value (mirrors ratings.py precedence).
    """
    conn = _get_conn()
    cols = [
        "athlete_id", "name", "country_alpha3", "position", "status", "notes",
        "orig_overall", "corr_overall",
        "orig_swim",    "corr_swim",
        "orig_t1",      "corr_t1",
        "orig_bike",    "corr_bike",
        "orig_t2",      "corr_t2",
        "orig_run",     "corr_run",
    ]
    return _dicts(cols, conn.execute("""
        WITH corr AS (
            SELECT race_id, athlete_id, discipline,
                   COALESCE(MAX(value) FILTER (WHERE source='manual'),
                            MAX(value) FILTER (WHERE source='auto')) AS value,
                   STRING_AGG(DISTINCT source, ',')  AS sources,
                   STRING_AGG(DISTINCT NULLIF(reason, ''), '; ') AS reasons
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
                   MAX(value) FILTER (WHERE discipline='t2')      AS t2,
                   STRING_AGG(DISTINCT NULLIF(reasons, ''), '. ') AS notes
            FROM corr GROUP BY athlete_id
        )
        SELECT
            cw.athlete_id,
            a.name,
            n.alpha3 AS country_alpha3,
            res.position,
            res.status,
            COALESCE(cw.notes, '') AS notes,
            res.overall_s, cw.overall,
            res.swim_s,    cw.swim,
            res.t1_s,      cw.t1,
            res.bike_s,    cw.bike,
            res.t2_s,      cw.t2,
            res.run_s,     cw.run
        FROM corr_wide cw
        JOIN results res ON res.race_id = ? AND res.athlete_id = cw.athlete_id
        JOIN athletes a  ON cw.athlete_id = a.athlete_id
        JOIN nationalities n ON a.country_full = n.country_full
        ORDER BY res.position NULLS LAST
    """, [race_id, race_id]))


def get_race_ratings(race_id):
    """
    All ratings for a race with athlete info.
    Returns list of dicts in the same order as get_race_results().
    """
    conn = _get_conn()
    cols = [
        "athlete_id", "name", "country_alpha3", "year_of_birth", "position", "status",
        "overall_rating",    "overall_change",
        "swim_rating",       "swim_change",
        "bike_rating",       "bike_change",
        "run_rating",        "run_change",
        "transition_rating", "transition_change",
    ]
    return _dicts(cols, conn.execute("""
        SELECT
            ra.athlete_id,
            a.name,
            n.alpha3  AS country_alpha3,
            a.year_of_birth,
            res.position,
            res.status,
            ra.overall,    ra.overall_change,
            ra.swim,       ra.swim_change,
            ra.bike,       ra.bike_change,
            ra.run,        ra.run_change,
            ra.transition, ra.transition_change
        FROM ratings ra
        JOIN athletes a    ON ra.athlete_id = a.athlete_id
        JOIN nationalities n ON a.country_full = n.country_full
        JOIN results res   ON ra.race_id = res.race_id AND ra.athlete_id = res.athlete_id
        WHERE ra.race_id = ?
        ORDER BY res.position NULLS LAST
    """, [race_id]))


def get_race_ignored_info(race_id):
    """Returns ignore metadata if the race is in ignored_races, else None.

    Dict keys: reason, parent_race_id, parent_race_title, parent_is_multi_stage
    (parent_* are None / False if no parent recorded).
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT reason, parent_race_id FROM ignored_races WHERE race_id = ?", [race_id]
    ).fetchone()
    if not row:
        return None
    reason, parent_race_id = row
    parent_title = None
    parent_is_multi_stage = False
    if parent_race_id:
        p = conn.execute(
            """SELECT p.race_title, p.is_multi_stage, p.event_id, child.event_id
                 FROM races p
                 JOIN races child ON child.race_id = ?
                WHERE p.race_id = ?""",
            [race_id, parent_race_id],
        ).fetchone()
        if p:
            parent_title = p[0]
            # True only when this race is actually a stage of the multi-round
            # parent (same event). Subset bundles from a different event (e.g.
            # national champs pulled into a conti cup) share a parent_race_id
            # but aren't stages, so they stay False.
            parent_is_multi_stage = bool(p[1]) and p[2] == p[3]
    return {
        "reason": reason,
        "parent_race_id": parent_race_id,
        "parent_race_title": parent_title,
        "parent_is_multi_stage": parent_is_multi_stage,
    }


def get_race_standards(race_id):
    """Exponential-decay weighted average pre-race rating per discipline.

    See ptd_data.ratings.standard_denom for the field-size handling. For normal
    races, pre-race = ra.overall - ra.overall_change (rating before this race).
    For ignored races (no ratings rows), falls back to each athlete's most recent
    rating from any prior race, which is their actual pre-race rating.
    """
    conn = _get_conn()

    is_ignored = conn.execute(
        "SELECT 1 FROM ignored_races WHERE race_id = ?", [race_id]
    ).fetchone()

    if is_ignored:
        # No ratings records exist for ignored races. Look up each athlete's most
        # recent rating from any other race in the same course bucket on or
        # before this race's date.
        target = conn.execute("SELECT distance FROM races WHERE race_id = ?", [race_id]).fetchone()
        course = course_for_distance(target[0]) if target else None
        if course is None:
            return {d: 0.0 for d in ["overall", "swim", "bike", "run", "transition"]}
        course_in = _course_in(course)
        row = conn.execute(f"""
            WITH pre_race AS (
                SELECT ra.athlete_id,
                       ra.overall, ra.swim, ra.bike, ra.run, ra.transition,
                       ROW_NUMBER() OVER (
                           PARTITION BY ra.athlete_id
                           ORDER BY r.race_date DESC, ra.race_id DESC
                       ) AS rn
                FROM ratings ra
                JOIN races r ON ra.race_id = r.race_id
                WHERE ra.athlete_id IN (SELECT athlete_id FROM results WHERE race_id = ?)
                  AND r.race_date <= (SELECT race_date FROM races WHERE race_id = ?)
                  AND r.distance IN {course_in}
            ),
            base AS (
                SELECT res.status, res.position AS overall_pos,
                       res.swim_s, res.bike_s, res.run_s,
                       CASE WHEN res.t1_s > 0 AND res.t2_s > 0 THEN res.t1_s + res.t2_s ELSE 0 END AS trans_s,
                       pr.overall, pr.swim, pr.bike, pr.run, pr.transition
                FROM results res
                JOIN pre_race pr ON res.athlete_id = pr.athlete_id AND pr.rn = 1
                WHERE res.race_id = ?
            ),
            ranked AS (
                SELECT *,
                    CASE WHEN swim_s  > 0 THEN ROW_NUMBER() OVER (ORDER BY CASE WHEN swim_s  > 0 THEN swim_s  END NULLS LAST) END AS swim_pos,
                    CASE WHEN bike_s  > 0 THEN ROW_NUMBER() OVER (ORDER BY CASE WHEN bike_s  > 0 THEN bike_s  END NULLS LAST) END AS bike_pos,
                    CASE WHEN run_s   > 0 THEN ROW_NUMBER() OVER (ORDER BY CASE WHEN run_s   > 0 THEN run_s   END NULLS LAST) END AS run_pos,
                    CASE WHEN trans_s > 0 THEN ROW_NUMBER() OVER (ORDER BY CASE WHEN trans_s > 0 THEN trans_s END NULLS LAST) END AS trans_pos
                FROM base
            )
            SELECT
                COUNT(*) FILTER (WHERE status = 'Finished' AND overall_pos IS NOT NULL) AS n_overall,
                COUNT(*) FILTER (WHERE swim_s  > 0) AS n_swim,
                COUNT(*) FILTER (WHERE bike_s  > 0) AS n_bike,
                COUNT(*) FILTER (WHERE run_s   > 0) AS n_run,
                COUNT(*) FILTER (WHERE trans_s > 0) AS n_trans,
                SUM(overall    * CASE WHEN status='Finished' AND overall_pos IS NOT NULL AND overall_pos <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (overall_pos - 1)) ELSE 0 END),
                SUM(swim       * CASE WHEN swim_pos  IS NOT NULL AND swim_pos  <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (swim_pos  - 1)) ELSE 0 END),
                SUM(bike       * CASE WHEN bike_pos  IS NOT NULL AND bike_pos  <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (bike_pos  - 1)) ELSE 0 END),
                SUM(run        * CASE WHEN run_pos   IS NOT NULL AND run_pos   <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (run_pos   - 1)) ELSE 0 END),
                SUM(transition * CASE WHEN trans_pos IS NOT NULL AND trans_pos <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (trans_pos - 1)) ELSE 0 END)
            FROM ranked
        """, [race_id, race_id, race_id]).fetchone()
        if not row or row[0] is None:
            return {d: 0.0 for d in ["overall", "swim", "bike", "run", "transition"]}
        n_overall, n_swim, n_bike, n_run, n_trans, ov, sw, bk, rn_, tr = row
        def _std(num, n):
            return (num or 0.0) / standard_denom(n) if n and n > 0 else 0.0
        return {
            "overall":    _std(ov,  n_overall),
            "swim":       _std(sw,  n_swim),
            "bike":       _std(bk,  n_bike),
            "run":        _std(rn_, n_run),
            "transition": _std(tr,  n_trans),
        }

    # Standards are pre-computed by ratings._compute_race_rankings.
    # Single indexed row lookup; no aggregation at request time.
    row = conn.execute("""
        SELECT overall_std, swim_std, bike_std, run_std, transition_std
        FROM race_rankings
        WHERE race_id = ?
    """, [race_id]).fetchone()

    if not row or row[0] is None:
        return {d: 0.0 for d in ["overall", "swim", "bike", "run", "transition"]}

    return {
        "overall":    row[0],
        "swim":       row[1],
        "bike":       row[2],
        "run":        row[3],
        "transition": row[4],
    }


_DISC_DEFAULT = {d: 0.0 for d in ["overall", "swim", "bike", "run", "transition"]}


def get_race_standards_bulk(race_ids):
    """Bulk get_race_standards. Returns {race_id: {overall, swim, bike, run, transition}}.

    Two queries total regardless of N:
      1. Normal races: indexed lookup against race_rankings.
      2. Ignored races: one aggregation grouped by race_id, with per-race date
         cutoff and course bucket handled inside the SQL.
    """
    if not race_ids:
        return {}

    conn = _get_conn()
    race_ids = list(dict.fromkeys(race_ids))  # de-dupe, preserve order
    placeholders = ",".join("?" * len(race_ids))

    ignored_set = {r[0] for r in conn.execute(
        f"SELECT race_id FROM ignored_races WHERE race_id IN ({placeholders})",
        race_ids,
    ).fetchall()}
    normal_ids = [rid for rid in race_ids if rid not in ignored_set]
    ignored_ids = [rid for rid in race_ids if rid in ignored_set]

    result = {}

    if normal_ids:
        np = ",".join("?" * len(normal_ids))
        rows = conn.execute(f"""
            SELECT race_id, overall_std, swim_std, bike_std, run_std, transition_std
            FROM race_rankings
            WHERE race_id IN ({np})
        """, normal_ids).fetchall()
        for row in rows:
            if row[1] is None:
                result[row[0]] = dict(_DISC_DEFAULT)
            else:
                result[row[0]] = {
                    "overall":    row[1],
                    "swim":       row[2],
                    "bike":       row[3],
                    "run":        row[4],
                    "transition": row[5],
                }

    if ignored_ids:
        ip = ",".join("?" * len(ignored_ids))
        # One pass for all ignored races: pre_race CTE partitions per target
        # race so each athlete's "most recent prior rating" is computed in the
        # target's course bucket and date window. Discipline ranks partition
        # by race_id so positions reset per race.
        rows = conn.execute(f"""
            WITH targets AS (
                SELECT race_id, race_date, distance
                FROM races
                WHERE race_id IN ({ip})
            ),
            target_results AS (
                SELECT res.race_id, res.athlete_id, res.position, res.status,
                       res.swim_s, res.bike_s, res.run_s,
                       CASE WHEN res.t1_s > 0 AND res.t2_s > 0 THEN res.t1_s + res.t2_s ELSE 0 END AS trans_s
                FROM results res
                WHERE res.race_id IN ({ip})
            ),
            pre_race AS (
                SELECT t.race_id AS target_race_id, ra.athlete_id,
                       ra.overall, ra.swim, ra.bike, ra.run, ra.transition,
                       ROW_NUMBER() OVER (
                           PARTITION BY t.race_id, ra.athlete_id
                           ORDER BY r.race_date DESC, ra.race_id DESC
                       ) AS rn
                FROM targets t
                JOIN target_results tr ON tr.race_id = t.race_id
                JOIN ratings ra ON ra.athlete_id = tr.athlete_id
                JOIN races r ON ra.race_id = r.race_id
                WHERE r.race_date <= t.race_date
                  AND (
                      (t.distance IN ('sprint','standard') AND r.distance IN ('sprint','standard'))
                      OR
                      (t.distance IN ('middle','t100','long') AND r.distance IN ('middle','t100','long'))
                  )
            ),
            base AS (
                SELECT tr.race_id, tr.status, tr.position AS overall_pos,
                       tr.swim_s, tr.bike_s, tr.run_s, tr.trans_s,
                       pr.overall, pr.swim, pr.bike, pr.run, pr.transition
                FROM target_results tr
                JOIN pre_race pr
                  ON pr.target_race_id = tr.race_id
                 AND pr.athlete_id = tr.athlete_id
                 AND pr.rn = 1
            ),
            ranked AS (
                SELECT *,
                    CASE WHEN swim_s  > 0 THEN ROW_NUMBER() OVER (PARTITION BY race_id ORDER BY CASE WHEN swim_s  > 0 THEN swim_s  END NULLS LAST) END AS swim_pos,
                    CASE WHEN bike_s  > 0 THEN ROW_NUMBER() OVER (PARTITION BY race_id ORDER BY CASE WHEN bike_s  > 0 THEN bike_s  END NULLS LAST) END AS bike_pos,
                    CASE WHEN run_s   > 0 THEN ROW_NUMBER() OVER (PARTITION BY race_id ORDER BY CASE WHEN run_s   > 0 THEN run_s   END NULLS LAST) END AS run_pos,
                    CASE WHEN trans_s > 0 THEN ROW_NUMBER() OVER (PARTITION BY race_id ORDER BY CASE WHEN trans_s > 0 THEN trans_s END NULLS LAST) END AS trans_pos
                FROM base
            )
            SELECT
                race_id,
                COUNT(*) FILTER (WHERE status = 'Finished' AND overall_pos IS NOT NULL) AS n_overall,
                COUNT(*) FILTER (WHERE swim_s  > 0) AS n_swim,
                COUNT(*) FILTER (WHERE bike_s  > 0) AS n_bike,
                COUNT(*) FILTER (WHERE run_s   > 0) AS n_run,
                COUNT(*) FILTER (WHERE trans_s > 0) AS n_trans,
                SUM(overall    * CASE WHEN status='Finished' AND overall_pos IS NOT NULL AND overall_pos <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (overall_pos - 1)) ELSE 0 END),
                SUM(swim       * CASE WHEN swim_pos  IS NOT NULL AND swim_pos  <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (swim_pos  - 1)) ELSE 0 END),
                SUM(bike       * CASE WHEN bike_pos  IS NOT NULL AND bike_pos  <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (bike_pos  - 1)) ELSE 0 END),
                SUM(run        * CASE WHEN run_pos   IS NOT NULL AND run_pos   <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (run_pos   - 1)) ELSE 0 END),
                SUM(transition * CASE WHEN trans_pos IS NOT NULL AND trans_pos <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (trans_pos - 1)) ELSE 0 END)
            FROM ranked
            GROUP BY race_id
        """, ignored_ids + ignored_ids).fetchall()
        for row in rows:
            rid, n_o, n_s, n_b, n_r, n_t, ov, sw, bk, rn_, tr = row
            def _std(num, n):
                return (num or 0.0) / standard_denom(n) if n and n > 0 else 0.0
            result[rid] = {
                "overall":    _std(ov,  n_o),
                "swim":       _std(sw,  n_s),
                "bike":       _std(bk,  n_b),
                "run":        _std(rn_, n_r),
                "transition": _std(tr,  n_t),
            }

    for rid in race_ids:
        if rid not in result:
            result[rid] = dict(_DISC_DEFAULT)

    return result


# Keyed by (gender, course). Invalidate by restarting the process (pre-race ratings are stable).
_standard_thresholds_cache: dict = {}

def get_race_standard_thresholds(gender, course='short'):
    """Percentile thresholds (p30, p60, p85, p95) per discipline for a given gender/course.

    Used to classify race standards as Beginner/Novice/Intermediate/Advanced/Expert. Cached on
    first call per (gender, course) since the computation is expensive and the data rarely changes.
    """
    cache_key = (gender, course)
    if cache_key in _standard_thresholds_cache:
        return _standard_thresholds_cache[cache_key]

    # Quantiles come straight off the pre-computed race_rankings table.
    # Course there uses the rankings bucket ('short'|'long'|'ag'), which already
    # matches the input here.
    conn = _get_conn()
    row = conn.execute("""
        SELECT
            quantile_cont(overall_std,    [0.30, 0.60, 0.85, 0.95]),
            quantile_cont(swim_std,       [0.30, 0.60, 0.85, 0.95]),
            quantile_cont(bike_std,       [0.30, 0.60, 0.85, 0.95]),
            quantile_cont(run_std,        [0.30, 0.60, 0.85, 0.95]),
            quantile_cont(transition_std, [0.30, 0.60, 0.85, 0.95])
        FROM race_rankings
        WHERE gender = ? AND course = ?
    """, [gender, course]).fetchone()

    result = {}
    for i, disc in enumerate(["overall", "swim", "bike", "run", "transition"]):
        qs = row[i]
        result[disc] = {"p30": qs[0], "p60": qs[1], "p85": qs[2], "p95": qs[3]}

    _standard_thresholds_cache[cache_key] = result
    return result


def get_race_best_performances(race_id):
    """Max positive rating change per discipline + athlete name.

    One pass over the race's rating rows using FILTER aggregates; previously
    this was 5 separate ORDER BY+LIMIT queries per race page render.
    """
    conn = _get_conn()
    discs = ["overall", "swim", "bike", "run", "transition"]
    # Per-discipline top row picked via argmax-style trick: pair (change,
    # athlete_id) so MAX picks the biggest change and ties break by athlete_id.
    # Then look up names in a single follow-up query.
    select_max = ",\n            ".join(
        f"MAX(CASE WHEN ra.{d}_change > 0 THEN ra.{d}_change END) AS {d}_change,\n            "
        f"ARG_MAX(ra.athlete_id, ra.{d}_change) FILTER (WHERE ra.{d}_change > 0) AS {d}_athlete_id"
        for d in discs
    )
    row = conn.execute(f"""
        SELECT {select_max}
        FROM ratings ra
        WHERE ra.race_id = ?
    """, [race_id]).fetchone()

    result = {}
    aids = set()
    for i, d in enumerate(discs):
        change = row[2 * i]
        aid    = row[2 * i + 1]
        result[f"{d}_change"]     = change
        result[f"{d}_athlete_id"] = aid
        if aid is not None:
            aids.add(aid)

    names = {}
    if aids:
        ph = ",".join("?" * len(aids))
        names = dict(conn.execute(
            f"SELECT athlete_id, name FROM athletes WHERE athlete_id IN ({ph})",
            list(aids),
        ).fetchall())

    for d in discs:
        aid = result[f"{d}_athlete_id"]
        result[f"{d}_athlete_name"] = names.get(aid, "") if aid is not None else ""
    return result


def get_race_time_values(race_id):
    """
    Raw time arrays for histogram computation (done in router with numpy).
    Corrections are applied inline, consistent with get_race_results.
    Returns dict of discipline -> list of non-zero seconds values.
    """
    conn = _get_conn()
    rows = conn.execute("""
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
        SELECT
            COALESCE(c.overall, res.overall_s),
            COALESCE(c.swim,    res.swim_s),
            COALESCE(c.bike,    res.bike_s),
            COALESCE(c.run,     res.run_s),
            COALESCE(c.t1,      res.t1_s),
            COALESCE(c.t2,      res.t2_s)
        FROM results res
        LEFT JOIN corr_wide c ON res.athlete_id = c.athlete_id
        WHERE res.race_id = ? AND res.overall_s > 0
    """, [race_id, race_id]).fetchall()

    result = {"overall": [], "swim": [], "bike": [], "run": [], "t1": [], "t2": []}
    for r in rows:
        if r[0] > 0: result["overall"].append(r[0])
        if r[1] > 0: result["swim"].append(r[1])
        if r[2] > 0: result["bike"].append(r[2])
        if r[3] > 0: result["run"].append(r[3])
        if r[4] > 0: result["t1"].append(r[4])
        if r[5] > 0: result["t2"].append(r[5])
    return result


def get_athlete_race_counts(athlete_ids):
    """Returns {athlete_id: total_race_count} for weighting the prediction regression."""
    if not athlete_ids:
        return {}
    pl = ','.join('?' * len(athlete_ids))
    rows = _get_conn().execute(
        f"SELECT athlete_id, COUNT(*) FROM results WHERE athlete_id IN ({pl}) GROUP BY athlete_id",
        athlete_ids,
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def get_race_rating_values(race_id):
    """
    Raw rating arrays for histogram computation (done in router with numpy).
    Returns dict of discipline -> list of rating values.
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT overall, swim, bike, run, transition
        FROM ratings WHERE race_id = ?
    """, [race_id]).fetchall()

    result = {"overall": [], "swim": [], "bike": [], "run": [], "transition": []}
    for r in rows:
        result["overall"].append(r[0])
        result["swim"].append(r[1])
        result["bike"].append(r[2])
        result["run"].append(r[3])
        result["transition"].append(r[4])
    return result


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def get_common_races(athlete1_id, athlete2_id, course='short', category='elite'):
    """
    Races where both athletes competed, scoped to a (course, category) program.
    Returns list of dicts ordered by race_date desc.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    cols = ["race_id", "race_title", "race_date",
            "a1_position", "a1_status", "a1_overall_s",
            "a1_swim_s", "a1_bike_s", "a1_run_s",
            "a2_position", "a2_status", "a2_overall_s",
            "a2_swim_s", "a2_bike_s", "a2_run_s"]
    return _dicts(cols, conn.execute(f"""
        SELECT
            r1.race_id,
            r.race_title,
            r.race_date,
            r1.position    AS a1_position,
            r1.status      AS a1_status,
            r1.overall_s   AS a1_overall_s,
            r1.swim_s      AS a1_swim_s,
            r1.bike_s      AS a1_bike_s,
            r1.run_s       AS a1_run_s,
            r2.position    AS a2_position,
            r2.status      AS a2_status,
            r2.overall_s   AS a2_overall_s,
            r2.swim_s      AS a2_swim_s,
            r2.bike_s      AS a2_bike_s,
            r2.run_s       AS a2_run_s
        FROM results r1
        JOIN results r2 ON r1.race_id = r2.race_id
        JOIN races r    ON r1.race_id = r.race_id
        WHERE r1.athlete_id = ?
          AND r2.athlete_id = ?
          AND r.distance IN {course_in}
          AND r.category = ?
        ORDER BY r.race_date DESC
    """, [athlete1_id, athlete2_id, category]))


def search_races_for_compare(query, course=None, gender=None, category='elite', limit=20):
    """Search individual races by event name / venue / country for the race-compare picker.

    Returns race-level rows (not events) since users pick a specific race (gender,
    program) to compare. Filters by course (short/long) and gender so the second
    pick is naturally restricted to apples-to-apples races.
    """
    conn = _get_conn()
    q = f"%{query}%"
    filters = [
        "(e.name ILIKE ? OR e.venue ILIKE ? OR e.country ILIKE ? OR r.race_title ILIKE ?)",
        "r.category = ?",
        # The compare tooling is built around individual results; relays out.
        "r.distance != 'relay'",
    ]
    params = [q, q, q, q, category]
    if course in ('short', 'long'):
        filters.append(f"r.distance IN {_course_in(course)}")
    if gender in ('male', 'female'):
        filters.append("r.gender = ?")
        params.append(gender)
    where = " AND ".join(filters)
    rows = conn.execute(f"""
        SELECT r.race_id, r.race_title, r.prog_name, r.race_date, r.gender,
               r.distance, e.venue, e.country
        FROM races r
        JOIN events e ON r.event_id = e.event_id
        WHERE {where}
        ORDER BY r.race_date DESC
        LIMIT ?
    """, params + [limit]).fetchall()
    cols = ["race_id", "race_title", "prog_name", "race_date", "gender",
            "distance", "venue", "country"]
    out = []
    for row in rows:
        d = dict(zip(cols, row))
        d["course"] = course_for_distance(d["distance"])
        out.append(d)
    return out


def get_race_compare_summary(race_id):
    """Lightweight race info for the race-compare picker card.

    Returns dict with race + event basics, gender, distance/course, and
    finish/dnf counts. Returns None if the race doesn't exist.
    """
    conn = _get_conn()
    row = conn.execute("""
        SELECT r.race_id, r.race_title, r.prog_name, r.race_date, r.gender,
               r.distance, e.venue, e.country,
               (SELECT COUNT(*) FROM results res WHERE res.race_id = r.race_id) AS athletes,
               (SELECT COUNT(*) FROM results res WHERE res.race_id = r.race_id AND res.status = 'Finished') AS finishers
        FROM races r
        JOIN events e ON r.event_id = e.event_id
        WHERE r.race_id = ?
    """, [race_id]).fetchone()
    if not row:
        return None
    cols = ["race_id", "race_title", "prog_name", "race_date", "gender",
            "distance", "venue", "country", "athletes", "finishers"]
    d = dict(zip(cols, row))
    d["course"] = course_for_distance(d["distance"])
    d["dnfs"] = d["athletes"] - d["finishers"]
    return d


def get_common_athletes_in_races(race1_id, race2_id):
    """Athletes who competed in both races. Returns rows with each athlete's
    position/status/overall time in each race, sorted by best (min) finishing
    position across the two races so the top performers float up.
    """
    conn = _get_conn()
    cols = ["athlete_id", "name", "country_alpha3",
            "r1_position", "r1_status",
            "r1_overall_s", "r1_swim_s", "r1_bike_s", "r1_run_s",
            "r2_position", "r2_status",
            "r2_overall_s", "r2_swim_s", "r2_bike_s", "r2_run_s"]
    return _dicts(cols, conn.execute("""
        SELECT
            a.athlete_id, a.name, n.alpha3 AS country_alpha3,
            r1.position AS r1_position, r1.status AS r1_status,
            r1.overall_s AS r1_overall_s, r1.swim_s AS r1_swim_s,
            r1.bike_s AS r1_bike_s, r1.run_s AS r1_run_s,
            r2.position AS r2_position, r2.status AS r2_status,
            r2.overall_s AS r2_overall_s, r2.swim_s AS r2_swim_s,
            r2.bike_s AS r2_bike_s, r2.run_s AS r2_run_s
        FROM results r1
        JOIN results r2  ON r1.athlete_id = r2.athlete_id
        JOIN athletes a  ON r1.athlete_id = a.athlete_id
        JOIN nationalities n ON a.country_full = n.country_full
        WHERE r1.race_id = ? AND r2.race_id = ?
        ORDER BY LEAST(COALESCE(r1.position, 9999), COALESCE(r2.position, 9999)) ASC,
                 a.name ASC
    """, [race1_id, race2_id]))


def get_athlete_rankings_data(athlete_id, category='elite', course='short'):
    """
    World and national rankings per race for chart rendering (chronological order).
    Returns list of dicts ordered by race_date asc. Course-scoped.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    cols = ["race_id", "race_date", "race_title",
            "world_overall",    "world_swim",    "world_bike",    "world_run",    "world_transition",
            "national_overall", "national_swim", "national_bike", "national_run", "national_transition",
            "overall_s", "swim_s", "bike_s", "run_s", "t1_s", "t2_s",
            "overall_diff", "swim_diff", "bike_diff", "run_diff", "t1_diff", "t2_diff",
            "status"]
    return _dicts(cols, conn.execute(f"""
        WITH leader AS (
            SELECT race_id,
                MIN(CASE WHEN overall_s > 0 THEN overall_s END) AS overall_s,
                MIN(CASE WHEN swim_s    > 0 THEN swim_s    END) AS swim_s,
                MIN(CASE WHEN bike_s    > 0 THEN bike_s    END) AS bike_s,
                MIN(CASE WHEN run_s     > 0 THEN run_s     END) AS run_s,
                MIN(CASE WHEN t1_s      > 0 THEN t1_s      END) AS t1_s,
                MIN(CASE WHEN t2_s      > 0 THEN t2_s      END) AS t2_s
            FROM results
            GROUP BY race_id
        )
        SELECT
            rk.race_id,
            r.race_date,
            r.race_title,
            rk.active_world_overall,    rk.active_world_swim,    rk.active_world_bike,    rk.active_world_run,    rk.active_world_transition,
            rk.national_overall, rk.national_swim, rk.national_bike, rk.national_run, rk.national_transition,
            res.overall_s, res.swim_s, res.bike_s, res.run_s, res.t1_s, res.t2_s,
            res.overall_s - l.overall_s AS overall_diff,
            res.swim_s    - l.swim_s    AS swim_diff,
            res.bike_s    - l.bike_s    AS bike_diff,
            res.run_s     - l.run_s     AS run_diff,
            res.t1_s      - l.t1_s      AS t1_diff,
            res.t2_s      - l.t2_s      AS t2_diff,
            res.status
        FROM rankings rk
        JOIN races r ON rk.race_id = r.race_id
        LEFT JOIN results res ON rk.race_id = res.race_id AND rk.athlete_id = res.athlete_id
        LEFT JOIN leader  l   ON rk.race_id = l.race_id
        WHERE rk.athlete_id = ? AND rk.category = ? AND r.distance IN {course_in}
        ORDER BY r.race_date ASC, rk.race_id ASC
    """, [athlete_id, category]))


# ---------------------------------------------------------------------------
# Race predictions
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_prediction_models():
    """Return all prediction models as dict: (gender, distance, discipline) -> {slope, intercept, year_coef}.

    Cached for process lifetime — the prediction_models table only changes
    when ratings rebuild runs, which restarts the app. Called on every race
    and upcoming-race page.

    year_coef applies to long-course models only — see ratings.YEAR_REF and
    the fit logic. Short-course rows have year_coef = 0.
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT gender, distance, discipline, slope, intercept, "
            "       COALESCE(year_coef, 0.0) "
            "FROM prediction_models"
        ).fetchall()
    except Exception:
        return {}  # table not yet created in this DB (schema migration pending)
    return {(r[0], r[1], r[2]): {"slope": r[3], "intercept": r[4], "year_coef": r[5]} for r in rows}


# Sensible discipline-time windows used to filter an athlete's own history
# before aggregating. Mirrors TIME_BOUNDS in ratings._fit_prediction_models;
# keep in sync if those change. Transitions aren't included — they vary too
# much with course layout to anchor a prediction on.
_HISTORY_TIME_BOUNDS = {
    'sprint':   {'overall': ( 1800,  5400), 'swim': ( 240,  1500), 'bike': ( 600,  3600), 'run': ( 480,  2400)},
    'standard': {'overall': ( 5400, 10800), 'swim': ( 600,  3600), 'bike': (2400,  7200), 'run': (1800,  5400)},
    'middle':   {'overall': (12600, 21600), 'swim': (1200,  3600), 'bike': (6000, 12000), 'run': (3600,  7200)},
    't100':     {'overall': (10800, 18000), 'swim': ( 900,  3600), 'bike': (6000, 12000), 'run': (2700,  7200)},
    'long':     {'overall': (25200, 50400), 'swim': (2400,  6000), 'bike': (12000, 25200), 'run': (8400, 21600)},
}

def get_athlete_discipline_history(athlete_id, distance, discipline, limit=10,
                                    before_date=None):
    """Recent finishing split times for this athlete at the given distance and
    discipline, newest first, filtered to the sensible time window for that
    distance. Used to anchor race predictions on the athlete's own recent form
    rather than a population quantile fit — a typical elite runs near their
    own median, not the population's p20.

    `before_date` (optional) restricts to finishes strictly before that date.
    Historical race-page displays pass the target race's date to avoid
    leaking post-race history into the retrospective prediction (eg. an
    athlete's 2010-era tech times would otherwise bias a 2005 race prediction
    faster than the era's actual performance). For upcoming races, leave
    None — there's no future history to leak.
    """
    if discipline not in ('overall', 'swim', 'bike', 'run'):
        return []
    bounds = _HISTORY_TIME_BOUNDS.get(distance)
    if not bounds:
        return []
    lo, hi = bounds[discipline]
    col = f"{discipline}_s"
    conn = _get_conn()
    params = [athlete_id, distance, lo, hi]
    date_clause = ""
    if before_date is not None:
        date_clause = "AND r.race_date < ?"
        params.append(before_date)
    params.append(limit)
    rows = conn.execute(f"""
        SELECT res.{col}
        FROM results res
        JOIN races r ON res.race_id = r.race_id
        WHERE res.athlete_id = ?
          AND r.distance = ?
          AND res.status = 'Finished'
          AND res.{col} BETWEEN ? AND ?
          {date_clause}
        ORDER BY r.race_date DESC, r.race_id DESC
        LIMIT ?
    """, params).fetchall()
    return [r[0] for r in rows]


def get_race_distance_type(race_id):
    """Return the distance enum: 'sprint', 'standard', 'middle', 't100', 'long' or None.

    Reads `races.distance` directly — populated at ingest time (WT via
    `infer_distance` in ingest.py, PTO via `_parse_distance` in pto_ingest.py).
    Keyed the same way as prediction_models rows so callers can look up a
    model with no aliasing.
    """
    conn = _get_conn()
    row = conn.execute("SELECT distance FROM races WHERE race_id = ?", [race_id]).fetchone()
    return row[0] if row else None


@lru_cache(maxsize=1024)
def get_race_pre_race_ratings(race_id):
    """Return most-recent rating for each field athlete before this race date.

    Returns list of dicts with athlete_id + per-discipline ratings.
    Athletes with no prior race in the same course bucket are absent from the result.

    Previously the prior_starts column used a correlated subquery that scanned
    results once per field athlete. This version computes prior_starts in one
    grouped aggregate scoped to the field, dropping it from O(field × races)
    to O(prior_results_for_field).
    """
    conn = _get_conn()
    row = conn.execute("SELECT distance, race_date FROM races WHERE race_id = ?", [race_id]).fetchone()
    if not row:
        return []
    course = course_for_distance(row[0])
    if course is None:
        return []
    target_date = row[1]
    course_in = _course_in(course)
    cols = ["athlete_id", "overall", "swim", "bike", "run", "transition", "prior_starts"]
    return _dicts(cols, conn.execute(f"""
        WITH field AS (SELECT DISTINCT athlete_id FROM results WHERE race_id = ?),
             prior_counts AS (
                 SELECT res.athlete_id, COUNT(*) AS prior_starts
                 FROM results res
                 JOIN races r ON res.race_id = r.race_id
                 JOIN field f ON res.athlete_id = f.athlete_id
                 WHERE r.race_date < ?
                   AND r.distance IN {course_in}
                 GROUP BY res.athlete_id
             ),
             latest_rating AS (
                 SELECT DISTINCT ON (ra.athlete_id)
                        ra.athlete_id, ra.overall, ra.swim, ra.bike, ra.run, ra.transition
                 FROM ratings ra
                 JOIN races r ON ra.race_id = r.race_id
                 JOIN field f ON ra.athlete_id = f.athlete_id
                 WHERE r.race_date < ?
                   AND r.distance IN {course_in}
                 ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
             )
        SELECT lr.athlete_id, lr.overall, lr.swim, lr.bike, lr.run, lr.transition,
               COALESCE(pc.prior_starts, 0) AS prior_starts
        FROM latest_rating lr
        LEFT JOIN prior_counts pc ON pc.athlete_id = lr.athlete_id
    """, [race_id, target_date, target_date]))


# ---------------------------------------------------------------------------
# Form model (see ptd_data/form.py)
# ---------------------------------------------------------------------------

FORM_MIN_PRIOR = 3   # observations before a form value feeds race predictions


@lru_cache(maxsize=1024)
def get_field_form(athlete_ids, course, before_date=None):
    """Latest form per (athlete, discipline) in the course bucket.

    athlete_ids must be a tuple (hashable for the cache). `before_date`
    restricts to races strictly before that date - pass the race date on
    historical race pages (mirrors get_race_pre_race_ratings); leave None
    for upcoming races where current form is wanted.

    Returns {athlete_id: {discipline: form_rel}}, only for athletes with at
    least FORM_MIN_PRIOR observations behind the value.
    """
    if not athlete_ids:
        return {}
    conn = _get_conn()
    ids_in = ", ".join("?" * len(athlete_ids))
    params = [*athlete_ids]
    date_clause = ""
    if before_date is not None:
        date_clause = "AND r.race_date < ?"
        params.append(before_date)
    rows = conn.execute(f"""
        SELECT DISTINCT ON (af.athlete_id, af.discipline)
               af.athlete_id, af.discipline, af.form_rel
        FROM athlete_form af
        JOIN races r ON af.race_id = r.race_id
        WHERE af.athlete_id IN ({ids_in})
          AND r.distance IN {_course_in(course)}
          AND af.n_obs >= {FORM_MIN_PRIOR}
          {date_clause}
        ORDER BY af.athlete_id, af.discipline, r.race_date DESC, af.race_id DESC
    """, params).fetchall()
    out = {}
    for aid, disc, form_rel in rows:
        out.setdefault(aid, {})[disc] = form_rel
    return out


def get_field_start_counts(athlete_ids, course, before_date=None):
    """Per-athlete count of prior elite starts in the course bucket (sprint/
    standard for short, middle/t100/long for long), for the low-confidence
    prediction flag. `before_date` restricts to races strictly before that date
    (pass the race date on historical pages; leave None for upcoming, where
    current counts are wanted). Returns {athlete_id: n_starts}; athletes with no
    starts are absent (caller treats missing as 0).
    """
    if not athlete_ids:
        return {}
    conn = _get_conn()
    ids_in = ", ".join("?" * len(athlete_ids))
    params = [*athlete_ids]
    date_clause = ""
    if before_date is not None:
        date_clause = "AND r.race_date < ?"
        params.append(before_date)
    rows = conn.execute(f"""
        SELECT res.athlete_id, COUNT(DISTINCT res.race_id)
        FROM results res JOIN races r ON res.race_id = r.race_id
        WHERE res.athlete_id IN ({ids_in})
          AND r.category = 'elite' AND r.distance IN {_course_in(course)}
          {date_clause}
        GROUP BY res.athlete_id
    """, params).fetchall()
    return {aid: n for aid, n in rows}


@lru_cache(maxsize=1024)
def get_field_rating_trends(athlete_ids, course, before_date=None, n=5):
    """Recent overall-rating slope (rating points per race, positive = improving)
    for each field athlete, from their last `n` rated elite races in the course
    bucket strictly before `before_date` (or all-time if None).

    Used to nudge short-course predictions for the ELO's lag on trending
    athletes (race_page._apply_momentum). Only athletes with >= 3 ratings are
    returned; the caller leaves the rest unadjusted.
    """
    if not athlete_ids:
        return {}
    conn = _get_conn()
    ids_in = ", ".join("?" * len(athlete_ids))
    params = [*athlete_ids]
    date_clause = ""
    if before_date is not None:
        date_clause = "AND r.race_date < ?"
        params.append(before_date)
    rows = conn.execute(f"""
        SELECT ra.athlete_id, ra.overall
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        WHERE ra.athlete_id IN ({ids_in})
          AND r.distance IN {_course_in(course)}
          AND ra.category = 'elite'
          {date_clause}
        ORDER BY ra.athlete_id, r.race_date, ra.race_id
    """, params).fetchall()

    by_athlete = {}
    for aid, overall in rows:
        by_athlete.setdefault(aid, []).append(overall)
    trends = {}
    for aid, vals in by_athlete.items():
        seq = vals[-n:]
        k = len(seq)
        if k < 3:
            continue
        mx = (k - 1) / 2.0
        my = sum(seq) / k
        sxx = sum((i - mx) ** 2 for i in range(k))
        trends[aid] = sum((i - mx) * (v - my) for i, v in enumerate(seq)) / sxx
    return trends


@lru_cache(maxsize=2048)
def get_form_course_constants(event_id, gender, distance, before_date):
    """Pre-race course constants {discipline: C} for predicting outright
    times as exp(form_rel + C).

    Mean C of the event's last 3 editions (same recurring event, gender and
    distance, strictly before the race date); disciplines without event
    history fall back to the all-time mean for the (gender, distance).
    Validated in analysis/model_compare.py.
    """
    conn = _get_conn()
    out = {}
    if event_id is not None:
        rows = conn.execute("""
            SELECT fc.discipline, fc.c
            FROM form_race_constants fc
            JOIN races r ON fc.race_id = r.race_id
            JOIN event_recurring er ON er.event_id = r.event_id
            WHERE er.recurring_event_id IN (
                      SELECT recurring_event_id FROM event_recurring WHERE event_id = ?)
              AND r.gender = ? AND r.distance = ? AND r.race_date < ?
            ORDER BY fc.discipline, r.race_date DESC
        """, [event_id, gender, distance, before_date]).fetchall()
        by_disc = {}
        for disc, c in rows:
            if len(by_disc.setdefault(disc, [])) < 3:
                by_disc[disc].append(c)
        out = {disc: sum(cs) / len(cs) for disc, cs in by_disc.items()}
    rows = conn.execute("""
        SELECT fc.discipline, AVG(fc.c)
        FROM form_race_constants fc
        JOIN races r ON fc.race_id = r.race_id
        WHERE r.gender = ? AND r.distance = ? AND r.race_date < ?
        GROUP BY fc.discipline
    """, [gender, distance, before_date]).fetchall()
    for disc, c in rows:
        out.setdefault(disc, c)
    return out


def get_athlete_form(athlete_id, course):
    """Athlete's current form per discipline for the profile display.

    Returns {discipline: {form_rel, n_obs, last_race_date}} from the latest
    observation per discipline in the course bucket.
    """
    conn = _get_conn()
    rows = conn.execute(f"""
        SELECT DISTINCT ON (af.discipline)
               af.discipline, af.form_rel, af.n_obs, r.race_date
        FROM athlete_form af
        JOIN races r ON af.race_id = r.race_id
        WHERE af.athlete_id = ?
          AND r.distance IN {_course_in(course)}
        ORDER BY af.discipline, r.race_date DESC, af.race_id DESC
    """, [athlete_id]).fetchall()
    return {disc: {"form_rel": f, "n_obs": n, "last_race_date": d}
            for disc, f, n, d in rows}


@lru_cache(maxsize=4)
def get_form_reference_times(course):
    """Typical split per (gender, distance, discipline) for mapping form to
    a real time: median exp(C) over the last 3 years of races. C is field-
    strength adjusted, so this is a neutral-course, neutral-field split.

    Short course is restricted to world-level races: lower-tier courses run
    ~5% slow even after field correction (inaccurately measured courses),
    and the display labels promise true distances (750m, 5km, ...). Long
    course has no tier structure and IM-brand courses are consistent.
    """
    conn = _get_conn()
    rows = conn.execute(f"""
        SELECT r.gender, r.distance, fc.discipline, fc.c, r.cat_ids
        FROM form_race_constants fc
        JOIN races r ON fc.race_id = r.race_id
        WHERE r.distance IN {_course_in(course)}
          AND r.race_date >= CURRENT_DATE - INTERVAL 3 YEAR
    """).fetchall()
    world = {'Games', 'WTCS', 'World Cup'}
    by_key = {}
    for g, dist, disc, c, cat_ids in rows:
        if course == 'short' and _tier_for(cat_ids) not in world:
            continue
        by_key.setdefault((g, dist, disc), []).append(math.exp(c))
    return {k: statistics.median(v) for k, v in by_key.items()}


@lru_cache(maxsize=2048)
def get_athlete_rivals(athlete_id, category='elite', course='short', limit=6):
    """Athletes the subject races against often with mixed results.

    Score: sqrt(n_meets) * min(wins,losses)/max(wins,losses) — rewards both
    meeting volume and head-to-head balance. Restricted to last 5 years (keeps
    the section current — old rivalries get forgotten), same gender/category/
    course bucket, and opponents whose latest overall rating is within 150
    points of the subject's.

    Cached per process — DB doesn't change between deploys.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    # Long-course ratings spread roughly 2x wider than short, so widen the
    # similarity window to match — otherwise top long-course athletes get
    # only one or two matches.
    rating_window = 400 if course == 'long' else 200

    subj_rating_row = conn.execute(f"""
        SELECT ra.overall
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        WHERE ra.athlete_id = ? AND ra.category = ? AND r.distance IN {course_in}
        ORDER BY r.race_date DESC, ra.race_id DESC
        LIMIT 1
    """, [athlete_id, category]).fetchone()
    if not subj_rating_row:
        return []
    subj_rating = subj_rating_row[0]

    subj_row = conn.execute(
        "SELECT gender, country_full FROM athletes WHERE athlete_id = ?", [athlete_id]
    ).fetchone()
    if not subj_row:
        return []
    subj_gender, subj_country = subj_row

    rows = conn.execute(f"""
        WITH my_results AS (
            SELECT res.race_id, res.position AS my_pos, r.race_date
            FROM results res
            JOIN races r ON res.race_id = r.race_id
            WHERE res.athlete_id = ?
              AND res.status = 'Finished'
              AND res.position IS NOT NULL
              AND r.category = ?
              AND r.distance IN {course_in}
              AND r.race_date >= CURRENT_DATE - INTERVAL 5 YEAR
        ),
        shared AS (
            SELECT my.race_id, my.my_pos, op.athlete_id AS opp_id, op.position AS opp_pos
            FROM my_results my
            JOIN results op ON op.race_id = my.race_id
            WHERE op.athlete_id <> ?
              AND op.status = 'Finished'
              AND op.position IS NOT NULL
        ),
        opp_stats AS (
            SELECT opp_id,
                   COUNT(*) AS n_meets,
                   SUM(CASE WHEN my_pos < opp_pos THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN my_pos > opp_pos THEN 1 ELSE 0 END) AS losses
            FROM shared
            GROUP BY opp_id
            HAVING COUNT(*) >= 4
               AND SUM(CASE WHEN my_pos < opp_pos THEN 1 ELSE 0 END) >= 1
               AND SUM(CASE WHEN my_pos > opp_pos THEN 1 ELSE 0 END) >= 1
        ),
        opp_latest_rating AS (
            SELECT DISTINCT ON (ra.athlete_id)
                   ra.athlete_id, ra.overall
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE ra.athlete_id IN (SELECT opp_id FROM opp_stats)
              AND ra.category = ?
              AND r.distance IN {course_in}
            ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
        )
        SELECT a.athlete_id, a.name, a.profile_img, n.alpha3,
               os.n_meets, os.wins, os.losses, olr.overall,
               SQRT(os.n_meets) * LEAST(os.wins, os.losses)::DOUBLE
                                / GREATEST(os.wins, os.losses)
                                * CASE WHEN a.country_full = ? THEN 1.2 ELSE 1.0 END AS score
        FROM opp_stats os
        JOIN opp_latest_rating olr ON olr.athlete_id = os.opp_id
        JOIN athletes a ON a.athlete_id = os.opp_id
        JOIN nationalities n ON n.country_full = a.country_full
        WHERE a.gender = ?
          AND ABS(olr.overall - ?) <= ?
        ORDER BY score DESC
        LIMIT ?
    """, [athlete_id, category, athlete_id, category,
          subj_country, subj_gender, subj_rating, rating_window, limit]).fetchall()

    return [{
        "athlete_id":   r[0],
        "name":         r[1],
        "profile_img":  r[2],
        "country_alpha3": r[3],
        "n_meets":      r[4],
        "wins":         r[5],
        "losses":       r[6],
    } for r in rows]


# --- Upcoming race queries ---

def get_athlete_upcoming_races(athlete_id):
    """
    All upcoming races an athlete is entered in, ordered by date.
    Returns list of dicts with race info needed for predictions.
    """
    conn = _get_conn()
    cols = ["race_id", "prog_name", "race_date", "gender", "event_spec_ids",
            "category", "event_name", "event_id", "country"]
    return _dicts(cols, conn.execute("""
        SELECT
            ur.race_id,
            ur.prog_name,
            ur.race_date,
            ur.gender,
            ur.event_spec_ids,
            ur.category,
            e.name      AS event_name,
            e.event_id,
            e.country
        FROM start_list_entries sle
        JOIN upcoming_races ur ON sle.race_id = ur.race_id
        JOIN events e          ON ur.event_id = e.event_id
        WHERE sle.athlete_id = ?
        ORDER BY ur.race_date ASC
    """, [athlete_id]))


def get_upcoming_race_info(race_id):
    """Basic race info from upcoming_races + events. Returns None if not found."""
    conn = _get_conn()
    row = conn.execute("""
        SELECT ur.race_id, ur.race_title, ur.prog_name, ur.race_date,
               e.venue AS location, e.country, ur.gender, ur.race_handle,
               ur.event_id, ur.event_spec_ids, ur.category
        FROM upcoming_races ur
        JOIN events e ON ur.event_id = e.event_id
        WHERE ur.race_id = ?
    """, [race_id]).fetchone()
    if not row:
        return None
    cols = ["race_id", "race_title", "prog_name", "race_date",
            "location", "country", "gender", "race_handle", "event_id",
            "event_spec_ids", "category"]
    return dict(zip(cols, row))


def get_upcoming_race_entries(race_id, course='short'):
    """Start list entries with athlete details and current ratings, scoped to course."""
    conn = _get_conn()
    course_in = _course_in(course)
    cols = ["athlete_id", "start_num", "name", "year_of_birth", "profile_img",
            "country_alpha3",
            "overall_rating", "swim_rating", "bike_rating", "run_rating", "transition_rating"]
    return _dicts(cols, conn.execute(f"""
        SELECT
            sle.athlete_id,
            sle.start_num,
            a.name,
            a.year_of_birth,
            a.profile_img,
            n.alpha3 AS country_alpha3,
            ra.overall    AS overall_rating,
            ra.swim       AS swim_rating,
            ra.bike       AS bike_rating,
            ra.run        AS run_rating,
            ra.transition AS transition_rating
        FROM start_list_entries sle
        JOIN athletes a      ON sle.athlete_id = a.athlete_id
        JOIN nationalities n ON a.country_full = n.country_full
        LEFT JOIN (
            SELECT ra2.athlete_id, ra2.overall, ra2.swim, ra2.bike, ra2.run, ra2.transition
            FROM ratings ra2
            JOIN races r2 ON ra2.race_id = r2.race_id
            WHERE r2.distance IN {course_in}
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY ra2.athlete_id ORDER BY r2.race_date DESC, ra2.race_id DESC
            ) = 1
        ) ra ON ra.athlete_id = sle.athlete_id
        WHERE sle.race_id = ?
        ORDER BY sle.start_num
    """, [race_id]))


def get_upcoming_race_standards(race_id, course='short'):
    """Top-K weighted standard for an upcoming start list, scoped to course.

    The start list has no finishing positions yet, so we rank each discipline
    by current pre-race rating (best -> worst) and apply the same exp-decay
    formula used on completed races. Denominator is set by standard_denom on
    the entrant count, so thin start lists are penalised against the floor —
    keeps the upcoming race rank directly comparable to historical races.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    row = conn.execute(f"""
        WITH ranked AS (
            SELECT ra.overall, ra.swim, ra.bike, ra.run, ra.transition,
                   ROW_NUMBER() OVER (ORDER BY ra.overall    DESC) AS overall_pos,
                   ROW_NUMBER() OVER (ORDER BY ra.swim       DESC) AS swim_pos,
                   ROW_NUMBER() OVER (ORDER BY ra.bike       DESC) AS bike_pos,
                   ROW_NUMBER() OVER (ORDER BY ra.run        DESC) AS run_pos,
                   ROW_NUMBER() OVER (ORDER BY ra.transition DESC) AS transition_pos
            FROM start_list_entries sle
            JOIN (
                SELECT ra2.athlete_id, ra2.overall, ra2.swim, ra2.bike, ra2.run, ra2.transition
                FROM ratings ra2
                JOIN races r2 ON ra2.race_id = r2.race_id
                WHERE r2.distance IN {course_in}
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY ra2.athlete_id ORDER BY r2.race_date DESC, ra2.race_id DESC
                ) = 1
            ) ra ON ra.athlete_id = sle.athlete_id
            WHERE sle.race_id = ?
        )
        SELECT
            COUNT(*),
            SUM(overall    * CASE WHEN overall_pos    <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (overall_pos    - 1)) ELSE 0 END),
            SUM(swim       * CASE WHEN swim_pos       <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (swim_pos       - 1)) ELSE 0 END),
            SUM(bike       * CASE WHEN bike_pos       <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (bike_pos       - 1)) ELSE 0 END),
            SUM(run        * CASE WHEN run_pos        <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (run_pos        - 1)) ELSE 0 END),
            SUM(transition * CASE WHEN transition_pos <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (transition_pos - 1)) ELSE 0 END)
        FROM ranked
    """, [race_id]).fetchone()
    if not row or row[0] is None or row[0] == 0:
        return {d: 0.0 for d in ["overall", "swim", "bike", "run", "transition"]}
    n, ov, sw, bk, rn_, tr = row
    denom = standard_denom(n)
    return {
        "overall":    (ov or 0.0) / denom,
        "swim":       (sw or 0.0) / denom,
        "bike":       (bk or 0.0) / denom,
        "run":        (rn_ or 0.0) / denom,
        "transition": (tr or 0.0) / denom,
    }


def get_upcoming_race_entries_bulk(race_ids, course='short'):
    """Bulk get_upcoming_race_entries. Returns {race_id: [entries]}."""
    if not race_ids:
        return {}
    conn = _get_conn()
    race_ids = list(dict.fromkeys(race_ids))
    placeholders = ",".join("?" * len(race_ids))
    course_in = _course_in(course)
    rows = conn.execute(f"""
        SELECT
            sle.race_id,
            sle.athlete_id,
            sle.start_num,
            a.name,
            a.year_of_birth,
            a.profile_img,
            n.alpha3 AS country_alpha3,
            ra.overall    AS overall_rating,
            ra.swim       AS swim_rating,
            ra.bike       AS bike_rating,
            ra.run        AS run_rating,
            ra.transition AS transition_rating
        FROM start_list_entries sle
        JOIN athletes a      ON sle.athlete_id = a.athlete_id
        JOIN nationalities n ON a.country_full = n.country_full
        LEFT JOIN (
            SELECT ra2.athlete_id, ra2.overall, ra2.swim, ra2.bike, ra2.run, ra2.transition
            FROM ratings ra2
            JOIN races r2 ON ra2.race_id = r2.race_id
            WHERE r2.distance IN {course_in}
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY ra2.athlete_id ORDER BY r2.race_date DESC, ra2.race_id DESC
            ) = 1
        ) ra ON ra.athlete_id = sle.athlete_id
        WHERE sle.race_id IN ({placeholders})
        ORDER BY sle.race_id, sle.start_num
    """, race_ids).fetchall()
    cols = ["athlete_id", "start_num", "name", "year_of_birth", "profile_img",
            "country_alpha3",
            "overall_rating", "swim_rating", "bike_rating", "run_rating", "transition_rating"]
    result = {rid: [] for rid in race_ids}
    for row in rows:
        rid = row[0]
        result[rid].append(dict(zip(cols, row[1:])))
    return result


def get_upcoming_race_distance_types_bulk(race_ids):
    """Bulk get_upcoming_race_distance_type. Returns {race_id: 'sprint'|'standard'|None}."""
    if not race_ids:
        return {}
    conn = _get_conn()
    race_ids = list(dict.fromkeys(race_ids))
    placeholders = ",".join("?" * len(race_ids))
    rows = conn.execute(
        f"SELECT race_id, event_spec_ids FROM upcoming_races WHERE race_id IN ({placeholders})",
        race_ids,
    ).fetchall()
    result = {rid: None for rid in race_ids}
    for rid, spec in rows:
        if spec is None:
            continue
        has_sprint   = '376' in spec
        has_standard = '377' in spec
        if has_sprint and not has_standard:
            result[rid] = 'sprint'
        elif has_standard and not has_sprint:
            result[rid] = 'standard'
    return result


def get_upcoming_race_standards_bulk(race_ids, course='short'):
    """Bulk get_upcoming_race_standards. Returns {race_id: {overall, swim, bike, run, transition}}."""
    if not race_ids:
        return {}
    conn = _get_conn()
    race_ids = list(dict.fromkeys(race_ids))
    placeholders = ",".join("?" * len(race_ids))
    course_in = _course_in(course)
    rows = conn.execute(f"""
        WITH ranked AS (
            SELECT sle.race_id,
                   ra.overall, ra.swim, ra.bike, ra.run, ra.transition,
                   ROW_NUMBER() OVER (PARTITION BY sle.race_id ORDER BY ra.overall    DESC) AS overall_pos,
                   ROW_NUMBER() OVER (PARTITION BY sle.race_id ORDER BY ra.swim       DESC) AS swim_pos,
                   ROW_NUMBER() OVER (PARTITION BY sle.race_id ORDER BY ra.bike       DESC) AS bike_pos,
                   ROW_NUMBER() OVER (PARTITION BY sle.race_id ORDER BY ra.run        DESC) AS run_pos,
                   ROW_NUMBER() OVER (PARTITION BY sle.race_id ORDER BY ra.transition DESC) AS transition_pos
            FROM start_list_entries sle
            JOIN (
                SELECT ra2.athlete_id, ra2.overall, ra2.swim, ra2.bike, ra2.run, ra2.transition
                FROM ratings ra2
                JOIN races r2 ON ra2.race_id = r2.race_id
                WHERE r2.distance IN {course_in}
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY ra2.athlete_id ORDER BY r2.race_date DESC, ra2.race_id DESC
                ) = 1
            ) ra ON ra.athlete_id = sle.athlete_id
            WHERE sle.race_id IN ({placeholders})
        )
        SELECT
            race_id,
            COUNT(*),
            SUM(overall    * CASE WHEN overall_pos    <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (overall_pos    - 1)) ELSE 0 END),
            SUM(swim       * CASE WHEN swim_pos       <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (swim_pos       - 1)) ELSE 0 END),
            SUM(bike       * CASE WHEN bike_pos       <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (bike_pos       - 1)) ELSE 0 END),
            SUM(run        * CASE WHEN run_pos        <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (run_pos        - 1)) ELSE 0 END),
            SUM(transition * CASE WHEN transition_pos <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (transition_pos - 1)) ELSE 0 END)
        FROM ranked
        GROUP BY race_id
    """, race_ids).fetchall()
    result = {rid: dict(_DISC_DEFAULT) for rid in race_ids}
    for row in rows:
        rid, n, ov, sw, bk, rn_, tr = row
        if not n or n == 0:
            continue
        denom = standard_denom(n)
        result[rid] = {
            "overall":    (ov or 0.0) / denom,
            "swim":       (sw or 0.0) / denom,
            "bike":       (bk or 0.0) / denom,
            "run":        (rn_ or 0.0) / denom,
            "transition": (tr or 0.0) / denom,
        }
    return result



def get_upcoming_events(country=None, course='short'):
    """All upcoming events grouped with their races and entry counts.

    Optional `country` filter restricts to events hosted in that country (name).
    Predictions/podiums are computed by the routers from the full start list
    via the shared prediction core (race_page._upcoming_pred_seconds).
    """
    conn = _get_conn()
    # Only events that haven't finished yet. Gate on the event's own latest race
    # date, not start_date, so a multi-day event still shows on its final day.
    where = ["e.event_id IN (SELECT event_id FROM upcoming_races GROUP BY event_id "
             "HAVING MAX(race_date) >= CURRENT_DATE)"]
    country_params = []
    if country:
        where.append("e.country = ?")
        country_params.append(country)
    where_sql = "WHERE " + " AND ".join(where)
    race_rows = conn.execute(f"""
        SELECT
            e.event_id, e.name, e.venue, e.country, e.start_date,
            ur.race_id, ur.prog_name, ur.gender, ur.category, ur.event_spec_ids,
            COUNT(sle.athlete_id) AS entry_count
        FROM events e
        JOIN upcoming_races ur ON ur.event_id = e.event_id
        LEFT JOIN start_list_entries sle ON sle.race_id = ur.race_id
        {where_sql}
        GROUP BY e.event_id, e.name, e.venue, e.country, e.start_date,
                 ur.race_id, ur.prog_name, ur.gender, ur.category, ur.event_spec_ids
        ORDER BY e.start_date, e.event_id,
                 CASE WHEN ur.gender = 'male' THEN 0 ELSE 1 END,
                 ur.race_id
    """, country_params).fetchall()

    race_cols = ["event_id", "name", "venue", "country", "start_date",
                 "race_id", "prog_name", "gender", "category", "event_spec_ids", "entry_count"]

    if not race_rows:
        return []

    events = {}
    for row in race_rows:
        r = dict(zip(race_cols, row))
        eid = r["event_id"]
        if eid not in events:
            venue = str(r["venue"]).strip() if r["venue"] else ""
            events[eid] = {
                "event_id":   eid,
                "name":       r["name"],
                "venue":      venue or None,
                "country":    r["country"],
                "start_date": r["start_date"],
                "races":      [],
            }
        # event_id + start_date are carried onto each race so the router can feed
        # the shared prediction core (race_page._upcoming_pred_seconds).
        events[eid]["races"].append({
            "race_id":        r["race_id"],
            "event_id":       eid,
            "start_date":     r["start_date"],
            "prog_name":      r["prog_name"],
            "gender":         r["gender"],
            "category":       r["category"],
            "event_spec_ids": r["event_spec_ids"],
            "entry_count":    r["entry_count"],
        })

    return list(events.values())


def get_upcoming_race_leaderboard(gender, course, country=None):
    """Upcoming races as race-leaderboard rows with predicted standards.

    Same shape as get_race_leaderboard rows (sans winner / stored ranks) so the
    route can merge the two lists. Short-course-only — upcoming standards are
    computed from short-course ratings.
    """
    if course != 'short':
        return []

    conn = _get_conn()
    course_in = _course_in(course)
    country_sql, country_params = ("AND e.country = ?", [country]) if country and country != 'all' else ("", [])

    # All upcoming races (gender, optional country) with event meta. Standards
    # are computed in a second pass so the SQL stays readable.
    race_rows = conn.execute(f"""
        SELECT ur.race_id, ur.race_title, ur.prog_name, ur.race_date,
               e.venue, e.country, n.alpha3 AS event_country_alpha3
        FROM upcoming_races ur
        JOIN events e ON ur.event_id = e.event_id
        LEFT JOIN nationalities n ON e.country = n.country_full
        WHERE ur.gender = ?
        {country_sql}
        ORDER BY ur.race_date ASC, ur.race_id ASC
    """, [gender] + country_params).fetchall()

    if not race_rows:
        return []

    race_ids = [r[0] for r in race_rows]
    ph = ",".join("?" * len(race_ids))

    # Bulk computation of per-race weighted standards across the start lists.
    # Mirrors get_upcoming_race_standards's formula but grouped by race_id.
    std_rows = conn.execute(f"""
        WITH ranked AS (
            SELECT
                sle.race_id,
                ra.overall, ra.swim, ra.bike, ra.run, ra.transition,
                ROW_NUMBER() OVER (PARTITION BY sle.race_id ORDER BY ra.overall    DESC) AS overall_pos,
                ROW_NUMBER() OVER (PARTITION BY sle.race_id ORDER BY ra.swim       DESC) AS swim_pos,
                ROW_NUMBER() OVER (PARTITION BY sle.race_id ORDER BY ra.bike       DESC) AS bike_pos,
                ROW_NUMBER() OVER (PARTITION BY sle.race_id ORDER BY ra.run        DESC) AS run_pos,
                ROW_NUMBER() OVER (PARTITION BY sle.race_id ORDER BY ra.transition DESC) AS transition_pos
            FROM start_list_entries sle
            JOIN (
                SELECT ra2.athlete_id, ra2.overall, ra2.swim, ra2.bike, ra2.run, ra2.transition
                FROM ratings ra2
                JOIN races r2 ON ra2.race_id = r2.race_id
                WHERE r2.distance IN {course_in}
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY ra2.athlete_id ORDER BY r2.race_date DESC, ra2.race_id DESC
                ) = 1
            ) ra ON ra.athlete_id = sle.athlete_id
            WHERE sle.race_id IN ({ph})
        )
        SELECT
            race_id,
            COUNT(*) AS n,
            SUM(overall    * CASE WHEN overall_pos    <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (overall_pos    - 1)) ELSE 0 END),
            SUM(swim       * CASE WHEN swim_pos       <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (swim_pos       - 1)) ELSE 0 END),
            SUM(bike       * CASE WHEN bike_pos       <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (bike_pos       - 1)) ELSE 0 END),
            SUM(run        * CASE WHEN run_pos        <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (run_pos        - 1)) ELSE 0 END),
            SUM(transition * CASE WHEN transition_pos <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (transition_pos - 1)) ELSE 0 END)
        FROM ranked
        GROUP BY race_id
    """, race_ids).fetchall()

    stds_by_race = {}
    for race_id, n, ov, sw, bk, rn_, tr in std_rows:
        denom = standard_denom(n) if n else None
        stds_by_race[race_id] = {
            "overall_std":    (ov or 0.0) / denom if denom else None,
            "swim_std":       (sw or 0.0) / denom if denom else None,
            "bike_std":       (bk or 0.0) / denom if denom else None,
            "run_std":        (rn_ or 0.0) / denom if denom else None,
            "transition_std": (tr or 0.0) / denom if denom else None,
        }

    race_cols = ["race_id", "race_title", "prog_name", "race_date",
                 "venue", "country", "event_country_alpha3"]
    out = []
    for r in race_rows:
        rec = dict(zip(race_cols, r))
        stds = stds_by_race.get(rec["race_id"], {})
        rec.update(stds)
        # Match get_race_leaderboard shape: no winner, no stored ranks
        rec["winner_id"] = None
        rec["winner_name"] = None
        rec["winner_country_alpha3"] = None
        rec["overall_rank"] = None
        rec["swim_rank"] = None
        rec["bike_rank"] = None
        rec["run_rank"] = None
        rec["transition_rank"] = None
        rec["is_upcoming"] = True
        out.append(rec)
    return out


def get_upcoming_races_by_event(event_id):
    """All upcoming races for an event, ordered female-first within race_id (matches event-page ordering)."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT race_id, prog_name, race_date, gender
        FROM upcoming_races
        WHERE event_id = ?
        ORDER BY race_date ASC,
                 CASE WHEN gender = 'male' THEN 0 ELSE 1 END,
                 race_id ASC
    """, [event_id]).fetchall()
    cols = ["race_id", "prog_name", "race_date", "gender"]
    return [{**dict(zip(cols, r)), "is_multi_stage": False, "race_title": None} for r in rows]


def get_upcoming_event_races_detail(event_id, course='short'):
    """Upcoming races for an event with field standards (top-K weighted). Course-scoped.

    Predictions/podium are computed by the router from the full start list
    (get_upcoming_race_entries_bulk) via the shared prediction core.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    race_rows = conn.execute("""
        SELECT race_id, prog_name, race_date, gender, category, event_spec_ids
        FROM upcoming_races
        WHERE event_id = ?
        ORDER BY CASE WHEN gender = 'male' THEN 0 ELSE 1 END,
                 race_id ASC
    """, [event_id]).fetchall()
    if not race_rows:
        return []

    races = [{"race_id": r[0], "prog_name": r[1], "race_date": r[2],
              "gender": r[3], "category": r[4], "event_spec_ids": r[5]}
             for r in race_rows]
    race_ids = [r["race_id"] for r in races]
    ph = ",".join("?" * len(race_ids))

    # Field standards: top-K exp-decay weighted, identical to the per-race
    # get_upcoming_race_standards so the event widget and the race page agree.
    # (A plain AVG over the whole start list used to sit here, which read far
    # lower than the race page since it gave every weak entrant equal weight.)
    std_rows = conn.execute(f"""
        WITH field AS (
            SELECT sle.race_id, ra.overall, ra.swim, ra.bike, ra.run, ra.transition,
                   ROW_NUMBER() OVER (PARTITION BY sle.race_id ORDER BY ra.overall    DESC) AS overall_pos,
                   ROW_NUMBER() OVER (PARTITION BY sle.race_id ORDER BY ra.swim       DESC) AS swim_pos,
                   ROW_NUMBER() OVER (PARTITION BY sle.race_id ORDER BY ra.bike       DESC) AS bike_pos,
                   ROW_NUMBER() OVER (PARTITION BY sle.race_id ORDER BY ra.run        DESC) AS run_pos,
                   ROW_NUMBER() OVER (PARTITION BY sle.race_id ORDER BY ra.transition DESC) AS transition_pos
            FROM start_list_entries sle
            JOIN (
                SELECT ra2.athlete_id, ra2.overall, ra2.swim, ra2.bike, ra2.run, ra2.transition
                FROM ratings ra2
                JOIN races r2 ON ra2.race_id = r2.race_id
                WHERE r2.distance IN {course_in}
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY ra2.athlete_id ORDER BY r2.race_date DESC, ra2.race_id DESC
                ) = 1
            ) ra ON ra.athlete_id = sle.athlete_id
            WHERE sle.race_id IN ({ph})
        )
        SELECT race_id, COUNT(*),
            SUM(overall    * CASE WHEN overall_pos    <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (overall_pos    - 1)) ELSE 0 END),
            SUM(swim       * CASE WHEN swim_pos       <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (swim_pos       - 1)) ELSE 0 END),
            SUM(bike       * CASE WHEN bike_pos       <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (bike_pos       - 1)) ELSE 0 END),
            SUM(run        * CASE WHEN run_pos        <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (run_pos        - 1)) ELSE 0 END),
            SUM(transition * CASE WHEN transition_pos <= {STANDARD_POS_CAP} THEN EXP(-{STANDARD_K} * (transition_pos - 1)) ELSE 0 END)
        FROM field
        GROUP BY race_id
    """, race_ids).fetchall()

    std_by_race = {}
    for race_id, n, ov, sw, bk, rn_, tr in std_rows:
        denom = standard_denom(n)
        std_by_race[race_id] = {
            "overall":    (ov  or 0.0) / denom,
            "swim":       (sw  or 0.0) / denom,
            "bike":       (bk  or 0.0) / denom,
            "run":        (rn_ or 0.0) / denom,
            "transition": (tr  or 0.0) / denom,
        }

    for race in races:
        race["standards_raw"] = std_by_race.get(race["race_id"])

    return races


def get_upcoming_race_distance_type(race_id):
    """Return 'sprint', 'standard', or None from upcoming_races.event_spec_ids."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT event_spec_ids FROM upcoming_races WHERE race_id = ?", [race_id]
    ).fetchone()
    if not row:
        return None
    spec = row[0]
    has_sprint   = '376' in spec
    has_standard = '377' in spec
    if has_sprint and not has_standard:
        return 'sprint'
    if has_standard and not has_sprint:
        return 'standard'
    return None  # ambiguous without winner's time


# --- Series queries ---

_SERIES_META_COLS = ["series_id", "name", "slug", "description", "tier", "continent",
                     "sort_order", "race_count", "earliest_date", "latest_date"]


def _series_meta_sql(where_clause, params):
    """Shared SELECT for series metadata + race counts. `where_clause` may be ''."""
    conn = _get_conn()
    return conn.execute(f"""
        SELECT s.series_id, s.name, s.slug, s.description, s.tier, s.continent, s.sort_order,
               COUNT(DISTINCT r.event_id) AS race_count,
               MIN(r.race_date)  AS earliest_date,
               MAX(r.race_date)  AS latest_date
        FROM series s
        LEFT JOIN event_series es ON es.series_id = s.series_id
        LEFT JOIN races r         ON r.event_id = es.event_id
        {where_clause}
        GROUP BY s.series_id, s.name, s.slug, s.description, s.tier, s.continent, s.sort_order
        ORDER BY s.sort_order, s.name
    """, params).fetchall()


def get_all_series():
    """All series with race counts and date range, ordered by sort_order, name."""
    rows = _series_meta_sql("", [])
    return [dict(zip(_SERIES_META_COLS, r)) for r in rows]


def get_series_index_highlights(series_ids):
    """Per-series highlights for the /series index page.

    For each series returns:
      - latest:       most recent elite event with event metadata and M/W top-3 podiums
      - top_male:     athlete with most men's wins in the series
      - top_female:   athlete with most women's wins in the series
    """
    if not series_ids:
        return {}
    conn = _get_conn()
    ph = ','.join(['?'] * len(series_ids))
    params = list(series_ids)

    # Per-series, per-gender "primary program": prefer elite; otherwise pick
    # the densest AG age band in the popular 25-44 range, standard distance.
    # This drives both the latest-edition podium picks AND the column labels
    # ("Men" for elite, "AG Men 25-29" etc. for AG).
    MAX_EDITIONS = 5
    primary_rows = conn.execute(f"""
        WITH counts AS (
            SELECT es.series_id, r.gender, r.sub_category, r.prog_name,
                   COUNT(*) AS n
            FROM event_series es
            JOIN series ser ON ser.series_id = es.series_id
            JOIN races r ON r.event_id = es.event_id
            WHERE es.series_id IN ({ph})
              AND r.sub_category IN ('elite','ag')
              AND r.gender IN ('male','female')
              -- AG-tier series share event_ids with elite versions
              -- (e.g. Vichy 2024 hosts both); restrict their candidates
              -- to AG races so the picker can't fall back to elite.
              AND (ser.tier NOT LIKE 'ag-%' OR r.sub_category = 'ag')
            GROUP BY es.series_id, r.gender, r.sub_category, r.prog_name
        )
        SELECT series_id, gender, sub_category, prog_name FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY series_id, gender
                ORDER BY
                  CASE WHEN sub_category='elite' THEN 0 ELSE 1 END,
                  CASE WHEN sub_category='ag' AND
                            regexp_matches(COALESCE(prog_name,''),
                                           '^(25-29|30-34|35-39|40-44)') THEN 0 ELSE 1 END,
                  CASE WHEN COALESCE(prog_name,'') LIKE '%Sprint%' THEN 1 ELSE 0 END,
                  n DESC,
                  prog_name  -- final tiebreaker for determinism
            ) AS rn FROM counts
        ) WHERE rn = 1
    """, params).fetchall()
    primary_prog = {(sid, g): (sub, pn) for sid, g, sub, pn in primary_rows}

    def _label(sid, gender, course=None):
        sub_pn = primary_prog.get((sid, gender))
        if not sub_pn:
            return "Men" if gender == "male" else "Women"
        sub, pn = sub_pn
        # Pro long-course programs ("Pro Men" / "Pro Women") render as
        # MPRO/WPRO regardless of how they're filed (Ironman pros sit
        # under sub_category='ag' in our data).
        if pn in ("Pro Men", "Pro Women") or course == "long":
            return "MPRO" if gender == "male" else "WPRO"
        if sub == "elite":
            return "Elite Men" if gender == "male" else "Elite Women"
        # AG: derive band + sprint suffix from prog_name e.g. "30-34 Male AG Sprint"
        parts = (pn or "").split()
        band = parts[0] if parts else ""
        sprint = " Sprint" if "Sprint" in (pn or "") else ""
        return f"AG {'Men' if gender == 'male' else 'Women'} {band}{sprint}".strip()

    event_rows = conn.execute(f"""
        WITH primary_sub AS (
            SELECT series_id,
                   CASE WHEN MAX(CASE WHEN sub_category='elite' THEN 1 ELSE 0 END) = 1
                        THEN 'elite' ELSE 'ag' END AS sub_category
            FROM (
                SELECT es.series_id, r.sub_category
                FROM event_series es
                JOIN series ser ON ser.series_id = es.series_id
                JOIN races r ON r.event_id = es.event_id
                WHERE es.series_id IN ({ph})
                  AND r.sub_category IN ('elite','ag')
                  -- AG-tier series share events with the elite version
                  -- (e.g. Vichy 2024); restrict candidates to AG races so
                  -- the elite-fallback below can never pick elite for them.
                  AND (ser.tier NOT LIKE 'ag-%' OR r.sub_category = 'ag')
            )
            GROUP BY series_id
        ),
        ranked AS (
          SELECT es.series_id, r.event_id,
                 MAX(r.race_date) AS race_date,
                 ROW_NUMBER() OVER (
                   PARTITION BY es.series_id
                   ORDER BY MAX(r.race_date) DESC, r.event_id DESC
                 ) AS rn
          FROM event_series es
          JOIN primary_sub ps ON ps.series_id = es.series_id
          JOIN races r ON r.event_id = es.event_id AND r.sub_category = ps.sub_category
          WHERE es.series_id IN ({ph})
          GROUP BY es.series_id, r.event_id
        )
        SELECT l.series_id, l.event_id, l.race_date, e.name, e.venue, e.country, nat.alpha3, l.rn
        FROM ranked l
        JOIN events e ON e.event_id = l.event_id
        LEFT JOIN nationalities nat ON nat.country_full = e.country
        WHERE l.rn <= ?
        ORDER BY l.series_id, l.rn
    """, params + params + [MAX_EDITIONS]).fetchall()

    editions_by_series = {}
    all_event_ids = []
    for sid, event_id, race_date, ename, venue, country, alpha3, _rn in event_rows:
        editions_by_series.setdefault(sid, []).append({
            "event_id":       event_id,
            "event_name":     ename,
            "venue":          venue,
            "country":        country,
            "country_alpha3": alpha3,
            "race_date":      race_date,
            "male_race_id":   None,
            "female_race_id": None,
            "male_podium":    [],
            "female_podium":  [],
            "is_multi_stage": False,
            "race_count":     0,    # total programs at this event (for "+N" link)
        })
        all_event_ids.append(event_id)

    # Pull all candidate races (elite + AG) for those events, then pick one
    # per (series, edition, gender) by matching the series's primary program
    # (sub_category + prog_name). Also count total races per event so the
    # template can show a "+N races" link to the full event page.
    race_rows = []
    race_count_by_event: dict = {}
    if all_event_ids:
        eph = ','.join(['?'] * len(all_event_ids))
        race_rows = conn.execute(f"""
            SELECT event_id, gender, sub_category, prog_name, race_id, is_multi_stage, distance
            FROM races
            WHERE event_id IN ({eph})
              AND sub_category IN ('elite','ag')
              AND gender IN ('male','female')
        """, all_event_ids).fetchall()
        # Total race count per event (across both genders, both sub-categories)
        for event_id, _g, _s, _pn, _rid, _ms, _d in race_rows:
            race_count_by_event[event_id] = race_count_by_event.get(event_id, 0) + 1

    # Index races as {(event_id, gender, sub_category, prog_name): (race_id, is_multi_stage, distance)}
    races_idx = {(e, g, s, pn): (rid, ms, d) for e, g, s, pn, rid, ms, d in race_rows}

    for sid, eds in editions_by_series.items():
        for info in eds:
            info["race_count"]     = race_count_by_event.get(info["event_id"], 0)
            male_course, female_course = None, None
            for gender, rid_key in (("male", "male_race_id"), ("female", "female_race_id")):
                primary = primary_prog.get((sid, gender))
                if not primary:
                    continue
                sub, pn = primary
                hit = races_idx.get((info["event_id"], gender, sub, pn))
                if hit:
                    info[rid_key] = hit[0]
                    info["is_multi_stage"] = info["is_multi_stage"] or bool(hit[1])
                    course = course_for_distance(hit[2])
                    if gender == "male":
                        male_course = course
                    else:
                        female_course = course
            info["male_label"]   = _label(sid, "male",   male_course)
            info["female_label"] = _label(sid, "female", female_course)

    # Podiums for all those races
    all_race_ids = [
        rid
        for eds in editions_by_series.values()
        for info in eds
        for rid in (info["male_race_id"], info["female_race_id"])
        if rid is not None
    ]
    podiums = {}
    if all_race_ids:
        rph = ','.join(['?'] * len(all_race_ids))
        pod_rows = conn.execute(f"""
            SELECT res.race_id, res.position, res.overall_s,
                   a.athlete_id, a.name, a.profile_img, nat.alpha3
            FROM results res
            JOIN athletes a        ON a.athlete_id = res.athlete_id
            JOIN nationalities nat ON nat.country_full = a.country_full
            WHERE res.race_id IN ({rph})
              AND res.position IN (1,2,3)
              AND res.status = 'Finished'
            ORDER BY res.race_id, res.position
        """, all_race_ids).fetchall()
        for race_id, pos, overall_s, aid, name, img, alpha3 in pod_rows:
            podiums.setdefault(race_id, []).append({
                "position": pos, "overall_s": overall_s,
                "athlete_id": aid, "name": name,
                "profile_img": img, "country_alpha3": alpha3,
            })

    for eds in editions_by_series.values():
        for info in eds:
            info["male_podium"]   = podiums.get(info["male_race_id"], [])
            info["female_podium"] = podiums.get(info["female_race_id"], [])

    # Top men's / women's leaders by wins (all tied athletes per series/gender).
    # Also pull the editions each top athlete won, so the UI can show short names.
    leader_rows = conn.execute(f"""
        WITH wins AS (
          SELECT es.series_id, r.gender, res.athlete_id, r.race_date
          FROM event_series es
          JOIN races   r   ON r.event_id = es.event_id
          JOIN results res ON res.race_id = r.race_id
          WHERE es.series_id IN ({ph})
            AND res.position = 1
            AND res.status = 'Finished'
            AND r.gender IN ('male','female')
            AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
        ),
        counts AS (
          SELECT series_id, gender, athlete_id,
                 COUNT(*) AS n,
                 MAX(race_date) AS latest_date
          FROM wins
          GROUP BY series_id, gender, athlete_id
        ),
        ranked AS (
          SELECT series_id, gender, athlete_id, n, latest_date,
                 MAX(n) OVER (PARTITION BY series_id, gender) AS max_n
          FROM counts
        )
        SELECT rk.series_id, rk.gender, rk.athlete_id, rk.n, rk.latest_date,
               a.name, a.profile_img, nat.alpha3
        FROM ranked rk
        JOIN athletes a        ON a.athlete_id = rk.athlete_id
        JOIN nationalities nat ON nat.country_full = a.country_full
        WHERE rk.n = rk.max_n
        ORDER BY rk.series_id, rk.gender, rk.latest_date DESC, a.name
    """, params).fetchall()

    # Build the leader objects first so we can then attach editions.
    out = {sid: {
        "editions":       editions_by_series.get(sid, []),
        "top_male":       [],
        "top_female":     [],
        # Records label suffix: "" for elite-having series, " (AG)" for AG-only.
        # Records aggregate across all AG bands so we don't show a band here.
        "male_label":     _label(sid, "male"),
        "female_label":   _label(sid, "female"),
        "is_ag_only":     primary_prog.get((sid, "male"), (None, None))[0] == "ag"
                          or primary_prog.get((sid, "female"), (None, None))[0] == "ag",
    } for sid in series_ids}
    leader_keys = set()
    for sid, gender, aid, n, latest_date, name, img, alpha3 in leader_rows:
        key = "top_male" if gender == 'male' else "top_female"
        out[sid][key].append({
            "athlete_id":    aid,
            "name":          name,
            "profile_img":   img,
            "country_alpha3": alpha3,
            "wins":          n,
            "latest_date":   latest_date,
            "editions":      [],
        })
        leader_keys.add((sid, gender, aid))

    # Fetch all won editions for the top athletes only.
    if leader_keys:
        athlete_ids = sorted({aid for _, _, aid in leader_keys})
        aph = ','.join(['?'] * len(athlete_ids))
        ed_rows = conn.execute(f"""
            SELECT es.series_id, r.gender, res.athlete_id,
                   e.event_id, r.race_date, r.race_id, r.race_handle
            FROM event_series es
            JOIN races   r   ON r.event_id = es.event_id
            JOIN events  e   ON e.event_id = r.event_id
            JOIN results res ON res.race_id = r.race_id
            WHERE es.series_id IN ({ph})
              AND res.athlete_id IN ({aph})
              AND res.position = 1
              AND res.status = 'Finished'
              AND r.gender IN ('male','female')
              AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
            ORDER BY r.race_date DESC
        """, params + athlete_ids).fetchall()

        # Index editions by (series, gender, athlete) → list
        ed_by_key = {}
        for sid, gender, aid, event_id, race_date, race_id, race_handle in ed_rows:
            if (sid, gender, aid) not in leader_keys:
                continue
            ed_by_key.setdefault((sid, gender, aid), []).append({
                "event_id":  event_id,
                "race_date": race_date,
                "race_id":   race_id,
                "short":     race_handle,
            })

        for sid in series_ids:
            for gender, key in (("male", "top_male"), ("female", "top_female")):
                for leader in out[sid][key]:
                    leader["editions"] = ed_by_key.get((sid, gender, leader["athlete_id"]), [])

    return out


def get_series_index_records(series_ids):
    """Per-series records used by the /series index page Records pane.

    For every series, returns (per gender) the athlete with the most
    starts / podium finishes / top-10 finishes, plus the race that
    posted the highest standard rating in each of the four disciplines
    (overall / swim / bike / run).

    Return shape: ``{series_id: {"male": {...}, "female": {...}}}`` where
    each gender dict contains any subset of:
      participations: {athlete_id, name, country_alpha3, profile_img, n}
      podiums:        {athlete_id, name, country_alpha3, profile_img, n}
      top10s:         {athlete_id, name, country_alpha3, profile_img, n}
      strongest_overall, strongest_swim, strongest_bike, strongest_run:
          {race_id, event_name, venue, race_date, value}
    """
    if not series_ids:
        return {}
    conn = _get_conn()
    ph = ','.join(['?'] * len(series_ids))
    params = list(series_ids)

    out = {sid: {"male": {}, "female": {}} for sid in series_ids}

    # ── Athlete records: most starts / podiums / top-10s ────────────
    # One row per (series, gender, athlete) with all three counts; keep
    # only those that rank #1 on at least one count. ROW_NUMBER (not
    # RANK) so ties resolve cleanly to a single athlete - newest result
    # wins the tie since "most recent dominance" reads more useful than
    # alphabetical.
    # Same primary-sub fallback as get_series_index_highlights: AG-only
    # series aggregate counts across all AG age bands (the user-facing
    # records line then reads as "best AG athlete in this series across
    # all bands"). Elite-having series use only elite, unchanged.
    ath_rows = conn.execute(f"""
        WITH primary_sub AS (
            SELECT series_id,
                   CASE WHEN MAX(CASE WHEN sub_category='elite' THEN 1 ELSE 0 END) = 1
                        THEN 'elite' ELSE 'ag' END AS sub_category
            FROM (
                SELECT es.series_id, r.sub_category
                FROM event_series es
                JOIN series ser ON ser.series_id = es.series_id
                JOIN races r ON r.event_id = es.event_id
                WHERE es.series_id IN ({ph})
                  AND r.sub_category IN ('elite','ag')
                  -- AG-tier series share events with the elite version
                  -- (e.g. Vichy 2024); restrict candidates to AG races so
                  -- the elite-fallback below can never pick elite for them.
                  AND (ser.tier NOT LIKE 'ag-%' OR r.sub_category = 'ag')
            )
            GROUP BY series_id
        ),
        base AS (
            SELECT es.series_id, r.gender, res.athlete_id,
                   res.position, r.race_date
            FROM event_series es
            JOIN primary_sub ps ON ps.series_id = es.series_id
            JOIN races   r   ON r.event_id = es.event_id AND r.sub_category = ps.sub_category
            JOIN results res ON res.race_id = r.race_id
            WHERE es.series_id IN ({ph})
              AND res.status = 'Finished'
              AND r.gender IN ('male', 'female')
              AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
        ),
        counts AS (
            SELECT series_id, gender, athlete_id,
                   COUNT(*) AS n_starts,
                   COUNT(*) FILTER (WHERE position IN (1, 2, 3)) AS n_podiums,
                   COUNT(*) FILTER (WHERE position IS NOT NULL AND position <= 10) AS n_top10,
                   MAX(race_date) AS latest
            FROM base
            GROUP BY series_id, gender, athlete_id
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY series_id, gender ORDER BY n_starts  DESC, latest DESC, athlete_id) AS rn_starts,
                   ROW_NUMBER() OVER (PARTITION BY series_id, gender ORDER BY n_podiums DESC, latest DESC, athlete_id) AS rn_podiums,
                   ROW_NUMBER() OVER (PARTITION BY series_id, gender ORDER BY n_top10   DESC, latest DESC, athlete_id) AS rn_top10
            FROM counts
        )
        SELECT r.series_id, r.gender, r.athlete_id,
               r.n_starts, r.n_podiums, r.n_top10,
               r.rn_starts, r.rn_podiums, r.rn_top10,
               a.name, a.profile_img, n.alpha3
        FROM ranked r
        JOIN athletes a       ON a.athlete_id = r.athlete_id
        JOIN nationalities n  ON n.country_full = a.country_full
        WHERE r.rn_starts = 1 OR r.rn_podiums = 1 OR r.rn_top10 = 1
    """, params + params).fetchall()

    for sid, gender, aid, n_starts, n_podiums, n_top10, rn_s, rn_p, rn_t, name, img, alpha3 in ath_rows:
        base = {"athlete_id": aid, "name": name, "profile_img": bool(img), "country_alpha3": alpha3}
        bucket = out[sid][gender]
        if rn_s == 1 and n_starts:  bucket["participations"] = {**base, "n": int(n_starts)}
        if rn_p == 1 and n_podiums: bucket["podiums"]        = {**base, "n": int(n_podiums)}
        if rn_t == 1 and n_top10:   bucket["top10s"]         = {**base, "n": int(n_top10)}

    # ── Field records: strongest race per discipline ────────────────
    # Standard rating uses the same EXP-weighted blend as
    # _scoped_standards_history. One row per (series, gender, race)
    # with all four discipline ratings; keep only those that rank #1
    # in at least one discipline.
    field_rows = conn.execute(f"""
        WITH primary_sub AS (
            SELECT series_id,
                   CASE WHEN MAX(CASE WHEN sub_category='elite' THEN 1 ELSE 0 END) = 1
                        THEN 'elite' ELSE 'ag' END AS sub_category
            FROM (
                SELECT es.series_id, r.sub_category
                FROM event_series es
                JOIN series ser ON ser.series_id = es.series_id
                JOIN races r ON r.event_id = es.event_id
                WHERE es.series_id IN ({ph})
                  AND r.sub_category IN ('elite','ag')
                  -- AG-tier series share events with the elite version
                  -- (e.g. Vichy 2024); restrict candidates to AG races so
                  -- the elite-fallback below can never pick elite for them.
                  AND (ser.tier NOT LIKE 'ag-%' OR r.sub_category = 'ag')
            )
            GROUP BY series_id
        ),
        race_stds AS (
            SELECT es.series_id, r.race_id, r.gender, r.prog_name, ps.sub_category,
                   r.race_handle, r.race_date,
                   rr.overall_std, rr.swim_std, rr.bike_std, rr.run_std
            FROM event_series es
            JOIN primary_sub ps ON ps.series_id = es.series_id
            JOIN races   r    ON r.event_id = es.event_id AND r.sub_category = ps.sub_category
            JOIN race_rankings rr ON rr.race_id = r.race_id
            WHERE es.series_id IN ({ph})
              AND r.gender IN ('male', 'female')
              AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY series_id, gender ORDER BY overall_std DESC NULLS LAST, race_id) AS rn_o,
                   ROW_NUMBER() OVER (PARTITION BY series_id, gender ORDER BY swim_std    DESC NULLS LAST, race_id) AS rn_s,
                   ROW_NUMBER() OVER (PARTITION BY series_id, gender ORDER BY bike_std    DESC NULLS LAST, race_id) AS rn_b,
                   ROW_NUMBER() OVER (PARTITION BY series_id, gender ORDER BY run_std     DESC NULLS LAST, race_id) AS rn_r
            FROM race_stds
        )
        SELECT series_id, gender, race_id, prog_name, sub_category,
               race_handle, race_date,
               overall_std, swim_std, bike_std, run_std,
               rn_o, rn_s, rn_b, rn_r
        FROM ranked
        WHERE rn_o = 1 OR rn_s = 1 OR rn_b = 1 OR rn_r = 1
    """, params + params).fetchall()

    for (sid, gender, race_id, prog_name, sub_category,
         race_handle, race_date,
         o_std, s_std, b_std, r_std, rn_o, rn_s, rn_b, rn_r) in field_rows:
        # Only attach prog_name on AG records — for elite the prog ("Elite
        # Men") is implied by the column header and would just add noise.
        common = {"race_id": race_id, "short": race_handle, "race_date": race_date,
                  "prog_name": prog_name if sub_category == 'ag' else None}
        bucket = out[sid][gender]
        if rn_o == 1 and o_std is not None: bucket["strongest_overall"] = {**common, "value": int(round(o_std))}
        if rn_s == 1 and s_std is not None: bucket["strongest_swim"]    = {**common, "value": int(round(s_std))}
        if rn_b == 1 and b_std is not None: bucket["strongest_bike"]    = {**common, "value": int(round(b_std))}
        if rn_r == 1 and r_std is not None: bucket["strongest_run"]     = {**common, "value": int(round(r_std))}

    return out


def get_series_by_slug(slug):
    """Series metadata by slug, or None."""
    rows = _series_meta_sql("WHERE s.slug = ?", [slug])
    return dict(zip(_SERIES_META_COLS, rows[0])) if rows else None


def _program_filter(program):
    """Build SQL fragment + params for a program filter. `program` is a tuple:

      - 2-tuple (sub_category, gender) — non-AG programs.
      - 3-tuple (sub_category, gender, prog_name) — AG programs where age band
        is encoded in prog_name (e.g. "30-34 Male AG", "30-34 Male AG Sprint").
        prog_name=None means "any prog_name" within (sub, gender).

    A `None` in the gender slot means "any gender" (used by the per-race year
    switcher: IM Worlds alternates gender by venue/year so requiring a gender
    match would hide alternate-gender editions of the same race).
    """
    if not program:
        return "", []
    if len(program) == 3:
        sub, gender, prog_name = program
    else:
        sub, gender = program
        prog_name = None
    parts, params = [], []
    if sub is not None:
        parts.append("r.sub_category = ?"); params.append(sub)
    if gender is not None:
        parts.append("r.gender = ?"); params.append(gender)
    if prog_name is not None:
        parts.append("r.prog_name = ?"); params.append(prog_name)
    if not parts:
        return "", []
    return "AND " + " AND ".join(parts), params


def _ag_race_filter_for_series(series_id):
    """Returns (sql_fragment, params) restricting races to sub_category='ag'
    when the series's tier is 'ag-championship', else ("", []).

    Used by the series-scoped helpers so AG-tier series only surface their
    AG races even when the underlying event also hosts elite races.
    """
    row = _get_conn().execute(
        "SELECT tier FROM series WHERE series_id = ?", [series_id]
    ).fetchone()
    if row and row[0] and row[0].startswith('ag-'):
        return " AND r.sub_category = ?", ['ag']
    return "", []


def _scope_clauses(*, series_id=None, recurring_id=None):
    """Race-set scope for series and recurring-event queries.

    Both scopes select races via a 1-many event link table:
    `event_series.event_id = events.event_id` for series,
    `event_recurring.event_id = events.event_id` for recurring groups.
    Callers splice {table_join} after the FROM-target events row and use
    {where} as the scope filter, with {params} prepended to other params.
    """
    if series_id is not None:
        # AG-tier series (Age-Group World/European Championships) sometimes
        # share an event_id with the elite version (e.g. "Europe Triathlon
        # Championships Vichy" hosts both). Filter races to AG so the
        # series page only surfaces AG programs.
        ag_filter, ag_params = _ag_race_filter_for_series(series_id)
        return {
            "table":  "event_series es",
            "join":   "JOIN races r ON r.event_id = es.event_id",
            "where":  "es.series_id = ?" + ag_filter,
            "params": [series_id] + ag_params,
        }
    if recurring_id is not None:
        return {
            "table":  "event_recurring er",
            "join":   "JOIN races r ON r.event_id = er.event_id",
            "where":  "er.recurring_event_id = ?",
            "params": [recurring_id],
        }
    raise ValueError("_scope_clauses requires series_id or recurring_id")


def get_series_for_race(race_id):
    """Return series dict {series_id, name, slug} for this race, or None.

    Picks the lowest sort_order (primary series) if the event belongs to
    multiple. Looks up via either `races` or `upcoming_races` so the
    breadcrumb on an upcoming race resolves the same way as a finished one.
    """
    conn = _get_conn()
    row = conn.execute("""
        WITH r AS (
            SELECT race_id, event_id FROM races
            UNION ALL
            SELECT race_id, event_id FROM upcoming_races
        )
        SELECT s.series_id, s.name, s.slug
        FROM r
        JOIN event_series es ON es.event_id = r.event_id
        JOIN series s        ON s.series_id = es.series_id
        WHERE r.race_id = ?
        ORDER BY s.sort_order, s.name
        LIMIT 1
    """, [race_id]).fetchone()
    return dict(zip(["series_id", "name", "slug"], row)) if row else None


_SUB_ORDER_CASE = """CASE r.sub_category
    WHEN 'elite'  THEN 0
    WHEN 'u23'    THEN 1
    WHEN 'junior' THEN 2
    WHEN 'youth'  THEN 3
    WHEN 'ag'     THEN 4
    ELSE 5 END"""

_GENDER_ORDER_CASE = "CASE r.gender WHEN 'male' THEN 0 WHEN 'female' THEN 1 ELSE 2 END"


def get_program_options_for_series(series_id):
    """Distinct programs present in a series, ordered for UI tabs.

    For non-AG sub-categories the program is keyed by (sub_category, gender)
    and prog_name is None. AG age-group races are split into one program per
    distinct prog_name (e.g. "30-34 Male AG", "30-34 Male AG Sprint") since
    age bands and sprint variants are meaningfully different.
    """
    conn = _get_conn()
    ag_filter, ag_params = _ag_race_filter_for_series(series_id)
    rows = conn.execute(f"""
        SELECT r.sub_category, r.gender, r.prog_name, COUNT(DISTINCT r.race_id) AS n
        FROM event_series es
        JOIN races r ON r.event_id = es.event_id
        WHERE es.series_id = ?{ag_filter}
        GROUP BY r.sub_category, r.gender, r.prog_name
        ORDER BY {_SUB_ORDER_CASE}, {_GENDER_ORDER_CASE}, r.prog_name
    """, [series_id] + ag_params).fetchall()
    return _collapse_program_rows(rows)


_DIVISION_RE = re.compile(r"\((D\d)\)\s*$")


def program_division(prog_name):
    """Division tag embedded in a prog_name ('Elite Men (D1)' -> 'D1'), else None.

    Lets a series split its programs by division the way AG bands split by
    prog_name — used for the French Grand Prix D1/D2, whose otherwise-identical
    'elite'/gender programs would collapse into one tab (and one very jumpy
    standards graph mixing the two divisions' field strengths)."""
    if not prog_name:
        return None
    m = _DIVISION_RE.search(prog_name)
    return m.group(1) if m else None


def _collapse_program_rows(rows):
    """Take rows of (sub, gender, prog_name, n) and:
      - keep AG rows one-per-prog_name (used for age-band tab strip)
      - keep division-tagged rows ('… (D1)') one-per-prog_name (FGP D1/D2)
      - collapse other non-AG rows to one entry per (sub, gender), summing counts
        (programs like "Elite Men" / "U23 Men" already split via sub_category
        so further per-prog_name splitting would just create duplicates).
    """
    out = []
    nonag_seen = {}
    for sub, gender, prog_name, n in rows:
        if sub == 'ag' or program_division(prog_name):
            out.append({"sub_category": sub, "gender": gender, "prog_name": prog_name, "count": n})
        else:
            key = (sub, gender)
            if key in nonag_seen:
                nonag_seen[key]["count"] += n
            else:
                opt = {"sub_category": sub, "gender": gender, "prog_name": None, "count": n}
                nonag_seen[key] = opt
                out.append(opt)
    return out


def get_all_recurring_events(min_editions=2):
    """All recurring events with edition count and date range.

    Used by the global search modal. Filters to groups with at least
    `min_editions` editions so noise from one-off renames is excluded.
    """
    rows = _get_conn().execute("""
        SELECT re.slug, re.name,
               COUNT(DISTINCT e.event_id) AS edition_count,
               MIN(e.start_date) AS first_date,
               MAX(e.start_date) AS last_date
        FROM recurring_events re
        JOIN event_recurring er ON er.recurring_event_id = re.recurring_event_id
        JOIN events e           ON e.event_id = er.event_id
        GROUP BY re.recurring_event_id, re.slug, re.name
        HAVING edition_count >= ?
        ORDER BY edition_count DESC, last_date DESC
    """, [min_editions]).fetchall()
    return [{
        "slug": r[0], "name": r[1], "edition_count": r[2],
        "first_year": r[3].year if r[3] else None,
        "last_year":  r[4].year if r[4] else None,
    } for r in rows]


def get_recurring_event_for_event(event_id):
    """Return the recurring_event row this event belongs to, or None."""
    row = _get_conn().execute("""
        SELECT re.recurring_event_id, re.slug, re.name, re.venue_key, re.description
        FROM event_recurring er
        JOIN recurring_events re ON re.recurring_event_id = er.recurring_event_id
        WHERE er.event_id = ?
    """, [event_id]).fetchone()
    if not row:
        return None
    cols = ["recurring_event_id", "slug", "name", "venue_key", "description"]
    return dict(zip(cols, row))


def get_other_editions_for_event(event_id, program=None, relay=False):
    """Races in the same recurring group as event_id (excluding event_id's own race(s)).

    `relay` keeps the two race types apart within a shared recurring group:
    individual race pages exclude relay editions, relay pages show only relay.
    """
    prog_sql, prog_params = _program_filter(program)
    dist_sql = "AND r.distance = 'relay'" if relay else "AND r.distance <> 'relay'"
    conn = _get_conn()
    cols = ["race_id", "race_date", "race_handle", "prog_name", "gender",
            "event_id", "event_name", "venue", "country"]
    return _dicts(cols, conn.execute(f"""
        SELECT r.race_id, r.race_date, r.race_handle, r.prog_name, r.gender,
               e.event_id, e.name AS event_name, e.venue, e.country
        FROM events ref
        JOIN event_recurring er_ref ON er_ref.event_id = ref.event_id
        JOIN event_recurring er     ON er.recurring_event_id = er_ref.recurring_event_id
        JOIN events e               ON e.event_id = er.event_id
        JOIN races r                ON r.event_id = e.event_id
        WHERE ref.event_id = ?
          AND e.event_id <> ref.event_id
          {dist_sql}
          {prog_sql}
        ORDER BY r.race_date DESC
    """, [event_id] + prog_params))


def get_year_options_for_recurring(recurring_event_id, gender, sub_category, relay=False):
    """One row per year of the recurring event for the breadcrumb dropdown.

    Picks the race in each year matching (sub_category, gender). Falls back
    to the same sub_category in the other gender when an exact match doesn't
    exist (mirrors get_other_editions_for_event behaviour for venues like
    Ironman Worlds that alternate gender).

    `relay` scopes to the right race type: a mixed-relay breadcrumb lists only
    relay editions (and shows the winning country), an individual-race
    breadcrumb excludes relay editions of the same venue/sub_category.
    """
    # Relay and individual races share sub_category='elite' within a recurring
    # group, so distance is what separates the two year-picker tracks.
    dist_sql = "r.distance = 'relay'" if relay else "r.distance <> 'relay'"
    rows = _get_conn().execute(f"""
        WITH year_race AS (
            SELECT
                EXTRACT(YEAR FROM r.race_date)::int AS year,
                r.race_id, r.race_handle, r.race_date,
                r.gender, r.sub_category,
                ROW_NUMBER() OVER (
                    PARTITION BY EXTRACT(YEAR FROM r.race_date)
                    ORDER BY (CASE WHEN r.gender = ? THEN 0 ELSE 1 END), r.race_date DESC
                ) AS rn
            FROM event_recurring er
            JOIN races r ON r.event_id = er.event_id
            WHERE er.recurring_event_id = ?
              AND r.sub_category = ?
              AND {dist_sql}
              AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
        )
        SELECT yr.year, yr.race_id, yr.race_handle, yr.race_date, yr.gender,
               COALESCE(a.name, rt.country_full)   AS winner_name,
               COALESCE(n.alpha3, rn.alpha3)        AS winner_country_alpha3,
               COALESCE(res.overall_s, rt.total_s)  AS overall_s
        FROM year_race yr
        LEFT JOIN results res
               ON res.race_id = yr.race_id AND res.position = 1 AND res.status = 'Finished'
        LEFT JOIN athletes a       ON a.athlete_id = res.athlete_id
        LEFT JOIN nationalities n  ON n.country_full = a.country_full
        LEFT JOIN relay_teams rt
               ON rt.race_id = yr.race_id AND rt.position = 1 AND rt.status = 'Finished'
        LEFT JOIN nationalities rn ON rn.country_full = rt.country_full
        WHERE yr.rn = 1
        ORDER BY yr.year DESC
    """, [gender, recurring_event_id, sub_category]).fetchall()
    cols = ["year", "race_id", "race_handle", "race_date", "gender",
            "winner_name", "winner_country_alpha3", "overall_s"]
    return [dict(zip(cols, r)) for r in rows]


def _scoped_races(scope, program=None):
    """All races in scope newest-first, each with top-3 podium and race standard."""
    from collections import defaultdict
    conn = _get_conn()

    prog_sql, prog_params = _program_filter(program)
    race_rows = conn.execute(f"""
        SELECT r.race_id, r.race_title, r.race_handle, r.race_date, r.prog_name, r.gender, r.sub_category,
               e.name AS event_name, e.venue, e.country, e.latitude, e.longitude,
               r.is_multi_stage
        FROM {scope['table']}
        {scope['join']}
        JOIN events e ON e.event_id = r.event_id
        WHERE {scope['where']}
          AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
          {prog_sql}
        ORDER BY r.race_date DESC
    """, scope['params'] + prog_params).fetchall()
    race_cols = ["race_id", "race_title", "race_handle", "race_date", "prog_name", "gender", "sub_category",
                 "event_name", "venue", "country", "latitude", "longitude",
                 "is_multi_stage"]
    races = [dict(zip(race_cols, r)) for r in race_rows]
    if not races:
        return races

    race_ids = [r["race_id"] for r in races]
    id_ph = ','.join(['?'] * len(race_ids))

    podium_rows = conn.execute(f"""
        SELECT res.race_id, res.position, res.overall_s,
               res.swim_s, res.bike_s, res.run_s, res.t1_s, res.t2_s,
               a.athlete_id, a.name, n.alpha3, a.profile_img
        FROM results res
        JOIN athletes a ON a.athlete_id = res.athlete_id
        JOIN nationalities n ON n.country_full = a.country_full
        WHERE res.race_id IN ({id_ph})
          AND res.position IN (1, 2, 3)
          AND res.status = 'Finished'
        ORDER BY res.race_id, res.position
    """, race_ids).fetchall()

    podiums = defaultdict(list)
    for race_id, pos, overall_s, swim_s, bike_s, run_s, t1_s, t2_s, athlete_id, name, alpha3, profile_img in podium_rows:
        podiums[race_id].append({
            "position": pos, "overall_s": overall_s,
            "swim_s": swim_s, "bike_s": bike_s, "run_s": run_s,
            "t1_s": t1_s, "t2_s": t2_s,
            "athlete_id": athlete_id, "name": name,
            "country_alpha3": alpha3, "profile_img": profile_img,
        })

    std_rows = conn.execute(f"""
        SELECT race_id, overall_std, swim_std, bike_std, run_std, transition_std
        FROM race_rankings
        WHERE race_id IN ({id_ph})
    """, race_ids).fetchall()
    std_by_race = {
        r[0]: {"overall": r[1], "swim": r[2], "bike": r[3], "run": r[4], "transition": r[5]}
        for r in std_rows
    }

    for race in races:
        podium = podiums[race["race_id"]]
        winner_s = podium[0]["overall_s"] if podium else None
        for p in podium:
            p["gap"] = (p["overall_s"] - winner_s) if winner_s and p["position"] != 1 else None
        race["podium"] = podium
        race["standards_raw"] = std_by_race.get(race["race_id"])

    return races


def get_series_races(series_id, program=None):
    return _scoped_races(_scope_clauses(series_id=series_id), program=program)


def get_recurring_races(recurring_id, program=None):
    return _scoped_races(_scope_clauses(recurring_id=recurring_id), program=program)


def _scoped_all_time_leaders(scope, program=None):
    """Athletes ranked by wins within scope, with 2nd/3rd counts and edition list."""
    conn = _get_conn()
    prog_sql, prog_params = _program_filter(program)
    rows = conn.execute(f"""
        SELECT a.athlete_id, a.name, n.alpha3, a.profile_img,
               COUNT(CASE WHEN res.position = 1 THEN 1 END) AS wins,
               COUNT(CASE WHEN res.position = 2 THEN 1 END) AS seconds,
               COUNT(CASE WHEN res.position = 3 THEN 1 END) AS thirds,
               MAX(CASE WHEN res.position = 1 THEN r.race_date END) AS latest_win
        FROM {scope['table']}
        {scope['join']}
        JOIN results res ON res.race_id = r.race_id
        JOIN athletes a  ON a.athlete_id = res.athlete_id
        JOIN nationalities n ON n.country_full = a.country_full
        WHERE {scope['where']}
          AND res.position IN (1, 2, 3)
          AND res.status = 'Finished'
          AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
          {prog_sql}
        GROUP BY a.athlete_id, a.name, n.alpha3, a.profile_img
        HAVING wins > 0
        ORDER BY wins DESC, seconds DESC, thirds DESC, latest_win DESC
    """, scope['params'] + prog_params).fetchall()

    leaders = [
        {"athlete_id": r[0], "name": r[1], "country_alpha3": r[2], "profile_img": r[3],
         "wins": r[4], "seconds": r[5], "thirds": r[6]}
        for r in rows  # r[7] is latest_win date, used for ordering only
    ]
    if not leaders:
        return leaders

    # Fetch all podium results with short race name, most recent first
    athlete_ids = [l["athlete_id"] for l in leaders]
    ph = ','.join(['?'] * len(athlete_ids))
    podium_rows = conn.execute(f"""
        SELECT res.athlete_id, res.position, res.race_id, r.race_date, r.race_handle
        FROM {scope['table']}
        {scope['join']}
        JOIN results res ON res.race_id = r.race_id
        WHERE {scope['where']}
          AND res.status = 'Finished'
          AND res.position IN (1, 2, 3)
          AND res.athlete_id IN ({ph})
          AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
          {prog_sql}
        ORDER BY r.race_date DESC
    """, scope['params'] + athlete_ids + prog_params).fetchall()

    podiums_by_athlete: dict[int, list] = {}
    for athlete_id, position, race_id, race_date, race_handle in podium_rows:
        podiums_by_athlete.setdefault(athlete_id, []).append(
            {"position": position, "race_id": race_id, "short": race_handle,
             "year": race_date.year if race_date else None}
        )

    for l in leaders:
        pds = podiums_by_athlete.get(l["athlete_id"], [])
        l["podiums"] = pds
        l["win_editions"] = [p for p in pds if p["position"] == 1]

    return leaders


def get_series_all_time_leaders(series_id, program=None):
    return _scoped_all_time_leaders(_scope_clauses(series_id=series_id), program=program)


def get_recurring_all_time_leaders(recurring_id, program=None):
    return _scoped_all_time_leaders(_scope_clauses(recurring_id=recurring_id), program=program)


def _scoped_medal_table(scope, program=None):
    """Per-country gold/silver/bronze counts within scope.

    Returns rows ordered by gold DESC, silver DESC, bronze DESC. Each row
    has the country's flag/alpha3 so the UI can link to the country page.
    Same ignore-rules as the rest of the scoped queries: ignored_races are
    skipped; podium positions 1/2/3 only with status='Finished'.
    """
    conn = _get_conn()
    prog_sql, prog_params = _program_filter(program)
    cols = ["country_full", "country_alpha3",
            "gold", "silver", "bronze"]
    return _dicts(cols, conn.execute(f"""
        SELECT a.country_full,
               n.alpha3,
               COUNT(CASE WHEN res.position = 1 THEN 1 END) AS gold,
               COUNT(CASE WHEN res.position = 2 THEN 1 END) AS silver,
               COUNT(CASE WHEN res.position = 3 THEN 1 END) AS bronze
        FROM {scope['table']}
        {scope['join']}
        JOIN results res     ON res.race_id = r.race_id
        JOIN athletes a      ON a.athlete_id = res.athlete_id
        JOIN nationalities n ON n.country_full = a.country_full
        WHERE {scope['where']}
          AND res.position IN (1, 2, 3)
          AND res.status = 'Finished'
          AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
          {prog_sql}
        GROUP BY a.country_full, n.alpha3
        ORDER BY gold DESC, silver DESC, bronze DESC, a.country_full
    """, scope['params'] + prog_params))


def get_series_medal_table(series_id, program=None):
    return _scoped_medal_table(_scope_clauses(series_id=series_id), program=program)


def get_recurring_medal_table(recurring_id, program=None):
    return _scoped_medal_table(_scope_clauses(recurring_id=recurring_id), program=program)


def _scoped_performance_history(scope, program=None):
    """Per-race winner/10th/25th overall + split times for the performance charts.
    Uses auto-corrected split times."""
    conn = _get_conn()
    prog_sql, prog_params = _program_filter(program)
    cols = ["race_id", "race_date", "event_name",
            "winner_s", "p10_s", "p25_s",
            "winner_swim", "p10_swim", "p25_swim",
            "winner_bike", "p10_bike", "p25_bike",
            "winner_run",  "p10_run",  "p25_run",
            "winner_t1",   "winner_t2"]
    return _dicts(cols, conn.execute(f"""
        WITH corr AS (
            SELECT race_id, athlete_id, discipline,
                   MAX(value) FILTER (WHERE source='auto') AS value
            FROM corrections
            GROUP BY race_id, athlete_id, discipline
        ),
        corr_wide AS (
            SELECT race_id, athlete_id,
                   MAX(value) FILTER (WHERE discipline='overall') AS overall,
                   MAX(value) FILTER (WHERE discipline='swim')    AS swim,
                   MAX(value) FILTER (WHERE discipline='bike')    AS bike,
                   MAX(value) FILTER (WHERE discipline='run')     AS run,
                   MAX(value) FILTER (WHERE discipline='t1')      AS t1,
                   MAX(value) FILTER (WHERE discipline='t2')      AS t2
            FROM corr GROUP BY race_id, athlete_id
        ),
        ranked AS (
            SELECT r.race_id, r.race_date, e.name AS event_name,
                   COALESCE(cw.overall, res.overall_s) AS overall_s,
                   COALESCE(cw.swim,    res.swim_s)    AS swim_s,
                   COALESCE(cw.bike,    res.bike_s)    AS bike_s,
                   COALESCE(cw.run,     res.run_s)     AS run_s,
                   COALESCE(cw.t1,      res.t1_s)      AS t1_s,
                   COALESCE(cw.t2,      res.t2_s)      AS t2_s,
                   ROW_NUMBER() OVER (PARTITION BY r.race_id ORDER BY COALESCE(cw.overall, res.overall_s) ASC) AS pos_rank,
                   ROW_NUMBER() OVER (PARTITION BY r.race_id ORDER BY NULLIF(COALESCE(cw.swim, res.swim_s), 0) ASC NULLS LAST) AS swim_rank,
                   ROW_NUMBER() OVER (PARTITION BY r.race_id ORDER BY NULLIF(COALESCE(cw.bike, res.bike_s), 0) ASC NULLS LAST) AS bike_rank,
                   ROW_NUMBER() OVER (PARTITION BY r.race_id ORDER BY NULLIF(COALESCE(cw.run,  res.run_s ), 0) ASC NULLS LAST) AS run_rank,
                   ROW_NUMBER() OVER (PARTITION BY r.race_id ORDER BY NULLIF(COALESCE(cw.t1,   res.t1_s ),  0) ASC NULLS LAST) AS t1_rank,
                   ROW_NUMBER() OVER (PARTITION BY r.race_id ORDER BY NULLIF(COALESCE(cw.t2,   res.t2_s ),  0) ASC NULLS LAST) AS t2_rank
            FROM {scope['table']}
            {scope['join']}
            JOIN events e    ON e.event_id = r.event_id
            JOIN results res ON res.race_id = r.race_id
            LEFT JOIN corr_wide cw ON cw.race_id = res.race_id AND cw.athlete_id = res.athlete_id
            WHERE {scope['where']}
              AND res.status = 'Finished'
              AND COALESCE(cw.overall, res.overall_s) > 0
              AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
              {prog_sql}
        )
        SELECT race_id, race_date, MAX(event_name) AS event_name,
               MAX(CASE WHEN pos_rank  = 1  THEN overall_s END) AS winner_s,
               MAX(CASE WHEN pos_rank  = 10 THEN overall_s END) AS p10_s,
               MAX(CASE WHEN pos_rank  = 25 THEN overall_s END) AS p25_s,
               MAX(CASE WHEN swim_rank = 1  AND swim_s > 0 THEN swim_s END) AS winner_swim,
               MAX(CASE WHEN swim_rank = 10 AND swim_s > 0 THEN swim_s END) AS p10_swim,
               MAX(CASE WHEN swim_rank = 25 AND swim_s > 0 THEN swim_s END) AS p25_swim,
               MAX(CASE WHEN bike_rank = 1  AND bike_s > 0 THEN bike_s END) AS winner_bike,
               MAX(CASE WHEN bike_rank = 10 AND bike_s > 0 THEN bike_s END) AS p10_bike,
               MAX(CASE WHEN bike_rank = 25 AND bike_s > 0 THEN bike_s END) AS p25_bike,
               MAX(CASE WHEN run_rank  = 1  AND run_s  > 0 THEN run_s  END) AS winner_run,
               MAX(CASE WHEN run_rank  = 10 AND run_s  > 0 THEN run_s  END) AS p10_run,
               MAX(CASE WHEN run_rank  = 25 AND run_s  > 0 THEN run_s  END) AS p25_run,
               MAX(CASE WHEN t1_rank   = 1  AND t1_s   > 0 THEN t1_s   END) AS winner_t1,
               MAX(CASE WHEN t2_rank   = 1  AND t2_s   > 0 THEN t2_s   END) AS winner_t2
        FROM ranked
        GROUP BY race_id, race_date
        ORDER BY race_date
    """, scope['params'] + prog_params))


def get_series_performance_history(series_id, program=None):
    return _scoped_performance_history(_scope_clauses(series_id=series_id), program=program)


def get_recurring_performance_history(recurring_id, program=None):
    return _scoped_performance_history(_scope_clauses(recurring_id=recurring_id), program=program)


def _scoped_standards_history(scope, program=None):
    """Per-race standards across the scope, optionally filtered by program.

    Reads the pre-computed standards directly from race_rankings; the
    formula is owned by ratings._compute_race_rankings, this is just a
    lookup keyed by race.
    """
    conn = _get_conn()
    prog_sql, prog_params = _program_filter(program)
    cols = ["race_id", "race_date", "race_title", "sub_category", "gender", "prog_name",
            "overall_std", "swim_std", "bike_std", "run_std"]
    return _dicts(cols, conn.execute(f"""
        SELECT r.race_id, r.race_date, r.race_title, r.sub_category, r.gender, r.prog_name,
               rr.overall_std, rr.swim_std, rr.bike_std, rr.run_std
        FROM {scope['table']}
        {scope['join']}
        JOIN race_rankings rr ON rr.race_id = r.race_id
        WHERE {scope['where']}
          AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
          {prog_sql}
        ORDER BY r.race_date
    """, scope['params'] + prog_params))


def get_series_standards_history(series_id, program=None):
    return _scoped_standards_history(_scope_clauses(series_id=series_id), program=program)


def get_recurring_standards_history(recurring_id, program=None):
    return _scoped_standards_history(_scope_clauses(recurring_id=recurring_id), program=program)



def _scoped_winners_with_age(scope, program=None):
    """All winners in scope with their age at the time of the win.

    Returns one row per winning result: athlete + race + age (race_date.year
    minus year_of_birth, integer years). Athletes with year_of_birth = 0
    (unknown) are excluded since their age is undefined. Caller can sort to
    surface oldest / youngest.
    """
    conn = _get_conn()
    prog_sql, prog_params = _program_filter(program)
    rows = conn.execute(f"""
        SELECT a.athlete_id, a.name, n.alpha3, a.profile_img,
               res.race_id, r.race_date, r.race_handle,
               EXTRACT(YEAR FROM r.race_date) - a.year_of_birth AS age
        FROM {scope['table']}
        {scope['join']}
        JOIN results res     ON res.race_id = r.race_id
        JOIN athletes a      ON a.athlete_id = res.athlete_id
        JOIN nationalities n ON n.country_full = a.country_full
        WHERE {scope['where']}
          AND res.position = 1
          AND res.status = 'Finished'
          AND a.year_of_birth > 0
          AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
          {prog_sql}
        ORDER BY r.race_date DESC
    """, scope['params'] + prog_params).fetchall()
    out = []
    for athlete_id, name, alpha3, profile_img, race_id, race_date, race_handle, age in rows:
        out.append({
            "athlete_id":    athlete_id,
            "name":          name,
            "country_alpha3": alpha3,
            "profile_img":   profile_img,
            "race_id":       race_id,
            "race_date":     race_date,
            "edition_short": race_handle,
            "age":           int(age),
        })
    return out


def get_series_winners_with_age(series_id, program=None):
    return _scoped_winners_with_age(_scope_clauses(series_id=series_id), program=program)


def get_recurring_winners_with_age(recurring_id, program=None):
    return _scoped_winners_with_age(_scope_clauses(recurring_id=recurring_id), program=program)


def get_recurring_event_by_slug(slug):
    """Recurring event metadata + edition count + year span."""
    row = _get_conn().execute("""
        SELECT re.recurring_event_id, re.slug, re.name, re.venue_key, re.description,
               COUNT(DISTINCT er.event_id) AS edition_count,
               MIN(e.start_date) AS first_date,
               MAX(e.start_date) AS last_date
        FROM recurring_events re
        LEFT JOIN event_recurring er ON er.recurring_event_id = re.recurring_event_id
        LEFT JOIN events e           ON e.event_id = er.event_id
        WHERE re.slug = ?
        GROUP BY re.recurring_event_id, re.slug, re.name, re.venue_key, re.description
    """, [slug]).fetchone()
    if not row:
        return None
    cols = ["recurring_event_id", "slug", "name", "venue_key", "description",
            "edition_count", "first_date", "last_date"]
    return dict(zip(cols, row))


def get_program_options_for_recurring(recurring_id):
    """Distinct programs present in a recurring group, ordered for UI tabs.
    See `get_program_options_for_series` for the AG-aware shape."""
    rows = _get_conn().execute(f"""
        SELECT r.sub_category, r.gender, r.prog_name, COUNT(DISTINCT r.race_id) AS n
        FROM event_recurring er
        JOIN races r ON r.event_id = er.event_id
        WHERE er.recurring_event_id = ?
        GROUP BY r.sub_category, r.gender, r.prog_name
        ORDER BY {_SUB_ORDER_CASE}, {_GENDER_ORDER_CASE}, r.prog_name
    """, [recurring_id]).fetchall()
    return _collapse_program_rows(rows)
