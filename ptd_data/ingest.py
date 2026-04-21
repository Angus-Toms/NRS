"""
Ingests triathlon data from the WorldTriathlon API directly into DuckDB.

Usage:
    python -m ptd_data.ingest
"""

import re
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from time import sleep
from ast import literal_eval

from config import WORLD_TRIATHLON_API_KEY
from ptd_data import db

BASE_URL = "https://api.triathlon.org/v1"
HEADERS = {"apikey": WORLD_TRIATHLON_API_KEY}

# Persistent session with retry logic for transient SSL/connection errors
_session = requests.Session()
_session.headers.update(HEADERS)
_retry = Retry(
    total=5,
    backoff_factor=2,  # 0s, 2s, 4s, 8s, 16s
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)
_session.mount("https://", HTTPAdapter(max_retries=_retry))

# Short course triathlon specification category IDs
VALID_SPEC_IDS = {376, 377}

_MALE_KEYWORDS = {'men', 'male'}
_FEMALE_KEYWORDS = {'women', 'female'}

_ELITE_PROG_PREFIXES = frozenset({'elite', 'u23', 'junior', 'youth'})


def race_category(prog_name):
    """Return 'elite', 'ag', or None (skip entirely, e.g. para/relay/mixed-team)."""
    first = prog_name.lower().split()[0] if prog_name else ''
    if first in _ELITE_PROG_PREFIXES:
        return 'elite'
    # Skip para, relay, and other non-individual formats
    if first in ('para', 'ptvi', 'pts5', 'pts4', 'pts3', 'pts2', 'ptwc', 'awad',
                 'relay', 'mixed', 'team', 'overall', 'open'):
        return None
    # Everything else (age-group brackets, masters, etc.) is AG
    return 'ag'


def race_sub_category(prog_name):
    """Sub-category used for program filtering: 'elite' | 'u23' | 'junior' | 'youth' | 'ag'."""
    first = prog_name.lower().split()[0] if prog_name else ''
    if first in _ELITE_PROG_PREFIXES:
        return first
    return 'ag'


def is_valid_program(prog_name):
    """Legacy shim - True if this program should be processed at all."""
    return race_category(prog_name) is not None


def detect_gender(prog_name):
    """Detect gender from a program name by checking for Men/Male vs Women/Female keywords.

    Returns 'male', 'female', or None if unrecognisable.
    """
    words = set(prog_name.lower().split())
    if words & _FEMALE_KEYWORDS:
        return 'female'
    if words & _MALE_KEYWORDS:
        return 'male'
    return None

NON_FINISH_STATUSES = {'DNF', 'DNS', 'DQ', 'LAP', 'NC'}


def time_to_seconds(time_str):
    """Convert time string (HH:MM:SS, MM:SS, or SS) to seconds.

    Returns 0 for empty/DNF/DQ/etc, float('inf') for unparseable values.
    """
    if time_str in ('None', '', 'DNF', 'DQ', 'LAP', 'NC', None):
        return 0.0

    if pd.isna(time_str):
        return float('inf')

    try:
        parts = str(time_str).strip().split(':')
        if len(parts) == 3:
            h, m, s = map(float, parts)
            return h * 3600 + m * 60 + s
        elif len(parts) == 2:
            m, s = map(float, parts)
            return m * 60 + s
        elif len(parts) == 1:
            return float(parts[0])
        return float('inf')
    except (ValueError, TypeError):
        return float('inf')


def parse_position(position_str):
    """Parse position string into (position_int_or_None, status_str)."""
    pos = str(position_str).strip().upper()
    if pos in NON_FINISH_STATUSES:
        return None, pos
    try:
        return int(position_str), 'Finished'
    except (ValueError, TypeError):
        return None, 'NC'


def clean_field(value):
    """Remove quotes and title-case a field value."""
    if pd.isna(value) or value is None:
        return ""
    cleaned = str(value).replace("'", "").replace('"', '').strip()
    return cleaned.title() if cleaned else ""


