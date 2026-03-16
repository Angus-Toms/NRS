"""
Read-only query functions against the PTD DuckDB database.

All functions return plain dicts/lists — no custom objects, no DataFrames.
Formatting stays in the routers.
"""

from ast import literal_eval

from ptd_data import db

# Module-level read-only connection, opened on first use
_conn = None


def _get_conn():
    global _conn
    if _conn is None:
        _conn = db.get_conn(read_only=True)
    return _conn


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

def search_athletes(query, gender=None):
    """
    Substring search by name (case-insensitive).
    Returns list of dicts ordered by current overall rating desc.
    Optionally filtered by gender ('male' or 'female').
    """
    conn = _get_conn()
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


def get_podium(gender):
    """
    Top 3 athletes by current overall rating for a given gender.
    Returns list of dicts.
    """
    conn = _get_conn()
    rows = conn.execute("""
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
            ORDER BY rk.athlete_id, r.race_date DESC, rk.race_id DESC
        ) rk ON a.athlete_id = rk.athlete_id
        JOIN (
            SELECT DISTINCT ON (ra.athlete_id)
                   ra.athlete_id, ra.overall
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
        ) cur ON a.athlete_id = cur.athlete_id
        WHERE a.gender = ?
        ORDER BY cur.overall DESC
        LIMIT 3
    """, [gender]).fetchall()

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

