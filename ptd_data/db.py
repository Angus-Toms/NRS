import csv
import datetime as _dt
import pathlib
import zlib

import duckdb
import pycountry

from config import DB_PATH

_DATA_DIR = pathlib.Path(__file__).parent / 'data'


def get_conn(read_only=False):
    conn = duckdb.connect(str(DB_PATH), read_only=read_only)
    if not read_only:
        create_schema(conn)
    return conn


def create_schema(conn):
    conn.execute("CREATE TYPE IF NOT EXISTS gender_enum AS ENUM ('male', 'female')")
    conn.execute("CREATE TYPE IF NOT EXISTS category_enum AS ENUM ('elite', 'ag')")
    conn.execute(
        "CREATE TYPE IF NOT EXISTS category_sub_enum AS ENUM "
        "('elite', 'u23', 'junior', 'youth', 'ag')"
    )
    conn.execute(
        "CREATE TYPE IF NOT EXISTS result_status_enum AS ENUM "
        "('Finished', 'DNF', 'DNS', 'DQ', 'LAP', 'NC')"
    )
    conn.execute(
        "CREATE TYPE IF NOT EXISTS continent_enum AS ENUM "
        "('Americas', 'Europe', 'Asia', 'Africa', 'Oceania', 'Other')"
    )
    conn.execute(
        "CREATE TYPE IF NOT EXISTS distance_enum AS ENUM "
        "('sprint', 'standard', 'middle', 't100', 'long')"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS nationalities (
            country_full    VARCHAR PRIMARY KEY,
            alpha3          VARCHAR NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS athletes (
            athlete_id      INTEGER PRIMARY KEY,
            name            VARCHAR NOT NULL,
            -- Current country snapshot (matches the NULL end_date row in
            -- athlete_nationality_history). Kept as a cache so hot queries
            -- like result lists don't need a join for every row.
            country_full    VARCHAR NOT NULL REFERENCES nationalities(country_full),
            year_of_birth   INTEGER NOT NULL DEFAULT 0,
            profile_img     VARCHAR NOT NULL DEFAULT '',
            gender          gender_enum NOT NULL,
            pto_slug        VARCHAR,
            height_cm       INTEGER,
            weight_kg       INTEGER,
            nickname        VARCHAR NOT NULL DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS series (
            series_id       INTEGER PRIMARY KEY,
            slug            VARCHAR UNIQUE NOT NULL,
            name            VARCHAR NOT NULL,
            tier            VARCHAR NOT NULL DEFAULT 'custom',
            continent       VARCHAR NOT NULL DEFAULT '',
            sort_order      INTEGER NOT NULL DEFAULT 100,
            description     VARCHAR NOT NULL DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS recurring_events (
            recurring_event_id  INTEGER PRIMARY KEY,
            slug                VARCHAR UNIQUE NOT NULL,
            name                VARCHAR NOT NULL,
            venue_key           VARCHAR NOT NULL DEFAULT '',
            description         VARCHAR NOT NULL DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id            INTEGER PRIMARY KEY,
            name                VARCHAR NOT NULL,
            venue               VARCHAR NOT NULL DEFAULT '',
            country             VARCHAR NOT NULL DEFAULT '',
            continent           continent_enum NOT NULL DEFAULT 'Other',
            start_date          DATE NOT NULL,
            end_date            DATE NOT NULL,
            longitude           DOUBLE NOT NULL DEFAULT 0,
            latitude            DOUBLE NOT NULL DEFAULT 0,
            brand               VARCHAR NOT NULL DEFAULT '',
            prize_money_usd     INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_recurring (
            event_id           INTEGER PRIMARY KEY REFERENCES events(event_id),
            recurring_event_id INTEGER NOT NULL REFERENCES recurring_events(recurring_event_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS races (
            race_id         INTEGER PRIMARY KEY,
            event_id        INTEGER NOT NULL REFERENCES events(event_id),
            race_title      VARCHAR NOT NULL,
            prog_name       VARCHAR NOT NULL,
            race_date       DATE NOT NULL,
            gender          gender_enum NOT NULL,
            category        category_enum NOT NULL DEFAULT 'elite',
            sub_category    category_sub_enum NOT NULL DEFAULT 'ag',
            cat_ids         VARCHAR NOT NULL DEFAULT '[]',
            race_handle     VARCHAR NOT NULL DEFAULT '',
            event_spec_ids  VARCHAR NOT NULL DEFAULT '[]',
            is_multi_stage  BOOLEAN NOT NULL DEFAULT FALSE,
            distance        distance_enum NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS results (
            race_id         INTEGER NOT NULL REFERENCES races(race_id),
            -- athlete_id intentionally NOT FK-constrained: DuckDB rejects any
            -- UPDATE on a parent row that's FK-referenced (even if the PK
            -- isn't changing), which blocks updating cached fields like
            -- athletes.country_full. Application code maintains integrity.
            athlete_id      INTEGER NOT NULL,
            position        INTEGER,
            status          result_status_enum NOT NULL,
            start_num       INTEGER NOT NULL DEFAULT 0,
            overall_s       DOUBLE NOT NULL DEFAULT 0,
            swim_s          DOUBLE NOT NULL DEFAULT 0,
            bike_s          DOUBLE NOT NULL DEFAULT 0,
            run_s           DOUBLE NOT NULL DEFAULT 0,
            t1_s            DOUBLE NOT NULL DEFAULT 0,
            t2_s            DOUBLE NOT NULL DEFAULT 0,
            pto_points      DOUBLE NOT NULL DEFAULT 0,
            PRIMARY KEY (race_id, athlete_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ignored_races (
            race_id        INTEGER PRIMARY KEY REFERENCES races(race_id),
            reason         VARCHAR NOT NULL,
            parent_race_id INTEGER REFERENCES races(race_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS corrections (
            race_id     INTEGER NOT NULL,
            athlete_id  INTEGER NOT NULL,
            discipline  VARCHAR NOT NULL,  -- 'overall'|'swim'|'bike'|'run'|'t1'|'t2'
            value       DOUBLE  NOT NULL,  -- 0 means "ignore this split in ELO"
            source      VARCHAR NOT NULL DEFAULT 'manual',  -- 'manual' | 'auto'
            reason      VARCHAR NOT NULL DEFAULT '',
            PRIMARY KEY (race_id, athlete_id, discipline, source)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            race_id             INTEGER NOT NULL REFERENCES races(race_id),
            athlete_id          INTEGER NOT NULL,  -- see results.athlete_id note
            category            category_enum NOT NULL,
            overall             DOUBLE NOT NULL DEFAULT 0,
            swim                DOUBLE NOT NULL DEFAULT 0,
            bike                DOUBLE NOT NULL DEFAULT 0,
            run                 DOUBLE NOT NULL DEFAULT 0,
            transition          DOUBLE NOT NULL DEFAULT 0,
            overall_change      DOUBLE NOT NULL DEFAULT 0,
            swim_change         DOUBLE NOT NULL DEFAULT 0,
            bike_change         DOUBLE NOT NULL DEFAULT 0,
            run_change          DOUBLE NOT NULL DEFAULT 0,
            transition_change   DOUBLE NOT NULL DEFAULT 0,
            PRIMARY KEY (race_id, athlete_id, category)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rankings (
            race_id                 INTEGER NOT NULL REFERENCES races(race_id),
            athlete_id              INTEGER NOT NULL,  -- see results.athlete_id note
            category                category_enum NOT NULL,
            world_overall           INTEGER NOT NULL,
            world_swim              INTEGER NOT NULL,
            world_bike              INTEGER NOT NULL,
            world_run               INTEGER NOT NULL,
            world_transition        INTEGER NOT NULL,
            national_overall        INTEGER NOT NULL,
            national_swim           INTEGER NOT NULL,
            national_bike           INTEGER NOT NULL,
            national_run            INTEGER NOT NULL,
            national_transition     INTEGER NOT NULL,
            active_world_overall    INTEGER,
            active_world_swim       INTEGER,
            active_world_bike       INTEGER,
            active_world_run        INTEGER,
            active_world_transition INTEGER,
            PRIMARY KEY (race_id, athlete_id, category)
        )
    """)

    # Race rankings: each race ranked vs all other races of the same
    # (gender, course) by its pre-race standard (EXP-weighted average of
    # finishers' pre-race ratings). Standards/ranks per discipline are NULL
    # when no finisher in the race had a split for that discipline.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS race_rankings (
            race_id              INTEGER PRIMARY KEY REFERENCES races(race_id),
            gender               gender_enum NOT NULL,
            course               VARCHAR NOT NULL,
            overall_std          DOUBLE,
            swim_std             DOUBLE,
            bike_std             DOUBLE,
            run_std              DOUBLE,
            transition_std       DOUBLE,
            overall_rank         INTEGER,
            swim_rank            INTEGER,
            bike_rank            INTEGER,
            run_rank             INTEGER,
            transition_rank      INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS prediction_models (
            gender      gender_enum NOT NULL,
            distance    VARCHAR NOT NULL,   -- 'sprint' | 'standard'
            discipline  VARCHAR NOT NULL,   -- 'overall' | 'swim' | 'bike' | 'run' | 'transition'
            slope       DOUBLE NOT NULL,
            intercept   DOUBLE NOT NULL,
            n_samples   INTEGER NOT NULL,
            year_coef   DOUBLE NOT NULL DEFAULT 0,   -- seconds/year era drift for long course
            PRIMARY KEY (gender, distance, discipline)
        )
    """)
    # Drop rating_cap if it exists on older DBs — empirically useless in the
    # residual sweep and replaced by per-athlete history anchoring.
    conn.execute("ALTER TABLE prediction_models DROP COLUMN IF EXISTS rating_cap")
    # Idempotent migration for DBs created before year_coef existed. Nullable
    # because DuckDB ALTER can't add NOT NULL + DEFAULT in one step; readers
    # COALESCE to 0 so pre-migration rows act as "no year term".
    conn.execute("ALTER TABLE prediction_models ADD COLUMN IF NOT EXISTS year_coef DOUBLE")

    # Form model state (see ptd_data/form.py). athlete_form mirrors the
    # ratings table's temporal semantics: one row per observation holding the
    # athlete's blended form *after* that race, so "latest row strictly
    # before date X" is the leakage-free pre-race form at X.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS athlete_form (
            race_id     INTEGER NOT NULL REFERENCES races(race_id),
            athlete_id  INTEGER NOT NULL,  -- see results.athlete_id note
            discipline  VARCHAR NOT NULL,  -- 'overall' | 'swim' | 'bike' | 'run'
            form_rel    DOUBLE NOT NULL,   -- blended Kalman+Elo form, rel space
            n_obs       INTEGER NOT NULL,  -- observations to date incl this race
            PRIMARY KEY (race_id, athlete_id, discipline)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS form_race_constants (
            race_id     INTEGER NOT NULL REFERENCES races(race_id),
            discipline  VARCHAR NOT NULL,
            c           DOUBLE NOT NULL,   -- ln(median_split) + ALS field adj
            PRIMARY KEY (race_id, discipline)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS upcoming_races (
            race_id         INTEGER PRIMARY KEY,
            event_id        INTEGER NOT NULL REFERENCES events(event_id),
            race_title      VARCHAR NOT NULL,
            prog_name       VARCHAR NOT NULL,
            race_date       DATE NOT NULL,
            gender          gender_enum NOT NULL,
            category        category_enum NOT NULL DEFAULT 'elite',
            cat_ids         VARCHAR NOT NULL DEFAULT '[]',
            race_handle     VARCHAR NOT NULL DEFAULT '',
            event_spec_ids  VARCHAR NOT NULL DEFAULT '[]',
            last_fetched    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS start_list_entries (
            race_id         INTEGER NOT NULL REFERENCES upcoming_races(race_id),
            athlete_id      INTEGER NOT NULL,  -- see results.athlete_id note
            start_num       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (race_id, athlete_id)
        )
    """)

    # Nationality history: one row per contiguous period the athlete
    # represented a country. end_date IS NULL for the country currently
    # represented. Maintained incrementally during ingest (see
    # record_athlete_nationality below).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS athlete_nationality_history (
            athlete_id    INTEGER NOT NULL,  -- see results.athlete_id note
            country_full  VARCHAR NOT NULL REFERENCES nationalities(country_full),
            start_date    DATE NOT NULL,
            end_date      DATE,
            PRIMARY KEY (athlete_id, country_full, start_date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_series (
            event_id  INTEGER NOT NULL REFERENCES events(event_id),
            series_id INTEGER NOT NULL REFERENCES series(series_id),
            PRIMARY KEY (event_id, series_id)
        )
    """)

    # Social posts: one row per (race, post_type) once successfully published.
    # The social scheduler diffs candidate races against this to avoid re-posting.
    # post_type is 'pre_race' (predictions) or 'post_race' (recap). No FK on
    # race_id - pre_race rows reference upcoming_races, post_race rows reference
    # races, and a race graduates from one table to the other over time.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS social_posts (
            race_id     BIGINT      NOT NULL,
            post_type   VARCHAR     NOT NULL,
            posted_at   TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ig_post_id  VARCHAR,
            fb_post_ids VARCHAR,
            PRIMARY KEY (race_id, post_type)
        )
    """)

    # ART indexes on non-PK columns used in WHERE/JOIN clauses
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(start_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_series_series_id ON event_series(series_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_races_event_id ON races(event_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_races_race_date ON races(race_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_athletes_country_full ON athletes(country_full)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_anh_athlete_id ON athlete_nationality_history(athlete_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_anh_country ON athlete_nationality_history(country_full)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_results_athlete_id ON results(athlete_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ratings_athlete_id ON ratings(athlete_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_athlete_form_athlete ON athlete_form(athlete_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rankings_athlete_id ON rankings(athlete_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_race_rankings_gc ON race_rankings(gender, course)")


# --- Country resolution ---

_COUNTRY_SPECIAL_CASES = {
    "Individual Neutral Athlete": "INA",
    "Great Britain": "GBR",
    "Republic of Korea": "KOR",
    "Czech Republic": "CZE",
    "Hong Kong, China": "HKG",
    "Russia": "RUS",
    "Syria": "SYR",
    "Macau, China": "MAC",
    "Venezuela": "VEN",
    "Chinese Taipei": "TPE",
    "Virgin Islands": "ISV",
    "Tahiti": "PYF",
    "Bolivia": "BOL",
    "Moldova": "MDA",
    "Saint Maarten": "SXM",
    "Czechoslovakia": "CSK",
    "Iran": "IRN",
    "Netherlands Antilles": "ANT",
    "Swaziland": "SWZ",
    # Historical German states (pre-1990). Kept distinct from modern "Germany"
    # (DEU) so each athlete's alpha3/URL matches the flag they competed under.
    "Federal Republic of Germany":   "FRG",  # West Germany 1949-1990
    "Democratic Republic of Germany": "GDR",  # East Germany 1949-1990
    # Other historical / non-standard entities
    "Soviet Union":                    "URS",
    "Yugoslavia":                      "YUG",
    "Myanmar (Burma)":                 "MMR",
    "Cote d'Ivoire":                   "CIV",
    "Vietnam":                         "VNM",
    "United Republic of Tanzania":     "TZA",
    "The Gambia":                      "GMB",
    "Palestine":                       "PSE",
    "Democratic People's Republic of Korea": "PRK",
    # Federation / neutral banner
    "World Triathlon":                 "WTR",
    # Home nations, sports-only entities, and disputed territories that
    # pycountry can't resolve (all otherwise fall through to "UNK").
    "Scotland":                        "SCO",
    "SCOTLAND":                        "SCO",
    "England":                         "ENG",
    "ENGLAND":                         "ENG",
    "Wales":                           "WAL",
    "WALES":                           "WAL",
    "Northern Ireland":                "NIR",  # Commonwealth Games: Ulster Banner
    "Russian Triathlon Federation":    "RUS",
    "Russian Olympic Committee":       "ROC",
    "Kosovo":                          "KOS",
    # Alt spellings pycountry doesn't recognise (a few stray history rows each).
    "USA":                             "USA",
    "Korea, South":                    "KOR",
}


def _resolve_country(country_full):
    """Returns alpha3 for a country name."""
    if country_full in _COUNTRY_SPECIAL_CASES:
        return _COUNTRY_SPECIAL_CASES[country_full]
    country = pycountry.countries.get(name=country_full)
    if country is None:
        return "UNK"
    return country.alpha_3


# --- Write methods ---

def upsert_nationality(conn, country_full):
    """Insert nationality if it doesn't exist."""
    exists = conn.execute(
        "SELECT 1 FROM nationalities WHERE country_full = ?", [country_full]
    ).fetchone()
    if exists:
        return
    alpha3 = _resolve_country(country_full)
    conn.execute(
        "INSERT INTO nationalities (country_full, alpha3) VALUES (?, ?)",
        [country_full, alpha3],
    )


def upsert_athlete(conn, athlete_id, name, country_full, year_of_birth, profile_img, gender):
    """Insert or update the WT-sourced athlete fields.

    INSERT OR REPLACE in DuckDB only updates columns listed (unlike SQLite's
    full-row replace), so PTO-sourced fields (pto_slug, height_cm, weight_kg,
    nickname) are preserved across WT re-ingest.

    Note: athletes must have a single UNIQUE key (the PK) — adding a second
    UNIQUE constraint breaks INSERT OR REPLACE with a multi-conflict binder
    error, and plain UPDATE on an FK-referenced row fails if country_full
    (itself an outgoing FK) is in the SET list. DuckDB FK-checker quirks.
    """
    conn.execute(
        """
        INSERT OR REPLACE INTO athletes
            (athlete_id, name, country_full, year_of_birth, profile_img, gender)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [athlete_id, name, country_full, year_of_birth, profile_img, gender],
    )


def insert_race(conn, race_id, event_id, race_title, prog_name, race_date, gender, category, sub_category, cat_ids, distance, race_handle='', event_spec_ids='[]'):
    """Insert a race, skip if it already exists."""
    conn.execute(
        """
        INSERT OR IGNORE INTO races
            (race_id, event_id, race_title, prog_name, race_date, gender, category, sub_category,
             cat_ids, race_handle, event_spec_ids, distance)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [race_id, event_id, race_title, prog_name, race_date, gender, category, sub_category,
         cat_ids, race_handle, event_spec_ids, distance],
    )


def insert_event(conn, event_id, name, venue, country, continent, start_date, end_date, longitude, latitude, brand='', prize_money_usd=0):
    """Insert an event, skip if it already exists."""
    conn.execute(
        """
        INSERT OR IGNORE INTO events
            (event_id, name, venue, country, continent, start_date, end_date, longitude, latitude,
             brand, prize_money_usd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [event_id, name, venue, country, continent, start_date, end_date, longitude, latitude,
         brand, prize_money_usd],
    )


def upsert_athlete_pto_fields(conn, athlete_id, pto_slug, height_cm, weight_kg, nickname):
    """Update the PTO-sourced fields for an existing athlete row."""
    conn.execute(
        """
        UPDATE athletes
        SET pto_slug  = ?,
            height_cm = ?,
            weight_kg = ?,
            nickname  = ?
        WHERE athlete_id = ?
        """,
        [pto_slug, height_cm, weight_kg, nickname, athlete_id],
    )


def _coerce_date(v):
    """Accept a date or ISO-8601 string; return a datetime.date."""
    if isinstance(v, _dt.date):
        return v
    return _dt.date.fromisoformat(str(v)[:10])


def record_athlete_nationality(conn, athlete_id, country_full, race_date):
    """Maintain athlete_nationality_history incrementally from an ingest observation.

    Assumes ingest is roughly chronological (which it is: WT paginates newest-
    first over completed events, PTO iterates years). Three cases:

      - No prior history: open the first range.
      - Latest range already matches this country: no-op (and roll start_date
        back if we're observing a race older than the range we've opened).
      - Country differs AND this race is newer than the open range: close
        the open range at race_date and open a new range under the new country.
        Races older than the current open range are ignored to avoid corrupting
        an established timeline.
    """
    race_date = _coerce_date(race_date)
    row = conn.execute("""
        SELECT country_full, start_date
        FROM athlete_nationality_history
        WHERE athlete_id = ?
        ORDER BY start_date DESC
        LIMIT 1
    """, [athlete_id]).fetchone()

    if row is None:
        conn.execute("""
            INSERT INTO athlete_nationality_history
                (athlete_id, country_full, start_date, end_date)
            VALUES (?, ?, ?, NULL)
        """, [athlete_id, country_full, race_date])
        return

    latest_country, latest_start = row
    if country_full == latest_country:
        if race_date < latest_start:
            conn.execute("""
                UPDATE athlete_nationality_history
                SET start_date = ?
                WHERE athlete_id = ? AND start_date = ?
            """, [race_date, athlete_id, latest_start])
        return

    if race_date <= latest_start:
        # Out-of-order older race under a different country — skip.
        return

    conn.execute("""
        UPDATE athlete_nationality_history
        SET end_date = ?
        WHERE athlete_id = ? AND end_date IS NULL
    """, [race_date, athlete_id])
    conn.execute("""
        INSERT INTO athlete_nationality_history
            (athlete_id, country_full, start_date, end_date)
        VALUES (?, ?, ?, NULL)
    """, [athlete_id, country_full, race_date])


def apply_athlete_merges(conn):
    """Apply manual athlete merges from data/athlete_merges.csv.

    Used when WT and PTO emit two separate athlete rows for the same person and
    the overlap-race auto matcher (ptd_data.pto_matcher) can't bridge them
    (e.g. an athlete who only ever raced short course on one platform and long
    course on the other shares no finisher times). Each row says: collapse
    `merge_athlete_id` into `keep_athlete_id`.

    Per pair we:
      1. Fill missing PTO-only fields (pto_slug/height/weight/nickname) on keep.
      2. Re-point all athlete-keyed FK rows from merge to keep, dropping any
         rows that would collide on the keep's primary key.
      3. Delete the merge athlete row.

    Idempotent: re-running after the merge row has been deleted is a no-op.
    """
    path = _DATA_DIR / 'athlete_merges.csv'
    if not path.exists():
        return

    pairs = []
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            try:
                pairs.append((int(row['keep_athlete_id']), int(row['merge_athlete_id'])))
            except (KeyError, ValueError, TypeError):
                continue

    applied = 0
    for keep_id, merge_id in pairs:
        merge_row = conn.execute(
            "SELECT pto_slug, height_cm, weight_kg, nickname FROM athletes WHERE athlete_id = ?",
            [merge_id],
        ).fetchone()
        if merge_row is None:
            continue  # Already merged on a previous run.
        keep_row = conn.execute(
            "SELECT pto_slug, height_cm, weight_kg, nickname FROM athletes WHERE athlete_id = ?",
            [keep_id],
        ).fetchone()
        if keep_row is None:
            print(f"  [merge] keep_id={keep_id} not found - skipping merge of {merge_id}")
            continue

        new_slug   = keep_row[0] or merge_row[0]
        new_height = keep_row[1] or merge_row[1]
        new_weight = keep_row[2] or merge_row[2]
        new_nick   = keep_row[3] or merge_row[3]
        if (new_slug, new_height, new_weight, new_nick) != keep_row:
            conn.execute(
                "UPDATE athletes SET pto_slug=?, height_cm=?, weight_kg=?, nickname=? WHERE athlete_id=?",
                [new_slug, new_height, new_weight, new_nick, keep_id],
            )

        # Re-point athlete_id in tables sharing a (race_id, athlete_id, …) PK.
        # The NOT-EXISTS clause keeps any keep-side row that already covers the
        # same key; the residual merge-side row gets dropped below.
        for table, key_cols in [
            ("results",                     ("race_id",)),
            ("ratings",                     ("race_id", "category")),
            ("rankings",                    ("race_id", "category")),
            ("start_list_entries",          ("race_id",)),
            ("corrections",                 ("race_id", "discipline", "source")),
        ]:
            on_clause = " AND ".join(f"k.{c} = m.{c}" for c in key_cols)
            conn.execute(f"""
                UPDATE {table} m
                SET athlete_id = ?
                WHERE m.athlete_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM {table} k
                      WHERE k.athlete_id = ? AND {on_clause}
                  )
            """, [keep_id, merge_id, keep_id])
            conn.execute(f"DELETE FROM {table} WHERE athlete_id = ?", [merge_id])

        # Nationality history: keep is canonical (built from its own ingest
        # observations). Drop the merge's history rather than splice in.
        conn.execute("DELETE FROM athlete_nationality_history WHERE athlete_id = ?", [merge_id])

        # Finally remove the orphan athlete row.
        conn.execute("DELETE FROM athletes WHERE athlete_id = ?", [merge_id])
        applied += 1

    print(f"Applied {applied} athlete merge(s) "
          f"({len(pairs) - applied} already-applied or skipped)")


def reconcile_athlete_nationality(conn):
    """Sync athletes.country_full to the latest athlete_nationality_history row.

    Per-race upserts can leave the country_full cache stale: WT short-course and
    PTO long-course ingests run in separate chronological passes, so the very
    last upsert may carry an *older* country than the athlete's true latest
    observation. This rebuilds the cache from history, which is correctly
    timeline-tracked.

    Uses a plain UPDATE; the FK constraints from results/ratings/rankings/etc
    that used to make this a no-op were dropped (see results.athlete_id note
    in the schema).
    """
    rows = conn.execute("""
        WITH latest AS (
            SELECT athlete_id, country_full,
                   ROW_NUMBER() OVER (PARTITION BY athlete_id ORDER BY start_date DESC) AS rn
            FROM athlete_nationality_history
        )
        SELECT a.athlete_id, l.country_full
        FROM athletes a
        JOIN latest l ON l.athlete_id = a.athlete_id AND l.rn = 1
        WHERE a.country_full <> l.country_full
    """).fetchall()

    if not rows:
        print("Nationality cache already in sync.")
        return 0

    for athlete_id, country_full in rows:
        conn.execute(
            "UPDATE athletes SET country_full = ? WHERE athlete_id = ?",
            [country_full, athlete_id],
        )
    print(f"Reconciled country_full for {len(rows)} athlete(s).")
    return len(rows)


def insert_results_bulk(conn, rows):
    """Batch insert results. Each row is a tuple matching the results columns.

    (race_id, athlete_id, position, status, start_num,
     overall_s, swim_s, bike_s, run_s, t1_s, t2_s)
    """
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR IGNORE INTO results
            (race_id, athlete_id, position, status, start_num,
             overall_s, swim_s, bike_s, run_s, t1_s, t2_s)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def clear_all(conn):
    """Drop all data for a full recompute."""
    for table in ("results", "races", "athletes", "nationalities", "event_recurring"):
        conn.execute(f"DELETE FROM {table}")


def backfill_sub_category(conn):
    """Set races.sub_category from prog_name for any rows still at the default.

    Idempotent: always computes from prog_name, overwriting whatever is there.
    Cheap full-table update; only meaningful on the first run after schema bump.
    """
    conn.execute("""
        UPDATE races SET sub_category = CASE
            WHEN LOWER(SPLIT_PART(prog_name, ' ', 1)) = 'elite'  THEN 'elite'
            WHEN LOWER(SPLIT_PART(prog_name, ' ', 1)) = 'u23'    THEN 'u23'
            WHEN LOWER(SPLIT_PART(prog_name, ' ', 1)) = 'junior' THEN 'junior'
            WHEN LOWER(SPLIT_PART(prog_name, ' ', 1)) = 'youth'  THEN 'youth'
            -- Long-course "Pro Men" / "Pro Women" are an elite field; they
            -- otherwise split out from "Elite Men" rows when both end up on
            -- the same recurring event page (Apfelland, Challenge etc.).
            WHEN LOWER(SPLIT_PART(prog_name, ' ', 1)) = 'pro'    THEN 'elite'
            ELSE 'ag'
        END
    """)


def _parse_csv_time(time_str):
    """Parse HH:MM:SS or MM:SS string to seconds. Returns 0.0 for empty/zero."""
    if not time_str or not time_str.strip():
        return 0.0
    try:
        parts = time_str.strip().split(':')
        if len(parts) == 3:
            h, m, s = map(float, parts)
            return h * 3600 + m * 60 + s
        elif len(parts) == 2:
            m, s = map(float, parts)
            return m * 60 + s
        return float(time_str)
    except (ValueError, TypeError):
        return 0.0


def load_corrections(conn):
    """Load manual time corrections from data/corrections.csv.

    The CSV is wide-format (one row per athlete-race, with all splits);
    it is fanned out into long rows here, one per (race, athlete, discipline),
    all tagged source='manual'. Clears existing manual rows first so edits
    (and row removals) are picked up on re-run.
    """
    conn.execute("DELETE FROM corrections WHERE source = 'manual'")

    disciplines = ('overall', 'swim', 'bike', 'run', 't1', 't2')
    long_rows = []
    n_csv = 0
    with open(_DATA_DIR / 'corrections.csv', newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            try:
                race_id    = int(row['race_id'])
                athlete_id = int(row['athlete_id'])
            except (ValueError, KeyError, TypeError):
                continue
            n_csv += 1
            notes = row.get('notes', '').strip()
            for disc in disciplines:
                value = _parse_csv_time(row.get(disc, ''))
                long_rows.append((race_id, athlete_id, disc, value, 'manual', notes))

    conn.executemany(
        """INSERT OR REPLACE INTO corrections
               (race_id, athlete_id, discipline, value, source, reason)
           VALUES (?, ?, ?, ?, ?, ?)""",
        long_rows,
    )
    print(f"Loaded {n_csv} manual corrections ({len(long_rows)} long rows)")


def social_already_posted(conn, race_id, post_type):
    """True if (race_id, post_type) has a social_posts row."""
    return conn.execute(
        "SELECT 1 FROM social_posts WHERE race_id = ? AND post_type = ?",
        [race_id, post_type],
    ).fetchone() is not None


def mark_social_posted(conn, race_id, post_type, ig_post_id, fb_post_ids):
    """Record a published post. fb_post_ids is a list (joined to CSV) or None.

    DuckDB's INSERT ... ON CONFLICT DO UPDATE chokes on CURRENT_TIMESTAMP in
    the SET list (parses the bare keyword as a column reference) and also
    doesn't fill the DEFAULT for posted_at on a parameterised INSERT path,
    so we set the timestamp explicitly on both sides.
    """
    csv = ",".join(fb_post_ids) if fb_post_ids else None
    conn.execute(
        """
        INSERT INTO social_posts (race_id, post_type, posted_at, ig_post_id, fb_post_ids)
        VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
        ON CONFLICT (race_id, post_type) DO UPDATE SET
            posted_at   = excluded.posted_at,
            ig_post_id  = excluded.ig_post_id,
            fb_post_ids = excluded.fb_post_ids
        """,
        [race_id, post_type, ig_post_id, csv],
    )


def slug_id(slug):
    """Deterministic positive 31-bit int ID from a slug.

    CRC32 is collision-safe for our small slug count and gives stable IDs
    across rebuilds without needing a sequence table.
    """
    return zlib.crc32(slug.encode()) & 0x7FFFFFFF


def _title_from_slug(slug):
    return ' '.join(w.capitalize() for w in slug.split('-'))


def load_series_defs(conn):
    """Load series definitions from data/series.csv into the series table.

    Each row: slug, name, tier, continent, sort_order, description.
    series_id is derived deterministically from slug via slug_id().
    Clears the full series hierarchy (event_recurring, event_series,
    recurring_events, series) before reinserting — avoids DuckDB's spurious FK
    checks on UPDATE and ensures a clean slate for series_rules.apply().
    """
    # Clear child-to-parent order to satisfy FK constraints.
    conn.execute("DELETE FROM event_recurring")
    conn.execute("DELETE FROM event_series")
    conn.execute("DELETE FROM recurring_events")
    conn.execute("DELETE FROM series")

    rows = []
    with open(_DATA_DIR / 'series.csv', newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            slug = row['slug'].strip()
            if not slug:
                continue
            rows.append((
                slug_id(slug),
                slug,
                row['name'].strip(),
                row.get('tier', 'custom').strip() or 'custom',
                row.get('continent', '').strip(),
                int(row.get('sort_order', '100') or 100),
                row.get('description', '').strip(),
            ))
    conn.executemany(
        """INSERT INTO series (series_id, slug, name, tier, continent, sort_order, description)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    print(f"Loaded {len(rows)} series definitions")


def load_event_series_csv(conn):
    """Load manual event→series mappings from data/event_series.csv.

    Each row: event_id, series_slug, recurring_slug (optional).
    Run AFTER rule-based population so CSV overrides/adds to the rule output.
    Unknown series slugs abort with a clear error (fail fast).
    """
    series_lookup = dict(conn.execute("SELECT slug, series_id FROM series").fetchall())

    added = 0
    with open(_DATA_DIR / 'event_series.csv', newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            try:
                event_id = int(row['event_id'])
            except (ValueError, KeyError, TypeError):
                continue
            series_slug = row.get('series_slug', '').strip()
            if not series_slug:
                continue
            series_id = series_lookup.get(series_slug)
            if series_id is None:
                raise RuntimeError(
                    f"event_series.csv references unknown series slug '{series_slug}' "
                    f"(event_id={event_id}). Add it to series.csv first."
                )
            if not conn.execute("SELECT 1 FROM events WHERE event_id = ?", [event_id]).fetchone():
                print(f"Warning: event_series.csv event_id {event_id} not in DB, skipping")
                continue

            recurring_slug = row.get('recurring_slug', '').strip()
            if recurring_slug:
                rid = slug_id(recurring_slug)
                # venue_key is the slug with the series suffix stripped if present
                vkey = recurring_slug
                if vkey.endswith('-' + series_slug):
                    vkey = vkey[:-(len(series_slug) + 1)]
                conn.execute("""
                    INSERT OR IGNORE INTO recurring_events (recurring_event_id, slug, name, venue_key)
                    VALUES (?, ?, ?, ?)
                """, [rid, recurring_slug, _title_from_slug(recurring_slug), vkey])
                conn.execute(
                    """INSERT INTO event_recurring (event_id, recurring_event_id) VALUES (?, ?)
                       ON CONFLICT (event_id) DO UPDATE SET recurring_event_id = excluded.recurring_event_id""",
                    [event_id, rid],
                )

            conn.execute(
                "INSERT OR IGNORE INTO event_series (event_id, series_id) VALUES (?, ?)",
                [event_id, series_id],
            )
            added += 1

    print(f"Loaded {added} manual event→series mappings from CSV")


def load_manual_ignored(conn):
    """Load manually specified ignored races from data/ignored.csv.

    Call AFTER detect_all() - that function clears the table first, so manual
    entries must be (re-)inserted afterwards with INSERT OR IGNORE.
    Skips rows where race_id isn't in the races table (FK would fail).
    """
    rows = []
    with open(_DATA_DIR / 'ignored.csv', newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            try:
                race_id = int(row['race_id'])
            except (ValueError, KeyError, TypeError):
                continue  # skip blank/malformed lines
            reason = row.get('reason', '').strip()
            raw_parent = row.get('parent_race_id', '').strip()
            parent_race_id = int(raw_parent) if raw_parent else None
            rows.append((race_id, reason, parent_race_id))
    valid = []
    for race_id, reason, parent_race_id in rows:
        if not conn.execute("SELECT 1 FROM races WHERE race_id = ?", [race_id]).fetchone():
            print(f"Warning: manual ignored race {race_id} not in DB, skipping")
            continue
        valid.append((race_id, reason, parent_race_id))
    if valid:
        conn.executemany(
            "INSERT OR REPLACE INTO ignored_races (race_id, reason, parent_race_id) VALUES (?, ?, ?)",
            valid,
        )
    print(f"Loaded {len(valid)} manual ignored races")


if __name__ == "__main__":
    conn = get_conn()
    
    tables = conn.execute("""
    SELECT table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = 'main'
    ORDER BY table_name, ordinal_position
    """).fetchall()

    current = None
    for table, col, dtype in tables:
        if table != current:
            print(f"\nTable: {table}")
            current = table
        print(f"  {col} ({dtype})")

    conn.close()
