"""
Read-only query functions against the PTD DuckDB database.

All functions return plain dicts/lists - no custom objects, no DataFrames.
Formatting stays in the routers.
"""

import re
from ast import literal_eval

from ptd_data import db

# Module-level read-only connection, opened on first use
_conn = None

_STRIP_WORDS_RE = re.compile(
    r'\b(?:world|triathlon|cup|championship|series|olympic|paralympic|games|'
    r'continental|super|sprint|wtcs|itu|ironman|t100|challenge|'
    r'elite|men|women|mixed|relay)\b',
    re.I,
)


def _location_from_name(event_name: str) -> str:
    """Extract the location from a full event name when no venue is stored.

    "2026 World Triathlon Cup Haikou" → "Haikou"
    Falls back to the original name if nothing useful remains.
    """
    s = re.sub(r'^\d{4}\s*', '', event_name.strip())
    s = _STRIP_WORDS_RE.sub('', s)
    s = ' '.join(s.split())
    return s or event_name.strip()


def _get_conn():
    global _conn
    if _conn is None:
        _conn = db.get_conn(read_only=True)
    return _conn


# Course → distance-enum values. Used to scope rating/ranking queries so
# that short-course and long-course ratings (which live in the same table
# but are computed independently) don't leak into each other.
COURSE_DISTANCES = {
    'short': ('sprint', 'standard'),
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

def search_athletes(query, gender=None, course='short'):
    """
    Substring search by name (case-insensitive).
    Returns list of dicts ordered by current overall rating desc in the given course.
    Optionally filtered by gender ('male' or 'female').
    """
    conn = _get_conn()
    course_in = _course_in(course)
    params = [f"%{query}%"]
    gender_clause = ""
    if gender:
        gender_clause = " AND a.gender = ?"
        params.append(gender)
    rows = conn.execute(f"""
        SELECT
            a.athlete_id,
            a.name,
            a.year_of_birth,
            a.gender,
            n.alpha3   AS country_alpha3,
            n.emoji    AS country_emoji,
            a.country_full,
            COALESCE(latest.overall, 0) AS rating
        FROM athletes a
        JOIN nationalities n ON a.country_full = n.country_full
        LEFT JOIN (
            SELECT ra.athlete_id, ra.overall
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE r.distance IN {course_in}
            QUALIFY ROW_NUMBER() OVER (PARTITION BY ra.athlete_id
                                       ORDER BY r.race_date DESC, ra.race_id DESC) = 1
        ) latest ON a.athlete_id = latest.athlete_id
        WHERE a.name ILIKE ?{gender_clause}
        ORDER BY rating DESC
        LIMIT 50
    """, params).fetchall()

    cols = ["athlete_id", "name", "year_of_birth", "gender", "country_alpha3",
            "country_emoji", "country_full", "rating"]
    return [dict(zip(cols, r)) for r in rows]


def search_athletes_full(query, disc="overall", order="top", country=None,
                         yob_start=None, yob_end=None, active_only=False, limit=10,
                         course='short'):
    """
    Name search with filter/sort options for the athletes landing page.
    Returns leaderboard-style dicts with ratings and 1yr disc change. Course-scoped.
    """
    assert disc in _VALID_DISCS and order in _VALID_ORDERS
    conn = _get_conn()
    course_in = _course_in(course)

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

    rows = conn.execute(f"""
        WITH current AS (
            SELECT DISTINCT ON (ra.athlete_id)
                   ra.athlete_id,
                   ra.overall, ra.swim, ra.bike, ra.run, ra.transition,
                   r.race_date AS last_race_date
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE r.distance IN {course_in}
            ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
        ),
        year_ago AS (
            SELECT DISTINCT ON (ra.athlete_id)
                   ra.athlete_id,
                   ra.overall, ra.swim, ra.bike, ra.run, ra.transition
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE r.race_date <= CURRENT_DATE - INTERVAL 1 YEAR AND r.distance IN {course_in}
            ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
        ),
        athlete_stats AS (
            SELECT athlete_id,
                   COUNT(*) AS race_starts,
                   COUNT(CASE WHEN position = 1 THEN 1 END) AS wins
            FROM results
            GROUP BY athlete_id
        )
        SELECT
            a.athlete_id,
            a.name,
            a.year_of_birth,
            a.gender,
            n.alpha3    AS country_alpha3,
            n.emoji     AS country_emoji,
            a.country_full,
            a.profile_img,
            c.overall, c.swim, c.bike, c.run, c.transition,
            COALESCE(s.race_starts, 0) AS race_starts,
            COALESCE(s.wins, 0)        AS wins
        FROM athletes a
        JOIN nationalities n ON a.country_full = n.country_full
        JOIN current c       ON a.athlete_id = c.athlete_id
        LEFT JOIN year_ago ya ON a.athlete_id = ya.athlete_id
        LEFT JOIN athlete_stats s ON a.athlete_id = s.athlete_id
        WHERE {where}
        ORDER BY {order_clause}
        LIMIT ?
    """, params + [limit]).fetchall()

    cols = ["athlete_id", "name", "year_of_birth", "gender", "country_alpha3",
            "country_emoji", "country_full", "profile_img",
            "overall_rating", "swim_rating", "bike_rating", "run_rating", "transition_rating",
            "race_starts", "wins"]
    return [dict(zip(cols, r)) for r in rows]


def get_podium(gender, category='elite', course='short'):
    """
    Top 3 athletes by current overall rating for a given gender, category, and course.
    Returns list of dicts.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    rows = conn.execute(f"""
        SELECT
            a.athlete_id,
            a.name,
            n.alpha3   AS country_alpha3,
            n.emoji    AS country_emoji,
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
    """, [category, category, gender]).fetchall()

    cols = ["athlete_id", "name", "country_alpha3", "country_emoji",
            "country_full", "year_of_birth", "profile_img", "overall", "overall_rank"]
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Race search
# ---------------------------------------------------------------------------

def _fmt_time(seconds):
    if not seconds or seconds <= 0:
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
        ORDER BY race_date ASC, race_id ASC
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
            SELECT res.race_id, res.position, a.athlete_id, a.name, n.emoji, res.overall_s, a.profile_img
            FROM results res
            JOIN athletes a ON res.athlete_id = a.athlete_id
            JOIN nationalities n ON a.country_full = n.country_full
            WHERE res.race_id IN ({ph})
              AND res.position IN (1, 2, 3)
              AND res.status = 'Finished'
            ORDER BY res.race_id, res.position
        """, podium_race_ids).fetchall()

        podium_by_race = {}
        for race_id, position, athlete_id, name, emoji, overall_s, profile_img in podium_rows:
            podium_by_race.setdefault(race_id, []).append(
                {"position": position, "athlete_id": athlete_id, "name": name,
                 "emoji": emoji, "overall_s": overall_s, "profile_img": profile_img}
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
        SELECT event_id, race_id, prog_name
        FROM races
        WHERE event_id IN ({placeholders})
        ORDER BY race_date ASC, race_id ASC
    """, event_ids).fetchall()
    for event_id, race_id, prog_name in race_rows:
        event_map[event_id]["races"].append({"race_id": race_id, "prog_name": prog_name})

    return [event_map[r[0]] for r in event_rows]


def search_events(query, limit=20):
    """Search events by name / venue / country. Returns up to `limit` most recent matches with constituent races."""
    conn = _get_conn()
    q = f"%{query}%"
    event_rows = conn.execute("""
        SELECT event_id, name, venue, country, start_date
        FROM events e
        WHERE (name ILIKE ? OR venue ILIKE ? OR country ILIKE ?)
          AND EXISTS (SELECT 1 FROM races r WHERE r.event_id = e.event_id)
        ORDER BY start_date DESC
        LIMIT ?
    """, [q, q, q, limit]).fetchall()
    if not event_rows:
        return []

    event_ids = [r[0] for r in event_rows]
    event_map = {r[0]: {
        "event_id": r[0], "name": r[1], "venue": r[2],
        "country": r[3], "start_date": r[4], "races": []
    } for r in event_rows}

    placeholders = ",".join("?" * len(event_ids))
    race_rows = conn.execute(f"""
        SELECT event_id, race_id, prog_name
        FROM races
        WHERE event_id IN ({placeholders})
        ORDER BY race_date ASC, race_id ASC
    """, event_ids).fetchall()
    for event_id, race_id, prog_name in race_rows:
        event_map[event_id]["races"].append({"race_id": race_id, "prog_name": prog_name})

    return [event_map[r[0]] for r in event_rows]


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

_VALID_DISCS = {"overall", "swim", "bike", "run", "transition"}
_VALID_ORDERS = {"top", "hot"}


def get_leaderboard(gender, disc, order, country, yob_start, yob_end,
                    active_only, offset, limit=100, category='elite', course='short'):
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

    if country and country != "all":
        filters.append("a.country_full = ?")
        params.append(country)
    if yob_start:
        filters.append("a.year_of_birth >= ?")
        params.append(yob_start)
    if yob_end:
        filters.append("a.year_of_birth <= ?")
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
            n.emoji     AS country_emoji,
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

    rows = conn.execute(sql, params).fetchall()
    cols = [
        "athlete_id", "name", "year_of_birth", "country_alpha3", "country_emoji", "country_full",
        "profile_img",
        "overall_rating", "swim_rating", "bike_rating", "run_rating", "transition_rating",
        "world_overall", "world_swim", "world_bike", "world_run", "world_transition",
        "active_world_overall", "active_world_swim", "active_world_bike", "active_world_run", "active_world_transition",
        "active", "race_starts", "wins",
        "overall_change", "swim_change", "bike_change", "run_change", "transition_change",
    ]
    return [dict(zip(cols, r)) for r in rows]


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
_CHAMPIONSHIP_TIERS = ["olympic-games", "commonwealth-games", "wtcs", "championship"]
_CHAMPIONSHIP_LABELS = {
    "olympic-games":      "Olympic Games",
    "commonwealth-games": "Commonwealth Games",
    "wtcs":               "WTCS",
    "championship":       "World Championships",
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
    # Asia
    "Republic of Korea": "Asia", "Iraq": "Asia", "Kuwait": "Asia",
    "Syria": "Asia", "Lebanon": "Asia", "Myanmar (Burma)": "Asia",
    "Cambodia": "Asia", "Bangladesh": "Asia", "Saudi Arabia": "Asia",
    "Vietnam": "Asia", "Palestine": "Asia", "Maldives": "Asia",
    "Pakistan": "Asia", "Yemen": "Asia",
    "Democratic People's Republic of Korea": "Asia",
    # Americas
    "Trinidad and Tobago": "Americas", "Paraguay": "Americas",
    "Saint Kitts and Nevis": "Americas", "Belize": "Americas",
    "Netherlands Antilles": "Americas", "Antigua and Barbuda": "Americas",
    "Suriname": "Americas", "Dominica": "Americas", "Guyana": "Americas",
    # Africa
    "Libya": "Africa", "Nigeria": "Africa", "Cote d'Ivoire": "Africa",
    "Niger": "Africa", "Seychelles": "Africa", "Sudan": "Africa",
    "Djibouti": "Africa", "Burundi": "Africa", "Cameroon": "Africa",
    "Comoros": "Africa", "Sierra Leone": "Africa", "Swaziland": "Africa",
    "Uganda": "Africa",
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
    """Return {country_full, alpha3, emoji, athlete_count, race_host_count} or None."""
    conn = _get_conn()
    row = conn.execute("""
        SELECT n.country_full, n.alpha3, n.emoji,
               (SELECT COUNT(*) FROM athletes a WHERE a.country_full = n.country_full) AS athlete_count,
               (SELECT COUNT(*) FROM events e    WHERE e.country     = n.country_full) AS race_host_count
        FROM nationalities n
        WHERE n.alpha3 = ?
    """, [alpha3]).fetchone()
    if not row:
        return None
    cols = ["country_full", "alpha3", "emoji", "athlete_count", "race_host_count"]
    return dict(zip(cols, row))


def get_countries_with_counts(course='short'):
    """Countries with athlete and race-host counts plus modal event-continent.

    One row per row in `nationalities`. Countries with zero athletes are included.
    Continent is taken from the most common `events.continent` for races hosted
    in that country; countries that host no events fall back to 'Other'.
    Top-athlete headline rating is scoped to the given course.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    rows = conn.execute(f"""
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
            -- modal continent per country
            SELECT country, continent
            FROM (
                SELECT country, continent, COUNT(*) AS n,
                       ROW_NUMBER() OVER (PARTITION BY country ORDER BY COUNT(*) DESC) AS rn
                FROM events
                GROUP BY country, continent
            )
            WHERE rn = 1
        ),
        topath AS (
            -- highest-rated (elite) athlete per country for a headline card
            SELECT country_full, athlete_id, name, profile_img, overall
            FROM (
                SELECT a.country_full, a.athlete_id, a.name, a.profile_img,
                       r.overall,
                       ROW_NUMBER() OVER (PARTITION BY a.country_full ORDER BY r.overall DESC) AS rn
                FROM athletes a
                JOIN (
                    SELECT DISTINCT ON (ra.athlete_id)
                           ra.athlete_id, ra.overall
                    FROM ratings ra
                    JOIN races rc ON rc.race_id = ra.race_id
                    WHERE ra.category = 'elite' AND rc.distance IN {course_in}
                    ORDER BY ra.athlete_id, rc.race_date DESC, ra.race_id DESC
                ) r ON r.athlete_id = a.athlete_id
            )
            WHERE rn = 1
        )
        SELECT n.country_full, n.alpha3, n.emoji,
               COALESCE(ath.athlete_count, 0),
               COALESCE(ev.race_host_count, 0),
               COALESCE(cont.continent, 'Other'),
               topath.athlete_id, topath.name, topath.profile_img, topath.overall
        FROM nationalities n
        LEFT JOIN ath    ON ath.country_full    = n.country_full
        LEFT JOIN ev     ON ev.country          = n.country_full
        LEFT JOIN cont   ON cont.country        = n.country_full
        LEFT JOIN topath ON topath.country_full = n.country_full
        ORDER BY COALESCE(ath.athlete_count, 0) DESC, n.country_full
    """).fetchall()
    cols = ["country_full", "alpha3", "emoji",
            "athlete_count", "race_host_count", "continent",
            "top_athlete_id", "top_athlete_name", "top_athlete_img", "top_athlete_rating"]
    out = [dict(zip(cols, r)) for r in rows]
    # Manual override for countries that host no events (or whose event-derived
    # modal continent is 'Other'). Final fallback remains 'Other'.
    for d in out:
        if d["continent"] in ("", "Other"):
            d["continent"] = _MANUAL_CONTINENTS.get(d["country_full"], "Other")
    return out


def get_country_leaderboard(country_full, gender, discipline="overall",
                            limit=20, active_only=True, category="elite", course='short'):
    """Top athletes from a country in the given discipline + gender.

    Thin wrapper over `get_leaderboard` that pre-sets `country`, `order='top'`,
    and no year-of-birth filters.
    """
    assert discipline in _VALID_DISCS
    return get_leaderboard(
        gender=gender, disc=discipline, order="top",
        country=country_full, yob_start=None, yob_end=None,
        active_only=active_only, offset=0, limit=limit, category=category, course=course,
    )


def get_country_hosted_race_locations(country_full):
    """Events hosted in this country with coords. For the country page map."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT event_id, name, venue, latitude, longitude, start_date
        FROM events
        WHERE country = ?
          AND latitude <> 0 AND longitude <> 0
        ORDER BY start_date DESC
    """, [country_full]).fetchall()
    cols = ["event_id", "event_name", "venue", "latitude", "longitude", "start_date"]
    return [dict(zip(cols, r)) for r in rows]


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

