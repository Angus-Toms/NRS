import duckdb
import pycountry

from config import DB_PATH


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
        CREATE TABLE IF NOT EXISTS races (
            race_id         INTEGER PRIMARY KEY,
            event_id        INTEGER NOT NULL,
            race_title      VARCHAR NOT NULL,
            prog_name       VARCHAR NOT NULL,
            race_date       DATE NOT NULL,
            location        VARCHAR NOT NULL DEFAULT '',
            country         VARCHAR NOT NULL DEFAULT '',
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
    "Moldova": ("MDA", "🇲🇩")
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


def insert_race(conn, race_id, event_id, race_title, prog_name, race_date, location, country,
                gender, cat_ids, race_handle='', event_spec_ids='[]'):
    """Insert a race, skip if it already exists."""
    conn.execute(
        """
        INSERT OR IGNORE INTO races
            (race_id, event_id, race_title, prog_name, race_date, location, country, gender,
             cat_ids, race_handle, event_spec_ids)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [race_id, event_id, race_title, prog_name, race_date, location, country, gender,
         cat_ids, race_handle, event_spec_ids],
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