def parse_lnglat(coord):
    """Convert degree measurement to float if possible."""
    try:
        return float(coord)
    except (ValueError, TypeError):
        return 0.0

_HANDLE_SKIP_WORDS = {
    'world', 'triathlon', 'championships', 'americas', 'europe', 'africa', 'asia', 'oceania',
    'cup', 'american', 'european', 'asian', 'games', 't100', 'tour', 'winter', 'development',
    'regional', 'african', 'para', 'itu', 'etu', 'atu', 'fisu', 'university', 'junior', 'north',
    'camtri', 'and', 'ntt', 'astc', 'premium',
}


def generate_race_handle(race_id, race_title, location, race_date):
    """Generate display name for a race.

    Priority:
    1. National champs: 2nd title word is a 3-letter country code → "{CODE} National Champs {YY}"
    2. Short venue (<=3 words) → "{venue} {YY}"
    3. First 3 non-skip words from title → "{words} {YY}"
    """
    year = race_date.year if hasattr(race_date, 'year') else int(str(race_date)[:4])
    year_suffix = f"{year % 100:02d}"
    title_words = str(race_title).split()

    if len(title_words) > 1 and re.match(r'^[A-Z]{3}$', title_words[1]) and title_words[1] != 'ITU':
        return f"{title_words[1]} National Champs {year_suffix}"

    if location:
        venue_words = str(location).replace('"', '').replace("'", '').split()
        if 0 < len(venue_words) <= 3:
            return f"{' '.join(venue_words)} {year_suffix}"

    candidates = [w for w in title_words[1:] if w and w.lower().strip('.,') not in _HANDLE_SKIP_WORDS]
    if candidates:
        return f"{' '.join(candidates[:3])} {year_suffix}"
    return f"Event {race_id} {year_suffix}"


def get_category_ids(event):
    """Extract category IDs from event dict."""
    cats = event.get('event_categories', [])
    if not cats:
        return [-1]
    # When coming from API JSON, cats is already a list of dicts
    if isinstance(cats, str):
        try:
            cats = literal_eval(cats)
        except (ValueError, SyntaxError):
            return [-1]
    return [cat.get('cat_id', -1) for cat in cats]


def get_spec_ids(event):
    """Extract specification category IDs from event dict."""
    specs = event.get('event_specifications', [])
    if not specs:
        return []
    if isinstance(specs, str):
        try:
            specs = literal_eval(specs)
        except (ValueError, SyntaxError):
            return []
    return [spec.get('cat_id', -1) for spec in specs]


def is_short_course(event):
    """Check if an event has short course triathlon specifications."""
    return any(sid in VALID_SPEC_IDS for sid in get_spec_ids(event))


def infer_distance(spec_ids, winner_time_s=None):
    """Map WT spec IDs to distance enum value.

    376 = sprint, 377 = standard. When both are present (mixed-distance event),
    fall back to winner time: sub-90-min winner is sprint, else standard.
    """
    has_sprint = 376 in spec_ids
    has_standard = 377 in spec_ids
    if has_sprint and not has_standard:
        return 'sprint'
    if has_standard and not has_sprint:
        return 'standard'
    # Both present — infer from winner time when available
    if winner_time_s:
        return 'sprint' if winner_time_s < 5400 else 'standard'
    return 'standard'  # conservative fallback