def get_athlete_info(athlete_id):
    """Basic athlete info: name, country, yob, gender, profile_img."""
    conn = _get_conn()
    row = conn.execute("""
        SELECT a.athlete_id, a.name, a.country_full, a.year_of_birth,
               a.gender, a.profile_img,
               n.alpha3 AS country_alpha3, n.emoji AS country_emoji
        FROM athletes a
        JOIN nationalities n ON a.country_full = n.country_full
        WHERE a.athlete_id = ?
    """, [athlete_id]).fetchone()
    if not row:
        return None
    cols = ["athlete_id", "name", "country_full", "year_of_birth",
            "gender", "profile_img", "country_alpha3", "country_emoji"]
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
    """
    conn = _get_conn()
    course_in = _course_in(course)
    discs = ["overall", "swim", "bike", "run", "transition"]
    result = {}
    for disc in discs:
        row = conn.execute(f"""
            SELECT ra.{disc}_change, r.race_handle, ra.race_id
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE ra.athlete_id = ? AND ra.category = ? AND r.distance IN {course_in}
              AND ra.{disc}_change IS NOT NULL
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
    Results at Olympic / WC / WTCS / World Cup / Continental Cup races.
    Returns list of dicts: {tier, position, race_id, race_handle, race_date, age_group}
    age_group is "U23" / "Junior" for non-Elite world champs, else None.
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT res.race_id, res.position, r.race_title, r.race_handle, r.cat_ids, r.race_date, r.prog_name
        FROM results res
        JOIN races r ON res.race_id = r.race_id
        WHERE res.athlete_id = ?
          AND res.status = 'Finished'
          AND res.position IS NOT NULL
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
    """
    AG World Championship results for an athlete.
    Returns list of dicts: {tier="ag_world_champs", position, race_id, race_handle, race_date}
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

        # AG World Championship: has world champs cat_id OR title contains "world championship"
        title_lower = race_title.lower()
        is_world_champs = (624 in cat_ids or 348 in cat_ids or
                           ("world" in title_lower and "championship" in title_lower))
        if is_world_champs:
            notable.append({"tier": "ag_world_champs", "position": position,
                             "race_id": race_id, "race_handle": race_handle, "race_date": race_date})

    return notable


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
    Behind time = time - fastest non-zero time in that race (NULL if DNF/0).
    Returns list of dicts ordered by race_date desc.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    rows = conn.execute(f"""
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
            -- behind times: subtract per-race winner time computed across ALL athletes
            CASE WHEN res.overall_s > 0 THEN res.overall_s - w.min_overall END AS overall_behind_s,
            CASE WHEN res.swim_s    > 0 THEN res.swim_s    - w.min_swim    END AS swim_behind_s,
            CASE WHEN res.bike_s    > 0 THEN res.bike_s    - w.min_bike    END AS bike_behind_s,
            CASE WHEN res.run_s     > 0 THEN res.run_s     - w.min_run     END AS run_behind_s,
            CASE WHEN res.t1_s      > 0 THEN res.t1_s      - w.min_t1      END AS t1_behind_s,
            CASE WHEN res.t2_s      > 0 THEN res.t2_s      - w.min_t2      END AS t2_behind_s,
            std.overall_std,
            ig.race_id IS NOT NULL AS is_ignored,
            ig.parent_race_id,
            r.is_multi_stage
        FROM results res
        JOIN races r ON res.race_id = r.race_id
        JOIN (
            SELECT race_id,
                   MIN(CASE WHEN overall_s > 0 THEN overall_s END) AS min_overall,
                   MIN(CASE WHEN swim_s    > 0 THEN swim_s    END) AS min_swim,
                   MIN(CASE WHEN bike_s    > 0 THEN bike_s    END) AS min_bike,
                   MIN(CASE WHEN run_s     > 0 THEN run_s     END) AS min_run,
                   MIN(CASE WHEN t1_s      > 0 THEN t1_s      END) AS min_t1,
                   MIN(CASE WHEN t2_s      > 0 THEN t2_s      END) AS min_t2
            FROM results
            GROUP BY race_id
        ) w ON res.race_id = w.race_id
        -- top-10 overall standard per race
        LEFT JOIN (
            SELECT ra.race_id,
                   SUM((ra.overall - ra.overall_change) * EXP(-0.1 * (top10.position - 1))) / SUM(EXP(-0.1 * (top10.position - 1))) AS overall_std
            FROM ratings ra
            JOIN results top10 ON ra.race_id = top10.race_id
                              AND ra.athlete_id = top10.athlete_id
            WHERE top10.status = 'Finished'
              AND top10.position IS NOT NULL
            GROUP BY ra.race_id
            HAVING COUNT(*) >= 3
        ) std ON std.race_id = res.race_id
        LEFT JOIN ignored_races ig ON ig.race_id = res.race_id
        WHERE res.athlete_id = ? AND r.category = ? AND r.distance IN {course_in}
        ORDER BY r.race_date DESC, res.race_id DESC
    """, [athlete_id, category]).fetchall()

    cols = [
        "race_id", "race_title", "race_date", "program", "gender", "position", "status",
        "overall_s", "swim_s", "bike_s", "run_s", "t1_s", "t2_s",
        "overall_behind_s", "swim_behind_s", "bike_behind_s", "run_behind_s",
        "t1_behind_s", "t2_behind_s", "overall_std", "is_ignored", "parent_race_id",
        "is_multi_stage",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_athlete_rating_history(athlete_id, category='elite', course='short'):
    """
    All rating entries for an athlete with race info and finish position.
    Returns list of dicts ordered by race_date desc. Course-scoped.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    rows = conn.execute(f"""
        SELECT
            ra.race_id,
            r.race_date,
            r.race_title,
            r.prog_name,
            res.position,
            res.status,
            ra.overall,    ra.overall_change,
            ra.swim,       ra.swim_change,
            ra.bike,       ra.bike_change,
            ra.run,        ra.run_change,
            ra.transition, ra.transition_change
        FROM ratings ra
        JOIN races r   ON ra.race_id   = r.race_id
        JOIN results res ON ra.race_id = res.race_id AND ra.athlete_id = res.athlete_id
        WHERE ra.athlete_id = ? AND ra.category = ? AND r.distance IN {course_in}
        ORDER BY r.race_date DESC, ra.race_id DESC
    """, [athlete_id, category]).fetchall()

    cols = [
        "race_id", "race_date", "race_title", "race_program", "position", "status",
        "overall_rating",    "overall_change",
        "swim_rating",       "swim_change",
        "bike_rating",       "bike_change",
        "run_rating",        "run_change",
        "transition_rating", "transition_change",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_athlete_times_data(athlete_id):
    """
    Corrected times + pct-behind-leader per race, for chart rendering.
    pct_behind = (time - fastest) / fastest, or None if time is 0.
    Splits and per-race mins both use auto-corrected values.
    Returns list of dicts ordered by race_date asc (chronological for charts).
    """
    conn = _get_conn()
    rows = conn.execute("""
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
    """, [athlete_id]).fetchall()

    cols = [
        "race_id", "race_date", "race_title",
        "overall_s", "swim_s", "bike_s", "run_s",
        "overall_pct_behind", "swim_pct_behind", "bike_pct_behind", "run_pct_behind",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_athlete_ratings_data(athlete_id, category='elite', course='short'):
    """
    Raw ratings per race for chart rendering (chronological order). Course-scoped.
    Includes discipline times, diffs from race leader, and world rankings.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    rows = conn.execute(f"""
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
    """, [athlete_id, category]).fetchall()

    cols = [
        "race_id", "race_date", "race_title",
        "overall_rating", "swim_rating", "bike_rating", "run_rating", "transition_rating",
        "overall_change", "swim_change", "bike_change", "run_change", "transition_change",
        "overall_s", "swim_s", "bike_s", "run_s", "t1_s", "t2_s",
        "overall_diff", "swim_diff", "bike_diff", "run_diff", "t1_diff", "t2_diff",
        "world_overall", "world_swim", "world_bike", "world_run", "world_transition",
        "status",
    ]
    return [dict(zip(cols, r)) for r in rows]


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
    """All races for an event, ordered by race date."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT race_id, race_title, prog_name, race_date, gender, is_multi_stage
        FROM races
        WHERE event_id = ?
        ORDER BY race_date ASC
    """, [event_id]).fetchall()
    cols = ["race_id", "race_title", "prog_name", "race_date", "gender", "is_multi_stage"]
    return [dict(zip(cols, r)) for r in rows]


def get_event_races_detail(event_id):
    """All races for an event with podium (top 3 + time gaps) and overall race standard."""
    conn = _get_conn()
    race_rows = conn.execute("""
        SELECT race_id, race_title, prog_name, race_date, gender
        FROM races
        WHERE event_id = ?
        ORDER BY race_date ASC, race_id ASC
    """, [event_id]).fetchall()
    if not race_rows:
        return []

    races = [{"race_id": r[0], "race_title": r[1], "prog_name": r[2],
              "race_date": r[3], "gender": r[4], "podium": [], "standard": None}
             for r in race_rows]
    race_ids = [r["race_id"] for r in races]
    ph = ",".join("?" * len(race_ids))

    podium_rows = conn.execute(f"""
        SELECT res.race_id, res.position, a.athlete_id, a.name, n.emoji, res.overall_s, a.profile_img
        FROM results res
        JOIN athletes a ON res.athlete_id = a.athlete_id
        JOIN nationalities n ON a.country_full = n.country_full
        WHERE res.race_id IN ({ph})
          AND res.position IN (1, 2, 3)
          AND res.status = 'Finished'
        ORDER BY res.race_id, res.position
    """, race_ids).fetchall()

    podium_by_race = {}
    for race_id, pos, athlete_id, name, emoji, overall_s, profile_img in podium_rows:
        podium_by_race.setdefault(race_id, []).append(
            {"position": pos, "athlete_id": athlete_id, "name": name,
             "emoji": emoji, "overall_s": overall_s, "profile_img": profile_img}
        )

    std_rows = conn.execute(f"""
        SELECT ra.race_id,
            SUM((ra.overall    - ra.overall_change)    * EXP(-0.1*(res.position-1))) / SUM(EXP(-0.1*(res.position-1))),
            SUM((ra.swim       - ra.swim_change)       * EXP(-0.1*(res.position-1))) / SUM(EXP(-0.1*(res.position-1))),
            SUM((ra.bike       - ra.bike_change)       * EXP(-0.1*(res.position-1))) / SUM(EXP(-0.1*(res.position-1))),
            SUM((ra.run        - ra.run_change)        * EXP(-0.1*(res.position-1))) / SUM(EXP(-0.1*(res.position-1))),
            SUM((ra.transition - ra.transition_change) * EXP(-0.1*(res.position-1))) / SUM(EXP(-0.1*(res.position-1)))
        FROM ratings ra
        JOIN results res ON ra.race_id = res.race_id AND ra.athlete_id = res.athlete_id
        WHERE ra.race_id IN ({ph})
          AND res.status = 'Finished'
          AND res.position IS NOT NULL
        GROUP BY ra.race_id
    """, race_ids).fetchall()
    std_by_race = {
        r[0]: {"overall": r[1], "swim": r[2], "bike": r[3], "run": r[4], "transition": r[5]}
        for r in std_rows
    }

    for race in races:
        rid = race["race_id"]
        raw = podium_by_race.get(rid, [])
        winner_s = raw[0]["overall_s"] if raw else None
        race["podium"] = [
            {**p,
             "time": _fmt_time(p["overall_s"]),
             "gap": f"+{_fmt_time(p['overall_s'] - winner_s)}"
                    if p["position"] != 1 and p["overall_s"] and winner_s else None}
            for p in raw
        ]
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
               r.race_handle, r.event_id, r.is_multi_stage
        FROM races r
        JOIN events e ON r.event_id = e.event_id
        WHERE r.race_id = ?
    """, [race_id]).fetchone()
    if not row:
        return None
    cols = ["race_id", "race_title", "prog_name", "race_date",
            "location", "country", "gender", "sub_category",
            "race_handle", "event_id", "is_multi_stage"]
    return dict(zip(cols, row))


def get_race_results(race_id):
    """
    All results for a race with athlete info and behind-leader times.
    Corrections are applied inline — corrected splits are used for all times and
    behind-leader calculations, matching the behaviour in ratings.py.
    Returns list of dicts ordered by position (nulls last for DNFs).
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
            n.emoji  AS country_emoji,
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
    """, [race_id, race_id]).fetchall()

    cols = [
        "athlete_id", "position", "status",
        "overall_s", "swim_s", "bike_s", "run_s", "t1_s", "t2_s",
        "name", "year_of_birth", "profile_img", "country_alpha3", "country_emoji",
        "overall_behind_s", "swim_behind_s", "bike_behind_s", "run_behind_s",
        "t1_behind_s", "t2_behind_s",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_race_corrections(race_id):
    """
    Returns correction records for a race, paired with their original result values.
    Only returns athletes that actually have a correction for this race. Manual
    rows win over auto in the reported value (mirrors ratings.py precedence).
    """
    conn = _get_conn()
    rows = conn.execute("""
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
            n.emoji AS country_emoji,
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
    """, [race_id, race_id]).fetchall()

    cols = [
        "athlete_id", "name", "country_emoji", "position", "status", "notes",
        "orig_overall", "corr_overall",
        "orig_swim",    "corr_swim",
        "orig_t1",      "corr_t1",
        "orig_bike",    "corr_bike",
        "orig_t2",      "corr_t2",
        "orig_run",     "corr_run",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_race_ratings(race_id):
    """
    All ratings for a race with athlete info.
    Returns list of dicts in the same order as get_race_results().
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            ra.athlete_id,
            a.name,
            n.alpha3  AS country_alpha3,
            n.emoji   AS country_emoji,
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
    """, [race_id]).fetchall()

    cols = [
        "athlete_id", "name", "country_alpha3", "country_emoji", "year_of_birth", "position", "status",
        "overall_rating",    "overall_change",
        "swim_rating",       "swim_change",
        "bike_rating",       "bike_change",
        "run_rating",        "run_change",
        "transition_rating", "transition_change",
    ]
    return [dict(zip(cols, r)) for r in rows]


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
            "SELECT race_title, is_multi_stage FROM races WHERE race_id = ?", [parent_race_id]
        ).fetchone()
        if p:
            parent_title, parent_is_multi_stage = p[0], bool(p[1])
    return {
        "reason": reason,
        "parent_race_id": parent_race_id,
        "parent_race_title": parent_title,
        "parent_is_multi_stage": parent_is_multi_stage,
    }