def get_recent_events(offset, limit):
    """Events sorted by start_date desc, paginated, with constituent races."""
    conn = _get_conn()
    event_rows = conn.execute("""
        SELECT event_id, name, venue, country, start_date, end_date
        FROM events e
        WHERE EXISTS (SELECT 1 FROM races r WHERE r.event_id = e.event_id)
        ORDER BY start_date DESC
        LIMIT ? OFFSET ?
    """, [limit, offset]).fetchall()
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
            SELECT res.race_id, res.position, a.athlete_id, a.name, n.emoji, res.overall_s
            FROM results res
            JOIN athletes a ON res.athlete_id = a.athlete_id
            JOIN nationalities n ON a.country_full = n.country_full
            WHERE res.race_id IN ({ph})
              AND res.position IN (1, 2, 3)
              AND res.status = 'Finished'
            ORDER BY res.race_id, res.position
        """, podium_race_ids).fetchall()

        podium_by_race = {}
        for race_id, position, athlete_id, name, emoji, overall_s in podium_rows:
            podium_by_race.setdefault(race_id, []).append(
                {"position": position, "athlete_id": athlete_id, "name": name,
                 "emoji": emoji, "time": _fmt_time(overall_s)}
            )
        for event in event_map.values():
            for race in event["races"][:2]:
                race["podium"] = podium_by_race.get(race["race_id"], [])

    return [event_map[r[0]] for r in event_rows]


def get_total_events():
    return _get_conn().execute(
        "SELECT COUNT(*) FROM events e WHERE EXISTS (SELECT 1 FROM races r WHERE r.event_id = e.event_id)"
    ).fetchone()[0]


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
                    active_only, offset, limit=100):
    """
    Paginated leaderboard with all filters applied in SQL.

    order='top'  → sort by current world rank for `disc` ASC
    order='hot'  → filter to non-zero 1yr change, sort by change DESC

    Returns list of dicts. Python caller assigns display rank = offset + 1, offset + 2, ...
    """
    assert disc in _VALID_DISCS and order in _VALID_ORDERS

    conn = _get_conn()

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
        filters.append("c.last_race_date >= CURRENT_DATE - INTERVAL 1 YEAR")
    if order == "hot":
        filters.append(f"COALESCE(c.{rating_col} - ya.{rating_col}, 0) != 0")

    where = " AND ".join(filters)

    order_clause = (
        f"c.{rating_col} DESC, a.athlete_id ASC"
        if order == "top"
        else f"COALESCE(c.{rating_col} - ya.{rating_col}, 0) DESC, a.athlete_id ASC"
    )

    sql = f"""
        WITH current AS (
            SELECT DISTINCT ON (ra.athlete_id)
                   ra.athlete_id,
                   ra.overall, ra.swim, ra.bike, ra.run, ra.transition,
                   r.race_date AS last_race_date
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
        ),
        current_rank AS (
            SELECT DISTINCT ON (rk.athlete_id)
                   rk.athlete_id,
                   rk.world_overall, rk.world_swim, rk.world_bike,
                   rk.world_run, rk.world_transition
            FROM rankings rk
            JOIN races r ON rk.race_id = r.race_id
            ORDER BY rk.athlete_id, r.race_date DESC, rk.race_id DESC
        ),
        year_ago AS (
            SELECT DISTINCT ON (ra.athlete_id)
                   ra.athlete_id,
                   ra.overall, ra.swim, ra.bike, ra.run, ra.transition
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE r.race_date <= CURRENT_DATE - INTERVAL 1 YEAR
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
            n.alpha3    AS country_alpha3,
            n.emoji     AS country_emoji,
            a.country_full,
            a.profile_img,
            c.overall, c.swim, c.bike, c.run, c.transition,
            cr.world_overall, cr.world_swim, cr.world_bike, cr.world_run, cr.world_transition,
            c.last_race_date >= CURRENT_DATE - INTERVAL 1 YEAR AS active,
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


def get_athlete_current_ratings(athlete_id):
    """Latest rating + world/national ranking for all 5 disciplines."""
    conn = _get_conn()
    # Latest rating
    rating = conn.execute("""
        SELECT ra.overall, ra.swim, ra.bike, ra.run, ra.transition
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        WHERE ra.athlete_id = ?
        ORDER BY r.race_date DESC, ra.race_id DESC
        LIMIT 1
    """, [athlete_id]).fetchone()

    # Latest ranking
    ranking = conn.execute("""
        SELECT rk.world_overall, rk.world_swim, rk.world_bike, rk.world_run, rk.world_transition,
               rk.national_overall, rk.national_swim, rk.national_bike, rk.national_run, rk.national_transition
        FROM rankings rk
        JOIN races r ON rk.race_id = r.race_id
        WHERE rk.athlete_id = ?
        ORDER BY r.race_date DESC, rk.race_id DESC
        LIMIT 1
    """, [athlete_id]).fetchone()

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


def get_athlete_active_rankings(athlete_id):
    """
    Rank among currently active athletes (raced in last 12 months), same gender.
    Returns None if the athlete themselves is not active.
    """
    conn = _get_conn()
    row = conn.execute("""
        WITH current AS (
            SELECT DISTINCT ON (ra.athlete_id)
                   ra.athlete_id,
                   ra.overall, ra.swim, ra.bike, ra.run, ra.transition,
                   r.race_date AS last_race_date
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            ORDER BY ra.athlete_id, r.race_date DESC, ra.race_id DESC
        ),
        active AS (
            SELECT c.athlete_id, c.overall, c.swim, c.bike, c.run, c.transition,
                   a.country_full
            FROM current c
            JOIN athletes a ON c.athlete_id = a.athlete_id
            WHERE c.last_race_date >= CURRENT_DATE - INTERVAL 1 YEAR
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
    """, [athlete_id, athlete_id]).fetchone()

    if not row:
        return None
    cols = ["world_overall", "world_swim", "world_bike", "world_run", "world_transition",
            "national_overall", "national_swim", "national_bike", "national_run", "national_transition"]
    return dict(zip(cols, row))


def get_athlete_1yr_changes(athlete_id):
    """Rating change over the past year per discipline. None if no data."""
    conn = _get_conn()
    current = conn.execute("""
        SELECT ra.overall, ra.swim, ra.bike, ra.run, ra.transition
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        WHERE ra.athlete_id = ?
        ORDER BY r.race_date DESC, ra.race_id DESC
        LIMIT 1
    """, [athlete_id]).fetchone()

    if not current:
        return {k: None for k in
                ["overall_change_1yr", "swim_change_1yr", "bike_change_1yr",
                 "run_change_1yr", "transition_change_1yr"]}

    year_ago = conn.execute("""
        SELECT ra.overall, ra.swim, ra.bike, ra.run, ra.transition
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        WHERE ra.athlete_id = ?
          AND r.race_date <= CURRENT_DATE - INTERVAL 1 YEAR
        ORDER BY r.race_date DESC, ra.race_id DESC
        LIMIT 1
    """, [athlete_id]).fetchone()

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


def get_athlete_peak_ratings(athlete_id):
    """Max rating per discipline + race handle + race_id where it was achieved."""
    conn = _get_conn()
    discs = ["overall", "swim", "bike", "run", "transition"]
    result = {}
    for disc in discs:
        row = conn.execute(f"""
            SELECT ra.{disc}, r.race_handle, r.race_id
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE ra.athlete_id = ?
            ORDER BY ra.{disc} DESC
            LIMIT 1
        """, [athlete_id]).fetchone()
        result[f"max_{disc}"]         = row[0] if row else 0
        result[f"max_{disc}_race"]    = row[1] if row else ""
        result[f"max_{disc}_race_id"] = row[2] if row else 0
    return result


def get_athlete_best_performances(athlete_id):
    """Max positive rating change per discipline + the race handle."""
    conn = _get_conn()
    discs = ["overall", "swim", "bike", "run", "transition"]
    result = {}
    for disc in discs:
        row = conn.execute(f"""
            SELECT ra.{disc}_change, r.race_handle, ra.race_id
            FROM ratings ra
            JOIN races r ON ra.race_id = r.race_id
            WHERE ra.athlete_id = ?
              AND ra.{disc}_change > 0
            ORDER BY ra.{disc}_change DESC
            LIMIT 1
        """, [athlete_id]).fetchone()
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
    Returns list of dicts: {tier, position, race_id, race_handle, race_title}
    grouped and categorised using the same logic as stats/athlete.py.
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

        # AG events are not notable
        if _AG_CAT_ID in cat_ids:
            continue

        # Major Games (343): only Olympic if "olympic" in title, else treat as continental champ (340)
        if 343 in cat_ids:
            if "olympic" in race_title.lower():
                notable.append({"tier": "olympic", "position": position,
                                 "race_id": race_id, "race_handle": race_handle, "race_date": race_date})
                continue
            else:
                cat_ids.discard(343)
                cat_ids.add(340)

        if 624 in cat_ids or 348 in cat_ids:
            notable.append({"tier": "world_champs", "position": position,
                             "race_id": race_id, "race_handle": race_handle, "race_date": race_date})
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


def get_athlete_stats(athlete_id):
    """race_starts, podiums (pos 1-3), wins (pos 1), last_race_date."""
    conn = _get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*)                                             AS race_starts,
            COUNT(CASE WHEN res.position <= 3 THEN 1 END)       AS podiums,
            COUNT(CASE WHEN res.position = 1  THEN 1 END)       AS wins,
            MAX(r.race_date)                                     AS last_race_date
        FROM results res
        JOIN races r ON res.race_id = r.race_id
        WHERE res.athlete_id = ?
    """, [athlete_id]).fetchone()
    return {"race_starts": row[0], "podiums": row[1], "wins": row[2], "last_race_date": row[3]}


def get_athlete_race_history(athlete_id):
    """
    All race results for an athlete with splits and behind-leader times.
    Behind time = time - fastest non-zero time in that race (NULL if DNF/0).
    Returns list of dicts ordered by race_date desc.
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            res.race_id,
            r.race_title,
            r.race_date,
            r.prog_name,
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
            CASE WHEN res.t2_s      > 0 THEN res.t2_s      - w.min_t2      END AS t2_behind_s
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
        WHERE res.athlete_id = ?
        ORDER BY r.race_date DESC, res.race_id DESC
    """, [athlete_id]).fetchall()

    cols = [
        "race_id", "race_title", "race_date", "program", "position", "status",
        "overall_s", "swim_s", "bike_s", "run_s", "t1_s", "t2_s",
        "overall_behind_s", "swim_behind_s", "bike_behind_s", "run_behind_s",
        "t1_behind_s", "t2_behind_s",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_athlete_rating_history(athlete_id):
    """
    All rating entries for an athlete with race info and finish position.
    Returns list of dicts ordered by race_date desc.
    """
    conn = _get_conn()
    rows = conn.execute("""
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
        WHERE ra.athlete_id = ?
        ORDER BY r.race_date DESC, ra.race_id DESC
    """, [athlete_id]).fetchall()

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
    Raw times + pct-behind-leader per race, for chart rendering.
    pct_behind = (time - fastest) / fastest, or None if time is 0.
    Returns list of dicts ordered by race_date asc (chronological for charts).
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            res.race_id,
            r.race_date,
            r.race_title,
            res.overall_s,
            res.swim_s,
            res.bike_s,
            res.run_s,
            -- pct behind leader — computed against all athletes in each race
            CASE WHEN res.overall_s > 0 THEN (res.overall_s - w.min_overall) / w.min_overall END AS overall_pct_behind,
            CASE WHEN res.swim_s    > 0 THEN (res.swim_s    - w.min_swim)    / w.min_swim    END AS swim_pct_behind,
            CASE WHEN res.bike_s    > 0 THEN (res.bike_s    - w.min_bike)    / w.min_bike    END AS bike_pct_behind,
            CASE WHEN res.run_s     > 0 THEN (res.run_s     - w.min_run)     / w.min_run     END AS run_pct_behind
        FROM results res
        JOIN races r ON res.race_id = r.race_id
        JOIN (
            SELECT race_id,
                   MIN(CASE WHEN overall_s > 0 THEN overall_s END) AS min_overall,
                   MIN(CASE WHEN swim_s    > 0 THEN swim_s    END) AS min_swim,
                   MIN(CASE WHEN bike_s    > 0 THEN bike_s    END) AS min_bike,
                   MIN(CASE WHEN run_s     > 0 THEN run_s     END) AS min_run
            FROM results
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


def get_athlete_ratings_data(athlete_id):
    """
    Raw ratings per race for chart rendering (chronological order).
    Returns list of dicts ordered by race_date asc.
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            ra.race_id,
            r.race_date,
            r.race_title,
            ra.overall, ra.swim, ra.bike, ra.run, ra.transition
        FROM ratings ra
        JOIN races r ON ra.race_id = r.race_id
        WHERE ra.athlete_id = ?
        ORDER BY r.race_date ASC, ra.race_id ASC
    """, [athlete_id]).fetchall()

    cols = ["race_id", "race_date", "race_title",
            "overall_rating", "swim_rating", "bike_rating", "run_rating", "transition_rating"]
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
        SELECT race_id, race_title, prog_name, race_date, gender
        FROM races
        WHERE event_id = ?
        ORDER BY race_date ASC
    """, [event_id]).fetchall()
    cols = ["race_id", "race_title", "prog_name", "race_date", "gender"]
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Race page
# ---------------------------------------------------------------------------