class Ingester:
    def __init__(self, conn):
        self.conn = conn

    def run(self):
        """Full ingestion: fetch events, filter, fetch results, write to DB."""
        print("Fetching all events from API...")
        events = self._fetch_all_events()
        print(f"Fetched {len(events)} total events")

        short_events = [e for e in events if is_short_course(e)]
        print(f"Found {len(short_events)} short course events")

        with open("all_events.csv", "w") as f:
            pd.DataFrame(events).to_csv(f, index=False)

        self._ingest_events(short_events)
        db.backfill_sub_category(self.conn)

    def _fetch_all_events(self):
        """Paginate through /events endpoint."""
        all_events = []
        page = 1

        while True:
            response = _session.get(
                f"{BASE_URL}/events",
                params={"page": page, "per_page": 500},
            )
            data = response.json()

            if 'data' not in data or not data['data']:
                break

            all_events.extend(data['data'])
            print(f"  Page {page} ({len(all_events)} events)")

            if not data.get('next_page_url'):
                break

            page += 1
            sleep(0.5)

        return all_events


    def _ingest_events(self, events):
        """Single pass over events - fetches programs once, processes both genders."""
        existing_event_ids = set(
            r[0] for r in self.conn.execute(
                "SELECT DISTINCT event_id FROM races"
            ).fetchall()
        )

        new_count = 0
        checked = 0
        total_new = len(events) - len(existing_event_ids)
        print(f"  {len(existing_event_ids)} events already in DB, {total_new} to check")

        for event in events:
            event_id = event['event_id']

            if event_id in existing_event_ids:
                continue

            checked += 1
            if checked % 100 == 0:
                print(f"  Checked {checked}/{total_new} new events, ingested {new_count} programs")

            # Insert event (once per API event, regardless of programs)
            self._insert_event(event)

            programs = self._fetch_programs(event_id)
            if not programs:
                existing_event_ids.add(event_id)
                continue

            for prog in programs:
                prog_name = prog.get('prog_name', '')
                category = race_category(prog_name)
                if category is None:
                    continue
                gender = detect_gender(prog_name)
                if gender is None:
                    continue
                if not prog.get('results') or not prog.get('is_race'):
                    continue

                prog_id = int(prog['prog_id'])
                results = self._fetch_results(event_id, prog_id)
                if not results:
                    continue

                if self._insert_program(event, prog, results, gender, category):
                    new_count += 1
                    print(f"  Ingested: {prog_name} - {event.get('event_title', '')}")

            existing_event_ids.add(event_id)

        print(f"Done. Checked {checked} new events, ingested {new_count} programs")

    def _fetch_programs(self, event_id):
        """GET /events/{id}/programs - returns list of dicts."""
        response = _session.get(f"{BASE_URL}/events/{event_id}/programs")
        data = response.json().get('data', [])
        if not isinstance(data, list):
            return []
        # API occasionally returns non-dict items (strings, nulls); skip them.
        return [p for p in data if isinstance(p, dict)]

    def _fetch_results(self, event_id, prog_id):
        """GET /events/{id}/programs/{prog_id}/results - returns list of result dicts."""
        url = f"{BASE_URL}/events/{event_id}/programs/{prog_id}/results"
        response = _session.get(url)
        results = response.json().get('data', {}).get('results', [])
        return results if results else []

    def _insert_program(self, event, prog, results, gender, category):
        """Insert a race + its athletes + results into the DB.

        Returns False if no valid individual results (e.g. team events).
        """
        prog_id = int(prog['prog_id'])
        event_id = int(event['event_id'])

        # Parse all results and build bulk rows
        result_rows = []
        for r in results:
            if 'athlete_id' not in r:
                continue
            athlete_id = int(r['athlete_id'])
            name = str(r.get('athlete_title', '')).replace('"', '').replace("'", "")
            country_name = str(r.get('athlete_country_name', ''))

            yob = 0
            try:
                yob = int(float(r.get('athlete_yob', 0)))
            except (ValueError, TypeError):
                pass

            profile_img = str(r.get('athlete_profile_image', '') or '')
            start_num = 0
            try:
                start_num = int(r.get('start_num', 0))
            except (ValueError, TypeError):
                pass

            # Upsert nationality + athlete
            db.upsert_nationality(self.conn, country_name)
            db.upsert_athlete(self.conn, athlete_id, name, country_name, yob, profile_img, gender)

            # Parse splits: [swim, t1, bike, t2, run]
            splits = r.get('splits', [])
            if isinstance(splits, str):
                try:
                    splits = literal_eval(splits)
                except (ValueError, SyntaxError):
                    splits = []

            if len(splits) == 5:
                swim_s = time_to_seconds(splits[0])
                t1_s = time_to_seconds(splits[1])
                bike_s = time_to_seconds(splits[2])
                t2_s = time_to_seconds(splits[3])
                run_s = time_to_seconds(splits[4])
            else:
                swim_s = t1_s = bike_s = t2_s = run_s = 0.0

            overall_s = time_to_seconds(r.get('total_time', ''))

            # Drop rows with unparseable times
            if float('inf') in (overall_s, swim_s, bike_s, run_s, t1_s, t2_s):
                continue

            position_int, status = parse_position(r.get('position', ''))

            # Note: DNFs keep whatever splits the API returned. auto_corrections
            # (run after ingest) handles cleanup; genuine per-leg splits then
            # contribute to per-discipline ELO updates.

            result_rows.append((
                prog_id, athlete_id, position_int, status, start_num,
                overall_s, swim_s, bike_s, run_s, t1_s, t2_s,
            ))

        if not result_rows:
            prog_name = str(prog.get('prog_name', ''))
            race_title = str(event.get('event_title', ''))
            print(f"  Skipping {race_title!r} — {prog_name!r} (no individual results, likely team event)")
            return False

        race_title = str(event.get('event_title', ''))
        location = clean_field(event.get('event_venue', ''))
        race_date = str(prog.get('prog_date', ''))
        prog_name = str(prog.get('prog_name', ''))
        winner_s = next((r[5] for r in result_rows if r[2] == 1 and r[5] > 0), None)
        db.insert_race(
            self.conn,
            race_id=prog_id,
            event_id=event_id,
            race_title=race_title,
            prog_name=prog_name,
            race_date=race_date,
            gender=gender,
            category=category,
            sub_category=race_sub_category(prog_name),
            cat_ids=str(get_category_ids(event)),
            distance=infer_distance(get_spec_ids(event), winner_s),
            race_handle=generate_race_handle(prog_id, race_title, location, race_date),
            event_spec_ids=str(get_spec_ids(event)),
        )
        db.insert_results_bulk(self.conn, result_rows)
        return True

    def _insert_event(self, event):
        """Insert event data into events table."""
        event_id = int(event['event_id'])
        name = str(event.get('event_title', ''))
        venue = clean_field(event.get('event_venue', ''))
        country = clean_field(event.get('event_country', ''))
        continent = clean_field(event.get('event_region_name', ''))
        start_date = str(event.get('event_date', ''))
        end_date = str(event.get('event_finish_date', ''))
        longitude = parse_lnglat(event.get('event_longitude', 0))
        latitude = parse_lnglat(event.get('event_latitude', 0))
        
        db.insert_event(
            self.conn,
            event_id=event_id,
            name=name,
            venue=venue,
            country=country,
            continent=continent,
            start_date=start_date if start_date else None,
            end_date=end_date if end_date else None,
            longitude=longitude,
            latitude=latitude
        )