def get_race_standards(race_id):
    """Exponential-decay weighted average pre-race rating per discipline (k=0.1).

    For normal races, pre-race = ra.overall - ra.overall_change (rating before this race).
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
            )
            SELECT
                SUM(pr.overall    * EXP(-0.1*(res.position-1))) / SUM(EXP(-0.1*(res.position-1))),
                SUM(pr.swim       * EXP(-0.1*(res.position-1))) / SUM(EXP(-0.1*(res.position-1))),
                SUM(pr.bike       * EXP(-0.1*(res.position-1))) / SUM(EXP(-0.1*(res.position-1))),
                SUM(pr.run        * EXP(-0.1*(res.position-1))) / SUM(EXP(-0.1*(res.position-1))),
                SUM(pr.transition * EXP(-0.1*(res.position-1))) / SUM(EXP(-0.1*(res.position-1)))
            FROM results res
            JOIN pre_race pr ON res.athlete_id = pr.athlete_id AND pr.rn = 1
            WHERE res.race_id = ?
              AND res.status = 'Finished'
              AND res.position IS NOT NULL
        """, [race_id, race_id, race_id]).fetchone()
    else:
        row = conn.execute("""
            SELECT
                SUM((ra.overall    - ra.overall_change)    * EXP(-0.1 * (res.position - 1))) / SUM(EXP(-0.1 * (res.position - 1))),
                SUM((ra.swim       - ra.swim_change)       * EXP(-0.1 * (res.position - 1))) / SUM(EXP(-0.1 * (res.position - 1))),
                SUM((ra.bike       - ra.bike_change)       * EXP(-0.1 * (res.position - 1))) / SUM(EXP(-0.1 * (res.position - 1))),
                SUM((ra.run        - ra.run_change)        * EXP(-0.1 * (res.position - 1))) / SUM(EXP(-0.1 * (res.position - 1))),
                SUM((ra.transition - ra.transition_change) * EXP(-0.1 * (res.position - 1))) / SUM(EXP(-0.1 * (res.position - 1)))
            FROM ratings ra
            JOIN results res ON ra.race_id = res.race_id AND ra.athlete_id = res.athlete_id
            WHERE ra.race_id = ?
              AND res.status = 'Finished'
              AND res.position IS NOT NULL
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

    conn = _get_conn()
    course_in = _course_in(course)
    row = conn.execute(f"""
        WITH stds AS (
            SELECT
                SUM((ra.overall    - ra.overall_change)    * EXP(-0.1 * (res.position - 1))) / SUM(EXP(-0.1 * (res.position - 1))) AS overall,
                SUM((ra.swim       - ra.swim_change)       * EXP(-0.1 * (res.position - 1))) / SUM(EXP(-0.1 * (res.position - 1))) AS swim,
                SUM((ra.bike       - ra.bike_change)       * EXP(-0.1 * (res.position - 1))) / SUM(EXP(-0.1 * (res.position - 1))) AS bike,
                SUM((ra.run        - ra.run_change)        * EXP(-0.1 * (res.position - 1))) / SUM(EXP(-0.1 * (res.position - 1))) AS run,
                SUM((ra.transition - ra.transition_change) * EXP(-0.1 * (res.position - 1))) / SUM(EXP(-0.1 * (res.position - 1))) AS transition
            FROM races r
            JOIN results res ON r.race_id = res.race_id
            JOIN ratings ra  ON ra.race_id = res.race_id AND ra.athlete_id = res.athlete_id
            WHERE r.gender = ?
              AND r.distance IN {course_in}
              AND res.status = 'Finished'
              AND res.position IS NOT NULL
            GROUP BY r.race_id
            HAVING COUNT(*) >= 3
        )
        SELECT
            quantile_cont(overall,    [0.30, 0.60, 0.85, 0.95]) AS overall_qs,
            quantile_cont(swim,       [0.30, 0.60, 0.85, 0.95]) AS swim_qs,
            quantile_cont(bike,       [0.30, 0.60, 0.85, 0.95]) AS bike_qs,
            quantile_cont(run,        [0.30, 0.60, 0.85, 0.95]) AS run_qs,
            quantile_cont(transition, [0.30, 0.60, 0.85, 0.95]) AS transition_qs
        FROM stds
    """, [gender]).fetchone()

    result = {}
    for i, disc in enumerate(["overall", "swim", "bike", "run", "transition"]):
        qs = row[i]
        result[disc] = {"p30": qs[0], "p60": qs[1], "p85": qs[2], "p95": qs[3]}

    _standard_thresholds_cache[cache_key] = result
    return result


def get_race_best_performances(race_id):
    """Max positive rating change per discipline + athlete name."""
    conn = _get_conn()
    discs = ["overall", "swim", "bike", "run", "transition"]
    result = {}
    for disc in discs:
        row = conn.execute(f"""
            SELECT ra.{disc}_change, a.name, ra.athlete_id
            FROM ratings ra
            JOIN athletes a ON ra.athlete_id = a.athlete_id
            WHERE ra.race_id = ?
              AND ra.{disc}_change > 0
            ORDER BY ra.{disc}_change DESC
            LIMIT 1
        """, [race_id]).fetchone()
        result[f"{disc}_change"]       = row[0] if row else None
        result[f"{disc}_athlete_name"] = row[1] if row else ""
        result[f"{disc}_athlete_id"]   = row[2] if row else None
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

def get_common_races(athlete1_id, athlete2_id):
    """
    Races where both athletes competed, with each athlete's time + position + status.
    Returns list of dicts ordered by race_date desc.
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            r1.race_id,
            r.race_title,
            r.race_date,
            r1.position    AS a1_position,
            r1.status      AS a1_status,
            r1.overall_s   AS a1_overall_s,
            r2.position    AS a2_position,
            r2.status      AS a2_status,
            r2.overall_s   AS a2_overall_s
        FROM results r1
        JOIN results r2 ON r1.race_id = r2.race_id
        JOIN races r    ON r1.race_id = r.race_id
        WHERE r1.athlete_id = ?
          AND r2.athlete_id = ?
        ORDER BY r.race_date DESC
    """, [athlete1_id, athlete2_id]).fetchall()

    cols = ["race_id", "race_title", "race_date",
            "a1_position", "a1_status", "a1_overall_s",
            "a2_position", "a2_status", "a2_overall_s"]
    return [dict(zip(cols, r)) for r in rows]


def get_athlete_rankings_data(athlete_id, category='elite', course='short'):
    """
    World and national rankings per race for chart rendering (chronological order).
    Returns list of dicts ordered by race_date asc. Course-scoped.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    rows = conn.execute(f"""
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
    """, [athlete_id, category]).fetchall()

    cols = ["race_id", "race_date", "race_title",
            "world_overall",    "world_swim",    "world_bike",    "world_run",    "world_transition",
            "national_overall", "national_swim", "national_bike", "national_run", "national_transition",
            "overall_s", "swim_s", "bike_s", "run_s", "t1_s", "t2_s",
            "overall_diff", "swim_diff", "bike_diff", "run_diff", "t1_diff", "t2_diff",
            "status"]
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Race predictions
# ---------------------------------------------------------------------------

def get_prediction_models():
    """Return all prediction models as dict: (gender, distance, discipline) -> {slope, intercept}."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT gender, distance, discipline, slope, intercept FROM prediction_models"
        ).fetchall()
    except Exception:
        return {}  # table not yet created in this DB (schema migration pending)
    return {(r[0], r[1], r[2]): {"slope": r[3], "intercept": r[4]} for r in rows}


def get_race_distance_type(race_id):
    """Return 'sprint', 'standard', or None.

    event_spec_ids alone is unreliable: many events list both 376 (sprint) and 377
    (standard) because multiple programme distances run at the same event, making every
    race in that event look ambiguous.  When both specs are present we fall back to the
    winner's actual finishing time to disambiguate - a clean split exists at 90 min
    for males and females alike.
    """
    conn = _get_conn()
    row = conn.execute("SELECT event_spec_ids, gender FROM races WHERE race_id = ?", [race_id]).fetchone()
    if not row:
        return None
    spec, gender = row

    has_sprint   = '376' in spec
    has_standard = '377' in spec

    if has_sprint and not has_standard:
        return 'sprint'
    if has_standard and not has_sprint:
        return 'standard'
    if has_sprint and has_standard:
        # Ambiguous - use winner's time to decide.  Threshold: 90 min (5400 s).
        winner = conn.execute(
            "SELECT overall_s FROM results WHERE race_id = ? AND position = 1", [race_id]
        ).fetchone()
        if winner and winner[0] > 0:
            return 'sprint' if winner[0] < 5400 else 'standard'
    return None


def get_race_pre_race_ratings(race_id):
    """Return most-recent rating for each field athlete before this race date.

    Returns list of dicts with athlete_id + per-discipline ratings.
    Athletes with no prior race in the same course bucket are absent from the result.
    """
    conn = _get_conn()
    # Derive course from the target race's distance so we only pull prior
    # ratings from the same course (short or long).
    row = conn.execute("SELECT distance FROM races WHERE race_id = ?", [race_id]).fetchone()
    if not row:
        return []
    course = course_for_distance(row[0])
    if course is None:
        return []
    course_in = _course_in(course)
    rows = conn.execute(f"""
        WITH race_info AS (SELECT race_date FROM races WHERE race_id = ?),
             field     AS (SELECT DISTINCT athlete_id FROM results WHERE race_id = ?)
        SELECT DISTINCT ON (ra.athlete_id)
               ra.athlete_id, ra.overall, ra.swim, ra.bike, ra.run, ra.transition,
               (SELECT COUNT(*) FROM results res2
                JOIN races r2 ON res2.race_id = r2.race_id
                WHERE res2.athlete_id = ra.athlete_id
                  AND r2.race_date < (SELECT race_date FROM race_info)
                  AND r2.distance IN {course_in}) AS prior_starts
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        JOIN field f ON ra.athlete_id = f.athlete_id
        WHERE r.race_date < (SELECT race_date FROM race_info)
          AND r.distance IN {course_in}
        ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
    """, [race_id, race_id]).fetchall()
    cols = ["athlete_id", "overall", "swim", "bike", "run", "transition", "prior_starts"]
    return [dict(zip(cols, r)) for r in rows]


# --- Upcoming race queries ---

def get_athlete_upcoming_races(athlete_id):
    """
    All upcoming races an athlete is entered in, ordered by date.
    Returns list of dicts with race info needed for predictions.
    """
    conn = _get_conn()
    rows = conn.execute("""
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
    """, [athlete_id]).fetchall()

    cols = ["race_id", "prog_name", "race_date", "gender", "event_spec_ids",
            "category", "event_name", "event_id", "country"]
    return [dict(zip(cols, r)) for r in rows]


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
    rows = conn.execute(f"""
        SELECT
            sle.athlete_id,
            sle.start_num,
            a.name,
            a.year_of_birth,
            a.profile_img,
            n.alpha3 AS country_alpha3,
            n.emoji  AS country_emoji,
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
    """, [race_id]).fetchall()
    cols = ["athlete_id", "start_num", "name", "year_of_birth", "profile_img",
            "country_alpha3", "country_emoji",
            "overall_rating", "swim_rating", "bike_rating", "run_rating", "transition_rating"]
    return [dict(zip(cols, r)) for r in rows]


def get_upcoming_race_standards(race_id, course='short'):
    """Simple average of current ratings for all athletes in the start list, scoped to course."""
    conn = _get_conn()
    course_in = _course_in(course)
    row = conn.execute(f"""
        SELECT
            AVG(ra.overall),
            AVG(ra.swim),
            AVG(ra.bike),
            AVG(ra.run),
            AVG(ra.transition)
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
    """, [race_id]).fetchone()
    if not row or row[0] is None:
        return {d: 0.0 for d in ["overall", "swim", "bike", "run", "transition"]}
    return {
        "overall":    row[0] or 0.0,
        "swim":       row[1] or 0.0,
        "bike":       row[2] or 0.0,
        "run":        row[3] or 0.0,
        "transition": row[4] or 0.0,
    }


def get_upcoming_events(country=None, course='short'):
    """All upcoming events grouped with their races, entry counts, and top-3 by rating.

    Optional `country` filter restricts to events hosted in that country (name).
    Top-3 ratings are scoped to the given course.
    """
    conn = _get_conn()
    course_in = _course_in(course)
    country_sql, country_params = ("WHERE e.country = ?", [country]) if country else ("", [])
    race_rows = conn.execute(f"""
        SELECT
            e.event_id, e.name, e.venue, e.country, e.start_date,
            ur.race_id, ur.prog_name, ur.gender, ur.category, ur.event_spec_ids,
            COUNT(sle.athlete_id) AS entry_count
        FROM events e
        JOIN upcoming_races ur ON ur.event_id = e.event_id
        LEFT JOIN start_list_entries sle ON sle.race_id = ur.race_id
        {country_sql}
        GROUP BY e.event_id, e.name, e.venue, e.country, e.start_date,
                 ur.race_id, ur.prog_name, ur.gender, ur.category, ur.event_spec_ids
        ORDER BY e.start_date, e.event_id, ur.race_id
    """, country_params).fetchall()

    race_cols = ["event_id", "name", "venue", "country", "start_date",
                 "race_id", "prog_name", "gender", "category", "event_spec_ids", "entry_count"]

    if not race_rows:
        return []

    all_race_ids = [r[5] for r in race_rows]
    ph = ",".join("?" * len(all_race_ids))

    top3_rows = conn.execute(f"""
        SELECT sle.race_id, a.athlete_id, a.name, n.emoji, a.profile_img, ra.overall
        FROM start_list_entries sle
        JOIN athletes a ON sle.athlete_id = a.athlete_id
        JOIN nationalities n ON a.country_full = n.country_full
        JOIN (
            SELECT ra2.athlete_id, ra2.overall
            FROM ratings ra2
            JOIN races r2 ON ra2.race_id = r2.race_id
            WHERE r2.distance IN {course_in}
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY ra2.athlete_id ORDER BY r2.race_date DESC, ra2.race_id DESC
            ) = 1
        ) ra ON ra.athlete_id = sle.athlete_id
        WHERE sle.race_id IN ({ph})
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY sle.race_id ORDER BY ra.overall DESC
        ) <= 3
        ORDER BY sle.race_id, ra.overall DESC
    """, all_race_ids).fetchall()

    top3_by_race = {}
    for race_id, athlete_id, name, emoji, profile_img, overall in top3_rows:
        top3_by_race.setdefault(race_id, []).append(
            {"athlete_id": athlete_id, "name": name, "emoji": emoji,
             "profile_img": profile_img, "overall_rating": overall}
        )

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
        events[eid]["races"].append({
            "race_id":        r["race_id"],
            "prog_name":      r["prog_name"],
            "gender":         r["gender"],
            "category":       r["category"],
            "event_spec_ids": r["event_spec_ids"],
            "entry_count":    r["entry_count"],
            "top3":           top3_by_race.get(r["race_id"], []),
        })

    return list(events.values())


def get_upcoming_event_races_detail(event_id, course='short'):
    """Upcoming races for an event with top-3 by rating and field standards. Course-scoped."""
    conn = _get_conn()
    course_in = _course_in(course)
    race_rows = conn.execute("""
        SELECT race_id, prog_name, race_date, gender, category, event_spec_ids
        FROM upcoming_races
        WHERE event_id = ?
        ORDER BY race_id ASC
    """, [event_id]).fetchall()
    if not race_rows:
        return []

    races = [{"race_id": r[0], "prog_name": r[1], "race_date": r[2],
              "gender": r[3], "category": r[4], "event_spec_ids": r[5]}
             for r in race_rows]
    race_ids = [r["race_id"] for r in races]
    ph = ",".join("?" * len(race_ids))

    # Top 3 athletes per race by current overall rating
    top3_rows = conn.execute(f"""
        SELECT sle.race_id, a.athlete_id, a.name, n.emoji, a.profile_img, ra.overall
        FROM start_list_entries sle
        JOIN athletes a ON sle.athlete_id = a.athlete_id
        JOIN nationalities n ON a.country_full = n.country_full
        JOIN (
            SELECT ra2.athlete_id, ra2.overall
            FROM ratings ra2
            JOIN races r2 ON ra2.race_id = r2.race_id
            WHERE r2.distance IN {course_in}
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY ra2.athlete_id ORDER BY r2.race_date DESC, ra2.race_id DESC
            ) = 1
        ) ra ON ra.athlete_id = sle.athlete_id
        WHERE sle.race_id IN ({ph})
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY sle.race_id ORDER BY ra.overall DESC
        ) <= 3
        ORDER BY sle.race_id, ra.overall DESC
    """, race_ids).fetchall()

    top3_by_race = {}
    for race_id, athlete_id, name, emoji, profile_img, overall in top3_rows:
        top3_by_race.setdefault(race_id, []).append(
            {"athlete_id": athlete_id, "name": name, "emoji": emoji,
             "profile_img": profile_img, "overall_rating": overall}
        )

    # Field average ratings for standards
    std_rows = conn.execute(f"""
        SELECT sle.race_id,
            AVG(ra.overall), AVG(ra.swim), AVG(ra.bike), AVG(ra.run), AVG(ra.transition)
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
        GROUP BY sle.race_id
    """, race_ids).fetchall()

    std_by_race = {
        r[0]: {"overall": r[1], "swim": r[2], "bike": r[3], "run": r[4], "transition": r[5]}
        for r in std_rows
    }

    for race in races:
        rid = race["race_id"]
        race["top3"]         = top3_by_race.get(rid, [])
        race["standards_raw"] = std_by_race.get(rid)

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

    # Up to N most recent elite events per series (M and W typically share an event)
    MAX_EDITIONS = 5
    event_rows = conn.execute(f"""
        WITH ranked AS (
          SELECT es.series_id, r.event_id,
                 MAX(r.race_date) AS race_date,
                 ROW_NUMBER() OVER (
                   PARTITION BY es.series_id
                   ORDER BY MAX(r.race_date) DESC, r.event_id DESC
                 ) AS rn
          FROM event_series es
          JOIN races r ON r.event_id = es.event_id
          WHERE es.series_id IN ({ph})
            AND r.sub_category = 'elite'
          GROUP BY es.series_id, r.event_id
        )
        SELECT l.series_id, l.event_id, l.race_date, e.name, e.venue, e.country, nat.emoji, l.rn
        FROM ranked l
        JOIN events e ON e.event_id = l.event_id
        LEFT JOIN nationalities nat ON nat.country_full = e.country
        WHERE l.rn <= ?
        ORDER BY l.series_id, l.rn
    """, params + [MAX_EDITIONS]).fetchall()

    editions_by_series = {}
    all_event_ids = []
    for sid, event_id, race_date, ename, venue, country, emoji, _rn in event_rows:
        editions_by_series.setdefault(sid, []).append({
            "event_id":       event_id,
            "event_name":     ename,
            "venue":          venue,
            "country":        country,
            "country_emoji":  emoji,
            "race_date":      race_date,
            "male_race_id":   None,
            "female_race_id": None,
            "male_podium":    [],
            "female_podium":  [],
            "is_multi_stage": False,
        })
        all_event_ids.append(event_id)

    # Pull elite M/W race_ids for each of those events
    race_by_event: dict = {}
    if all_event_ids:
        eph = ','.join(['?'] * len(all_event_ids))
        race_rows = conn.execute(f"""
            SELECT event_id, gender, race_id, is_multi_stage
            FROM races
            WHERE event_id IN ({eph})
              AND sub_category = 'elite'
              AND gender IN ('male','female')
        """, all_event_ids).fetchall()
        for event_id, gender, race_id, is_multi_stage in race_rows:
            race_by_event.setdefault(event_id, {})[gender] = (race_id, is_multi_stage)

    for eds in editions_by_series.values():
        for info in eds:
            rbg = race_by_event.get(info["event_id"], {})
            m = rbg.get("male")
            f = rbg.get("female")
            info["male_race_id"]   = m[0] if m else None
            info["female_race_id"] = f[0] if f else None
            info["is_multi_stage"] = bool((m and m[1]) or (f and f[1]))

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
                   a.athlete_id, a.name, a.profile_img, nat.emoji
            FROM results res
            JOIN athletes a        ON a.athlete_id = res.athlete_id
            JOIN nationalities nat ON nat.country_full = a.country_full
            WHERE res.race_id IN ({rph})
              AND res.position IN (1,2,3)
              AND res.status = 'Finished'
            ORDER BY res.race_id, res.position
        """, all_race_ids).fetchall()
        for race_id, pos, overall_s, aid, name, img, emoji in pod_rows:
            podiums.setdefault(race_id, []).append({
                "position": pos, "overall_s": overall_s,
                "athlete_id": aid, "name": name,
                "profile_img": img, "country_emoji": emoji,
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
               a.name, a.profile_img, nat.emoji
        FROM ranked rk
        JOIN athletes a        ON a.athlete_id = rk.athlete_id
        JOIN nationalities nat ON nat.country_full = a.country_full
        WHERE rk.n = rk.max_n
        ORDER BY rk.series_id, rk.gender, rk.latest_date DESC, a.name
    """, params).fetchall()

    # Build the leader objects first so we can then attach editions.
    out = {sid: {"editions": editions_by_series.get(sid, []), "top_male": [], "top_female": []}
           for sid in series_ids}
    leader_keys = set()
    for sid, gender, aid, n, latest_date, name, img, emoji in leader_rows:
        key = "top_male" if gender == 'male' else "top_female"
        out[sid][key].append({
            "athlete_id":    aid,
            "name":          name,
            "profile_img":   img,
            "country_emoji": emoji,
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
                   e.event_id, e.name AS event_name, e.venue, r.race_date, r.race_id
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
        for sid, gender, aid, event_id, ename, venue, race_date, race_id in ed_rows:
            if (sid, gender, aid) not in leader_keys:
                continue
            ed_by_key.setdefault((sid, gender, aid), []).append({
                "event_id":   event_id,
                "event_name": ename,
                "venue":      venue,
                "race_date":  race_date,
                "race_id":    race_id,
            })

        for sid in series_ids:
            for gender, key in (("male", "top_male"), ("female", "top_female")):
                for leader in out[sid][key]:
                    leader["editions"] = ed_by_key.get((sid, gender, leader["athlete_id"]), [])

    return out


def get_series_by_slug(slug):
    """Series metadata by slug, or None."""
    rows = _series_meta_sql("WHERE s.slug = ?", [slug])
    return dict(zip(_SERIES_META_COLS, rows[0])) if rows else None


def _program_filter(program):
    """Build SQL fragment + params for a (sub_category, gender) filter. program may be None."""
    if not program:
        return "", []
    return "AND r.sub_category = ? AND r.gender = ?", list(program)


def get_series_for_race(race_id):
    """Return series dict {series_id, name, slug} for this race, or None.

    Picks the lowest sort_order (primary series) if the event belongs to multiple.
    """
    conn = _get_conn()
    row = conn.execute("""
        SELECT s.series_id, s.name, s.slug
        FROM races r
        JOIN event_series es ON es.event_id = r.event_id
        JOIN series s        ON s.series_id = es.series_id
        WHERE r.race_id = ?
        ORDER BY s.sort_order, s.name
        LIMIT 1
    """, [race_id]).fetchone()
    return dict(zip(["series_id", "name", "slug"], row)) if row else None


def get_series_for_event(event_id):
    """Return list of series [{series_id, name, slug}] the event belongs to."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT s.series_id, s.name, s.slug
        FROM event_series es
        JOIN series s ON s.series_id = es.series_id
        WHERE es.event_id = ?
        ORDER BY s.sort_order, s.name
    """, [event_id]).fetchall()
    cols = ["series_id", "name", "slug"]
    return [dict(zip(cols, r)) for r in rows]


def get_all_series_for_race(race_id):
    """All series this race's event belongs to."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT DISTINCT s.series_id, s.name, s.slug
        FROM races r
        JOIN event_series es ON es.event_id = r.event_id
        JOIN series s        ON s.series_id = es.series_id
        WHERE r.race_id = ?
        ORDER BY s.sort_order, s.name
    """, [race_id]).fetchall()
    cols = ["series_id", "name", "slug"]
    return [dict(zip(cols, r)) for r in rows]


_SUB_ORDER_CASE = """CASE r.sub_category
    WHEN 'elite'  THEN 0
    WHEN 'u23'    THEN 1
    WHEN 'junior' THEN 2
    WHEN 'youth'  THEN 3
    WHEN 'ag'     THEN 4
    ELSE 5 END"""

_GENDER_ORDER_CASE = "CASE r.gender WHEN 'male' THEN 0 WHEN 'female' THEN 1 ELSE 2 END"


def get_program_options_for_series(series_id):
    """Distinct (sub_category, gender) programs present in a series, ordered for UI tabs."""
    conn = _get_conn()
    rows = conn.execute(f"""
        SELECT r.sub_category, r.gender, COUNT(DISTINCT r.race_id) AS n
        FROM event_series es
        JOIN races r ON r.event_id = es.event_id
        WHERE es.series_id = ?
        GROUP BY r.sub_category, r.gender
        ORDER BY {_SUB_ORDER_CASE}, {_GENDER_ORDER_CASE}
    """, [series_id]).fetchall()
    return [{"sub_category": r[0], "gender": r[1], "count": r[2]} for r in rows]


def get_recurring_groups_for_series(series_id, program=None):
    """Venue groupings within a series: one row per recurring_event with edition count + year range."""
    prog_sql, prog_params = _program_filter(program)
    race_join = "JOIN races r ON r.event_id = e.event_id" if program else ""
    conn = _get_conn()
    rows = conn.execute(f"""
        SELECT re.recurring_event_id, re.slug, re.name, re.venue_key,
               COUNT(DISTINCT e.event_id) AS edition_count,
               MIN(e.start_date) AS first_date,
               MAX(e.start_date) AS last_date
        FROM event_series es
        JOIN events e            ON e.event_id = es.event_id
        JOIN event_recurring er  ON er.event_id = e.event_id
        JOIN recurring_events re ON re.recurring_event_id = er.recurring_event_id
        {race_join}
        WHERE es.series_id = ?
          {prog_sql}
        GROUP BY re.recurring_event_id, re.slug, re.name, re.venue_key
        HAVING edition_count > 1
        ORDER BY edition_count DESC, last_date DESC
    """, [series_id] + prog_params).fetchall()
    cols = ["recurring_event_id", "slug", "name", "venue_key",
            "edition_count", "first_date", "last_date"]
    return [dict(zip(cols, r)) for r in rows]


def get_other_editions_for_event(event_id, program=None):
    """Races in the same recurring group as event_id (excluding event_id's own race(s))."""
    prog_sql, prog_params = _program_filter(program)
    conn = _get_conn()
    rows = conn.execute(f"""
        SELECT r.race_id, r.race_date, r.race_handle, r.prog_name,
               e.event_id, e.name AS event_name, e.venue, e.country
        FROM events ref
        JOIN event_recurring er_ref ON er_ref.event_id = ref.event_id
        JOIN event_recurring er     ON er.recurring_event_id = er_ref.recurring_event_id
        JOIN events e               ON e.event_id = er.event_id
        JOIN races r                ON r.event_id = e.event_id
        WHERE ref.event_id = ?
          AND e.event_id <> ref.event_id
          {prog_sql}
        ORDER BY r.race_date DESC
    """, [event_id] + prog_params).fetchall()
    cols = ["race_id", "race_date", "race_handle", "prog_name",
            "event_id", "event_name", "venue", "country"]
    return [dict(zip(cols, r)) for r in rows]


def get_series_races(series_id, program=None):
    """All races in series newest-first, each with top-3 podium and race standard."""
    from collections import defaultdict
    conn = _get_conn()

    prog_sql, prog_params = _program_filter(program)
    race_rows = conn.execute(f"""
        SELECT r.race_id, r.race_title, r.race_date, r.prog_name, r.gender, r.sub_category,
               e.name AS event_name, e.venue, e.country, e.latitude, e.longitude,
               r.is_multi_stage
        FROM event_series es
        JOIN races r  ON r.event_id = es.event_id
        JOIN events e ON e.event_id = r.event_id
        WHERE es.series_id = ?
          AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
          {prog_sql}
        ORDER BY r.race_date DESC
    """, [series_id] + prog_params).fetchall()
    race_cols = ["race_id", "race_title", "race_date", "prog_name", "gender", "sub_category",
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
               a.athlete_id, a.name, n.emoji, a.profile_img
        FROM results res
        JOIN athletes a ON a.athlete_id = res.athlete_id
        JOIN nationalities n ON n.country_full = a.country_full
        WHERE res.race_id IN ({id_ph})
          AND res.position IN (1, 2, 3)
          AND res.status = 'Finished'
        ORDER BY res.race_id, res.position
    """, race_ids).fetchall()

    podiums = defaultdict(list)
    for race_id, pos, overall_s, swim_s, bike_s, run_s, t1_s, t2_s, athlete_id, name, emoji, profile_img in podium_rows:
        podiums[race_id].append({
            "position": pos, "overall_s": overall_s,
            "swim_s": swim_s, "bike_s": bike_s, "run_s": run_s,
            "t1_s": t1_s, "t2_s": t2_s,
            "athlete_id": athlete_id, "name": name,
            "country_emoji": emoji, "profile_img": profile_img,
        })

    std_rows = conn.execute(f"""
        SELECT ra.race_id,
            SUM((ra.overall    - ra.overall_change)    * EXP(-0.1*(res.position-1))) / SUM(EXP(-0.1*(res.position-1))),
            SUM((ra.swim       - ra.swim_change)       * EXP(-0.1*(res.position-1))) / SUM(EXP(-0.1*(res.position-1))),
            SUM((ra.bike       - ra.bike_change)       * EXP(-0.1*(res.position-1))) / SUM(EXP(-0.1*(res.position-1))),
            SUM((ra.run        - ra.run_change)        * EXP(-0.1*(res.position-1))) / SUM(EXP(-0.1*(res.position-1))),
            SUM((ra.transition - ra.transition_change) * EXP(-0.1*(res.position-1))) / SUM(EXP(-0.1*(res.position-1)))
        FROM ratings ra
        JOIN results res ON ra.race_id = res.race_id AND ra.athlete_id = res.athlete_id
        WHERE ra.race_id IN ({id_ph})
          AND res.status = 'Finished'
          AND res.position IS NOT NULL
        GROUP BY ra.race_id
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


def get_series_all_time_leaders(series_id, program=None):
    """Athletes ranked by wins, with 2nd/3rd counts and formatted win-year list."""
    conn = _get_conn()
    prog_sql, prog_params = _program_filter(program)
    rows = conn.execute(f"""
        SELECT a.athlete_id, a.name, n.emoji, a.profile_img,
               COUNT(CASE WHEN res.position = 1 THEN 1 END) AS wins,
               COUNT(CASE WHEN res.position = 2 THEN 1 END) AS seconds,
               COUNT(CASE WHEN res.position = 3 THEN 1 END) AS thirds,
               MAX(CASE WHEN res.position = 1 THEN r.race_date END) AS latest_win
        FROM event_series es
        JOIN races r     ON r.event_id = es.event_id
        JOIN results res ON res.race_id = r.race_id
        JOIN athletes a  ON a.athlete_id = res.athlete_id
        JOIN nationalities n ON n.country_full = a.country_full
        WHERE es.series_id = ?
          AND res.position IN (1, 2, 3)
          AND res.status = 'Finished'
          AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
          {prog_sql}
        GROUP BY a.athlete_id, a.name, n.emoji, a.profile_img
        HAVING wins > 0
        ORDER BY wins DESC, seconds DESC, thirds DESC, latest_win DESC
    """, [series_id] + prog_params).fetchall()

    leaders = [
        {"athlete_id": r[0], "name": r[1], "country_emoji": r[2], "profile_img": r[3],
         "wins": r[4], "seconds": r[5], "thirds": r[6]}
        for r in rows  # r[7] is latest_win date, used for ordering only
    ]
    if not leaders:
        return leaders

    # Fetch all podium results with short race name, most recent first
    athlete_ids = [l["athlete_id"] for l in leaders]
    ph = ','.join(['?'] * len(athlete_ids))
    podium_rows = conn.execute(f"""
        SELECT res.athlete_id, res.position, res.race_id, r.race_date,
               TRIM(e.venue) AS venue, e.name AS event_name
        FROM event_series es
        JOIN races r     ON r.event_id = es.event_id
        JOIN events e    ON e.event_id = r.event_id
        JOIN results res ON res.race_id = r.race_id
        WHERE es.series_id = ?
          AND res.status = 'Finished'
          AND res.position IN (1, 2, 3)
          AND res.athlete_id IN ({ph})
          AND NOT EXISTS (SELECT 1 FROM ignored_races ig WHERE ig.race_id = r.race_id)
          {prog_sql}
        ORDER BY r.race_date DESC
    """, [series_id] + athlete_ids + prog_params).fetchall()

    podiums_by_athlete: dict[int, list] = {}
    for athlete_id, position, race_id, race_date, venue, event_name in podium_rows:
        short_name = venue or _location_from_name(event_name)
        yr = race_date.year % 100 if race_date else None
        short = f"{short_name} '{yr:02d}" if yr is not None else short_name
        podiums_by_athlete.setdefault(athlete_id, []).append(
            {"position": position, "race_id": race_id, "short": short}
        )

    for l in leaders:
        pds = podiums_by_athlete.get(l["athlete_id"], [])
        l["podiums"] = pds
        l["win_editions"] = [p for p in pds if p["position"] == 1]

    return leaders


def get_series_performance_history(series_id, program=None):
    """Per-race winner/10th/25th overall + split times for the performance charts.
    Uses auto-corrected split times."""
    conn = _get_conn()
    prog_sql, prog_params = _program_filter(program)
    rows = conn.execute(f"""
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
        ranked AS (
            SELECT r.race_id, r.race_date, e.name AS event_name,
                   COALESCE(cw.overall, res.overall_s) AS overall_s,
                   COALESCE(cw.swim,    res.swim_s)    AS swim_s,
                   COALESCE(cw.bike,    res.bike_s)    AS bike_s,
                   COALESCE(cw.run,     res.run_s)     AS run_s,
                   ROW_NUMBER() OVER (PARTITION BY r.race_id ORDER BY COALESCE(cw.overall, res.overall_s) ASC) AS pos_rank,
                   ROW_NUMBER() OVER (PARTITION BY r.race_id ORDER BY NULLIF(COALESCE(cw.swim, res.swim_s), 0) ASC NULLS LAST) AS swim_rank,
                   ROW_NUMBER() OVER (PARTITION BY r.race_id ORDER BY NULLIF(COALESCE(cw.bike, res.bike_s), 0) ASC NULLS LAST) AS bike_rank,
                   ROW_NUMBER() OVER (PARTITION BY r.race_id ORDER BY NULLIF(COALESCE(cw.run,  res.run_s ), 0) ASC NULLS LAST) AS run_rank
            FROM event_series es
            JOIN races r     ON r.event_id = es.event_id
            JOIN events e    ON e.event_id = r.event_id
            JOIN results res ON res.race_id = r.race_id
            LEFT JOIN corr_wide cw ON cw.race_id = res.race_id AND cw.athlete_id = res.athlete_id
            WHERE es.series_id = ?
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
               MAX(CASE WHEN run_rank  = 25 AND run_s  > 0 THEN run_s  END) AS p25_run
        FROM ranked
        GROUP BY race_id, race_date
        ORDER BY race_date
    """, [series_id] + prog_params).fetchall()
    cols = ["race_id", "race_date", "event_name",
            "winner_s", "p10_s", "p25_s",
            "winner_swim", "p10_swim", "p25_swim",
            "winner_bike", "p10_bike", "p25_bike",
            "winner_run",  "p10_run",  "p25_run"]
    return [dict(zip(cols, r)) for r in rows]