def get_race_info(race_id):
    """Basic race info."""
    conn = _get_conn()
    row = conn.execute("""
        SELECT r.race_id, r.race_title, r.prog_name, r.race_date, 
               e.venue AS location, e.country, r.gender, r.race_handle, r.event_id
        FROM races r
        JOIN events e ON r.event_id = e.event_id
        WHERE r.race_id = ?
    """, [race_id]).fetchone()
    if not row:
        return None
    cols = ["race_id", "race_title", "prog_name", "race_date",
            "location", "country", "gender", "race_handle", "event_id"]
    return dict(zip(cols, row))


def get_race_results(race_id):
    """
    All results for a race with athlete info and behind-leader times.
    Returns list of dicts ordered by position (nulls last for DNFs).
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            res.athlete_id,
            res.position,
            res.status,
            res.overall_s, res.swim_s, res.bike_s, res.run_s, res.t1_s, res.t2_s,
            a.name, a.year_of_birth,
            n.alpha3  AS country_alpha3,
            n.emoji   AS country_emoji,
            -- behind times
            CASE WHEN res.overall_s > 0 THEN
                res.overall_s - MIN(CASE WHEN res.overall_s > 0 THEN res.overall_s END) OVER ()
            END AS overall_behind_s,
            CASE WHEN res.swim_s > 0 THEN
                res.swim_s - MIN(CASE WHEN res.swim_s > 0 THEN res.swim_s END) OVER ()
            END AS swim_behind_s,
            CASE WHEN res.bike_s > 0 THEN
                res.bike_s - MIN(CASE WHEN res.bike_s > 0 THEN res.bike_s END) OVER ()
            END AS bike_behind_s,
            CASE WHEN res.run_s > 0 THEN
                res.run_s - MIN(CASE WHEN res.run_s > 0 THEN res.run_s END) OVER ()
            END AS run_behind_s,
            CASE WHEN res.t1_s > 0 THEN
                res.t1_s - MIN(CASE WHEN res.t1_s > 0 THEN res.t1_s END) OVER ()
            END AS t1_behind_s,
            CASE WHEN res.t2_s > 0 THEN
                res.t2_s - MIN(CASE WHEN res.t2_s > 0 THEN res.t2_s END) OVER ()
            END AS t2_behind_s
        FROM results res
        JOIN athletes a    ON res.athlete_id = a.athlete_id
        JOIN nationalities n ON a.country_full = n.country_full
        WHERE res.race_id = ?
        ORDER BY res.position NULLS LAST
    """, [race_id]).fetchall()

    cols = [
        "athlete_id", "position", "status",
        "overall_s", "swim_s", "bike_s", "run_s", "t1_s", "t2_s",
        "name", "year_of_birth", "country_alpha3", "country_emoji",
        "overall_behind_s", "swim_behind_s", "bike_behind_s", "run_behind_s",
        "t1_behind_s", "t2_behind_s",
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


def get_race_standards(race_id):
    """Average rating of the top 10 finishers per discipline."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT ra.overall, ra.swim, ra.bike, ra.run, ra.transition
        FROM ratings ra
        JOIN results res ON ra.race_id = res.race_id AND ra.athlete_id = res.athlete_id
        WHERE ra.race_id = ?
          AND res.status = 'Finished'
          AND res.position IS NOT NULL
        ORDER BY res.position ASC
        LIMIT 10
    """, [race_id]).fetchall()

    if not rows:
        return {d: 0.0 for d in ["overall", "swim", "bike", "run", "transition"]}

    n = len(rows)
    return {
        "overall":    sum(r[0] for r in rows) / n,
        "swim":       sum(r[1] for r in rows) / n,
        "bike":       sum(r[2] for r in rows) / n,
        "run":        sum(r[3] for r in rows) / n,
        "transition": sum(r[4] for r in rows) / n,
    }


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
    Returns dict of discipline -> list of non-zero seconds values.
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT overall_s, swim_s, bike_s, run_s, t1_s, t2_s
        FROM results
        WHERE race_id = ? AND overall_s > 0
    """, [race_id]).fetchall()

    result = {"overall": [], "swim": [], "bike": [], "run": [], "t1": [], "t2": []}
    for r in rows:
        if r[0] > 0: result["overall"].append(r[0])
        if r[1] > 0: result["swim"].append(r[1])
        if r[2] > 0: result["bike"].append(r[2])
        if r[3] > 0: result["run"].append(r[3])
        if r[4] > 0: result["t1"].append(r[4])
        if r[5] > 0: result["t2"].append(r[5])
    return result


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


def get_athlete_rankings_data(athlete_id):
    """
    World and national rankings per race for chart rendering (chronological order).
    Returns list of dicts ordered by race_date asc.
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            rk.race_id,
            r.race_date,
            r.race_title,
            rk.world_overall,    rk.world_swim,    rk.world_bike,    rk.world_run,    rk.world_transition,
            rk.national_overall, rk.national_swim, rk.national_bike, rk.national_run, rk.national_transition
        FROM rankings rk
        JOIN races r ON rk.race_id = r.race_id
        WHERE rk.athlete_id = ?
        ORDER BY r.race_date ASC, rk.race_id ASC
    """, [athlete_id]).fetchall()

    cols = ["race_id", "race_date", "race_title",
            "world_overall",    "world_swim",    "world_bike",    "world_run",    "world_transition",
            "national_overall", "national_swim", "national_bike", "national_run", "national_transition"]
    return [dict(zip(cols, r)) for r in rows]