class StartListIngester:
    """Fetches start lists for upcoming short-course races (next 90 days) and writes to DB."""

    def __init__(self, conn):
        self.conn = conn

    def run(self):
        from datetime import date, timedelta
        today = date.today()
        end = today + timedelta(days=90)

        print(f"Fetching upcoming events {today} → {end}...")
        resp = _session.get(f"{BASE_URL}/events", params={
            'start_date': today.isoformat(),
            'end_date': end.isoformat(),
            'per_page': 100,
        })
        events = resp.json().get('data', [])
        short_events = [e for e in events if is_short_course(e)]
        print(f"Found {len(short_events)} short course events")

        self._purge_completed()

        total_entries = 0
        for event in short_events:
            event_id = event['event_id']
            programs = self._fetch_programs(event_id)
            sleep(0.3)

            for prog in programs:
                prog_name = prog.get('prog_name', '')
                category = race_category(prog_name)
                if category is None:
                    continue
                gender = detect_gender(prog_name)
                if gender is None:
                    continue
                # Skip if already has results (will be/is ingested as a completed race)
                if not prog.get('is_race') or prog.get('results'):
                    continue

                prog_id = int(prog['prog_id'])
                entries = self._fetch_entries(event_id, prog_id)
                sleep(0.3)
                if not entries:
                    continue

                self._upsert_upcoming_race(event, prog, gender, category)
                count = self._upsert_entries(prog_id, entries, gender)
                total_entries += count
                print(f"  {prog_name} — {event['event_title']}: {count} entries")

        print(f"Done. {total_entries} total start list entries")

    def _fetch_programs(self, event_id):
        resp = _session.get(f"{BASE_URL}/events/{event_id}/programs")
        data = resp.json().get('data', [])
        if not isinstance(data, list):
            return []
        return [p for p in data if isinstance(p, dict)]

    def _fetch_entries(self, event_id, prog_id):
        resp = _session.get(f"{BASE_URL}/events/{event_id}/programs/{prog_id}/entries")
        entries = resp.json().get('data', {}).get('entries', [])
        # Only approved entries make the start list
        return [e for e in entries if e.get('approved', False)]

    def _upsert_upcoming_race(self, event, prog, gender, category):
        race_id = int(prog['prog_id'])
        event_id = int(event['event_id'])
        race_title = str(event.get('event_title', ''))
        prog_name = str(prog.get('prog_name', ''))
        race_date = str(prog.get('prog_date', ''))
        location = clean_field(event.get('event_venue', ''))

        # Ensure event exists in events table
        db.insert_event(
            self.conn,
            event_id=event_id,
            name=race_title,
            venue=location,
            country=clean_field(event.get('event_country', '')),
            continent=clean_field(event.get('event_region_name', '')),
            start_date=str(event.get('event_date', '')) or None,
            end_date=str(event.get('event_finish_date', '')) or None,
            longitude=parse_lnglat(event.get('event_longitude', 0)),
            latitude=parse_lnglat(event.get('event_latitude', 0)),
        )

        self.conn.execute("""
            INSERT OR REPLACE INTO upcoming_races
                (race_id, event_id, race_title, prog_name, race_date, gender, category,
                 cat_ids, race_handle, event_spec_ids, last_fetched)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, [
            race_id, event_id, race_title, prog_name, race_date, gender, category,
            str(get_category_ids(event)),
            generate_race_handle(race_id, race_title, location, race_date),
            str(get_spec_ids(event)),
        ])

    def _upsert_entries(self, race_id, entries, gender):
        rows = []
        for e in entries:
            if 'athlete_id' not in e:
                continue
            athlete_id = int(e['athlete_id'])
            name = str(e.get('athlete_title', '')).replace('"', '').replace("'", "")
            country_name = str(e.get('athlete_country_name', ''))
            yob = 0
            try:
                yob = int(float(e.get('athlete_yob', 0)))
            except (ValueError, TypeError):
                pass
            profile_img = str(e.get('athlete_profile_image', '') or '')
            start_num = 0
            try:
                start_num = int(e.get('start_num', 0) or 0)
            except (ValueError, TypeError):
                pass

            db.upsert_nationality(self.conn, country_name)
            db.upsert_athlete(self.conn, athlete_id, name, country_name, yob, profile_img, gender)
            rows.append((race_id, athlete_id, start_num))

        if rows:
            # Full replace — handles withdrawals and bib reassignments
            self.conn.execute("DELETE FROM start_list_entries WHERE race_id = ?", [race_id])
            self.conn.executemany(
                "INSERT OR IGNORE INTO start_list_entries (race_id, athlete_id, start_num) VALUES (?, ?, ?)",
                rows,
            )
        return len(rows)

    def _purge_completed(self):
        """Remove upcoming races that have since been ingested as completed races."""
        self.conn.execute("""
            DELETE FROM start_list_entries
            WHERE race_id IN (SELECT race_id FROM races)
        """)
        self.conn.execute("""
            DELETE FROM upcoming_races
            WHERE race_id IN (SELECT race_id FROM races)
        """)


if __name__ == "__main__":
    conn = db.get_conn(read_only=False)
    ingester = Ingester(conn)
    ingester.run()
    conn.close()
    print("Done.")
