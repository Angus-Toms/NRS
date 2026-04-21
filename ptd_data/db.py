import csv
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
            alpha3          VARCHAR NOT NULL,
            emoji           VARCHAR NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS athletes (
            athlete_id      INTEGER PRIMARY KEY,
            name            VARCHAR NOT NULL,
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
            athlete_id      INTEGER NOT NULL REFERENCES athletes(athlete_id),
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
            athlete_id          INTEGER NOT NULL REFERENCES athletes(athlete_id),
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
            athlete_id              INTEGER NOT NULL REFERENCES athletes(athlete_id),
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS prediction_models (
            gender      gender_enum NOT NULL,
            distance    VARCHAR NOT NULL,   -- 'sprint' | 'standard'
            discipline  VARCHAR NOT NULL,   -- 'overall' | 'swim' | 'bike' | 'run' | 'transition'
            slope       DOUBLE NOT NULL,
            intercept   DOUBLE NOT NULL,
            n_samples   INTEGER NOT NULL,
            PRIMARY KEY (gender, distance, discipline)
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
            athlete_id      INTEGER NOT NULL REFERENCES athletes(athlete_id),
            start_num       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (race_id, athlete_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_series (
            event_id  INTEGER NOT NULL REFERENCES events(event_id),
            series_id INTEGER NOT NULL REFERENCES series(series_id),
            PRIMARY KEY (event_id, series_id)
        )
    """)

    # ART indexes on non-PK columns used in WHERE/JOIN clauses
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(start_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_series_series_id ON event_series(series_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_races_event_id ON races(event_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_races_race_date ON races(race_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_athletes_country_full ON athletes(country_full)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_results_athlete_id ON results(athlete_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ratings_athlete_id ON ratings(athlete_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rankings_athlete_id ON rankings(athlete_id)")


# --- Country resolution ---

_COUNTRY_SPECIAL_CASES = {
    "Individual Neutral Athlete": ("INA", "🇺🇳"),
    "Great Britain": ("GBR", "🇬🇧"),
    "Republic of Korea": ("KOR", "🇰🇷"),
    "Czech Republic": ("CZE", "🇨🇿"),
    "Hong Kong, China": ("HKG", "🇭🇰"),
    "Russia": ("RUS", "🇷🇺"),
    "Syria": ("SYR", "🇸🇾"),
    "Macau, China": ("MAC", "🇲🇴"),
    "Venezuela": ("VEN", "🇻🇪"),
    "Chinese Taipei": ("TPE", "🇹🇼"),
    "Virgin Islands": ("ISV", "🇻🇮"),
    "Tahiti": ("PYF", "🇵🇫"),
    "Bolivia": ("BOL", "🇧🇴"),
    "Moldova": ("MDA", "🇲🇩"),
    "Saint Maarten": ("SXM", "🇸🇽"),
    "Czechoslovakia": ("CSK", "🇨🇿"),
    "Iran": ("IRN", "🇮🇷"),
    "Netherlands Antilles": ("ANT", "🇧🇶"),
    "Swaziland": ("SWZ", "🇸🇿"),
    # Historical German states (pre-1990). Kept distinct from modern "Germany"
    # (DEU) so each athlete's alpha3/URL matches the flag they competed under.
    "Federal Republic of Germany":   ("FRG", "🇩🇪"),  # West Germany 1949-1990
    "Democratic Republic of Germany": ("GDR", "🏴"),  # East Germany 1949-1990
    # Other historical / non-standard entities
    "Soviet Union":                    ("URS", "🏴"),
    "Yugoslavia":                      ("YUG", "🏴"),
    "Myanmar (Burma)":                 ("MMR", "🇲🇲"),
    "Cote d'Ivoire":                   ("CIV", "🇨🇮"),
    "Vietnam":                         ("VNM", "🇻🇳"),
    "Palestine":                       ("PSE", "🇵🇸"),
    "Democratic People's Republic of Korea": ("PRK", "🇰🇵"),
    # Federation / neutral banner
    "World Triathlon":                 ("WTR", "🇺🇳"),
}


def _resolve_country(country_full):
    """Returns (alpha3, emoji) for a country name."""
    if country_full in _COUNTRY_SPECIAL_CASES:
        return _COUNTRY_SPECIAL_CASES[country_full]
    country = pycountry.countries.get(name=country_full)
    if country is None:
        return ("UNK", "🏳️")
    return (country.alpha_3, country.flag)


# --- Write methods ---

def upsert_nationality(conn, country_full):
    """Insert nationality if it doesn't exist."""
    exists = conn.execute(
        "SELECT 1 FROM nationalities WHERE country_full = ?", [country_full]
    ).fetchone()
    if exists:
        return
    alpha3, emoji = _resolve_country(country_full)
    conn.execute(
        "INSERT INTO nationalities (country_full, alpha3, emoji) VALUES (?, ?, ?)",
        [country_full, alpha3, emoji],
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
