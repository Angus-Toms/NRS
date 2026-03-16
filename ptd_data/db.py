import csv
import pathlib

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
    conn.execute(
        "CREATE TYPE IF NOT EXISTS result_status_enum AS ENUM "
        "('Finished', 'DNF', 'DNS', 'DQ', 'LAP', 'NC')"
    )
    conn.execute(
        "CREATE TYPE IF NOT EXISTS continent_enum AS ENUM "
        "('Americas', 'Europe', 'Asia', 'Africa', 'Oceania', 'Other')"
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
            gender          gender_enum NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS series (
            series_id       INTEGER PRIMARY KEY,
            name            VARCHAR NOT NULL,
            description     VARCHAR NOT NULL DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS recurring_events (
            recurring_event_id  INTEGER PRIMARY KEY,
            name                VARCHAR NOT NULL,
            series_id           INTEGER REFERENCES series(series_id),
            description         VARCHAR NOT NULL DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id            INTEGER PRIMARY KEY,
            recurring_event_id  INTEGER REFERENCES recurring_events(recurring_event_id), -- Max one of these FKs should be set at any time
            series_id           INTEGER REFERENCES series(series_id),                    -- Max one of these FKs should be set at any time
            name                VARCHAR NOT NULL,
            venue               VARCHAR NOT NULL DEFAULT '',
            country             VARCHAR NOT NULL DEFAULT '',
            continent           continent_enum NOT NULL DEFAULT 'Other',
            start_date          DATE NOT NULL,
            end_date            DATE NOT NULL,
            longitude           DOUBLE NOT NULL DEFAULT 0, 
            latitude            DOUBLE NOT NULL DEFAULT 0
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
            cat_ids         VARCHAR NOT NULL DEFAULT '[]',
            race_handle     VARCHAR NOT NULL DEFAULT '',
            event_spec_ids  VARCHAR NOT NULL DEFAULT '[]'
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
            PRIMARY KEY (race_id, athlete_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ignored_races (
            race_id     INTEGER PRIMARY KEY REFERENCES races(race_id),
            reason      VARCHAR NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS corrections (
            race_id    INTEGER NOT NULL,
            athlete_id INTEGER NOT NULL,
            swim       DOUBLE NOT NULL DEFAULT 0,
            t1         DOUBLE NOT NULL DEFAULT 0,
            bike       DOUBLE NOT NULL DEFAULT 0,
            t2         DOUBLE NOT NULL DEFAULT 0,
            run        DOUBLE NOT NULL DEFAULT 0,
            overall    DOUBLE NOT NULL DEFAULT 0,
            notes      VARCHAR NOT NULL DEFAULT '',
            PRIMARY KEY (race_id, athlete_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            race_id             INTEGER NOT NULL REFERENCES races(race_id),
            athlete_id          INTEGER NOT NULL REFERENCES athletes(athlete_id),
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
            PRIMARY KEY (race_id, athlete_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rankings (
            race_id                 INTEGER NOT NULL REFERENCES races(race_id),
            athlete_id              INTEGER NOT NULL REFERENCES athletes(athlete_id),
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
            PRIMARY KEY (race_id, athlete_id)
        )
    """)

    # ART indexes on non-PK columns used in WHERE/JOIN clauses
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_recurring_event_id ON events(recurring_event_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(start_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_recurring_events_series_id ON recurring_events(series_id)")
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
    "Federal Republic of Germany": ("DEU", "🇩🇪"),
    "Czechoslovakia": ("CSK", "🇨🇿"),
    "Iran": ("IRN", "🇮🇷"),
    "Netherlands Antilles": ("ANT", "🇧🇶"),
    "Yugoslavia": ("YUG", "🏴"),
    "Swaziland": ("SWZ", "🇸🇿")
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
    """Insert or update an athlete."""
    conn.execute(
        """
        INSERT OR REPLACE INTO athletes
            (athlete_id, name, country_full, year_of_birth, profile_img, gender)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [athlete_id, name, country_full, year_of_birth, profile_img, gender],
    )


def insert_race(conn, race_id, event_id, race_title, prog_name, race_date, gender, cat_ids, race_handle='', event_spec_ids='[]'):
    """Insert a race, skip if it already exists."""
    conn.execute(
        """
        INSERT OR IGNORE INTO races
            (race_id, event_id, race_title, prog_name, race_date, gender,
             cat_ids, race_handle, event_spec_ids)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [race_id, event_id, race_title, prog_name, race_date, gender,
         cat_ids, race_handle, event_spec_ids],
    )


def insert_event(conn, event_id, name, venue, country, continent, start_date, end_date, longitude, latitude):
    """Insert an event, skip if it already exists."""
    conn.execute(
        """
        INSERT OR IGNORE INTO events
            (event_id, name, venue, country, continent, start_date, end_date, longitude, latitude)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [event_id, name, venue, country, continent, start_date, end_date, longitude, latitude],
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
    for table in ("results", "races", "athletes", "nationalities"):
        conn.execute(f"DELETE FROM {table}")


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

    Overwrites existing entries (INSERT OR REPLACE) so re-running picks up edits.
    """
    rows = []
    with open(_DATA_DIR / 'corrections.csv', newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            try:
                race_id    = int(row['race_id'])
                athlete_id = int(row['athlete_id'])
            except (ValueError, KeyError, TypeError):
                continue
            rows.append((
                race_id, athlete_id,
                _parse_csv_time(row.get('swim', '')),
                _parse_csv_time(row.get('t1', '')),
                _parse_csv_time(row.get('bike', '')),
                _parse_csv_time(row.get('t2', '')),
                _parse_csv_time(row.get('run', '')),
                _parse_csv_time(row.get('overall', '')),
                row.get('notes', '').strip(),
            ))
    conn.executemany(
        """INSERT OR REPLACE INTO corrections
               (race_id, athlete_id, swim, t1, bike, t2, run, overall, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    print(f"Loaded {len(rows)} corrections")


def load_manual_ignored(conn):
    """Load manually specified ignored races from data/ignored.csv.

    Call AFTER detect_all() — that function clears the table first, so manual
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
            rows.append((race_id, reason))
    valid = []
    for race_id, reason in rows:
        if not conn.execute("SELECT 1 FROM races WHERE race_id = ?", [race_id]).fetchone():
            print(f"Warning: manual ignored race {race_id} not in DB, skipping")
            continue
        valid.append((race_id, reason))
    if valid:
        conn.executemany(
            "INSERT OR REPLACE INTO ignored_races (race_id, reason) VALUES (?, ?)",
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
