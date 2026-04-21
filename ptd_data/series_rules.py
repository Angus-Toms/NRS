"""Rule-based series population.

Runs as part of the ingest pipeline to auto-populate `event_series` and
`recurring_events` from the events table. Rules are matched against events;
each matching rule adds an (event, series) row. Recurring-aware rules also
group events by a normalized venue key so e.g. all "Lanzarote World Cup"
events across years share one recurring_event row.

CSV overrides (ptd_data/data/event_series.csv) run after this module.

    from ptd_data import series_rules
    series_rules.apply(conn)
"""
import re
import unicodedata
from ast import literal_eval
from dataclasses import dataclass
from typing import Callable

from ptd_data import db


# World Triathlon event_categories cat_id constants
CAT_WORLD_CHAMPS      = 348   # World Championships
CAT_CONTINENTAL_CUP   = 341   # Continental Cup
CAT_CONTINENTAL_CHAMPS = 340  # Continental Championships (unused for now)


@dataclass(frozen=True)
class EventRow:
    event_id: int
    name: str
    country: str
    continent: str
    cat_ids: frozenset
    spec_ids: frozenset


# --- Predicate helpers ---

def name_regex(pattern):
    rx = re.compile(pattern, re.IGNORECASE)
    return lambda e: bool(rx.search(e.name))

def has_cat_id(n):
    return lambda e: n in e.cat_ids

def continent_is(c):
    return lambda e: e.continent == c

def all_of(*preds):
    return lambda e: all(p(e) for p in preds)

def any_of(*preds):
    return lambda e: any(p(e) for p in preds)

def not_(pred):
    return lambda e: not pred(e)


@dataclass(frozen=True)
class SeriesRule:
    series_slug: str
    match: Callable
    recurring: bool = False


# Order matters only in that the first recurring-aware match sets
# events.recurring_event_id. All matching rules add event_series rows.
RULES = [
    # Summer Olympic Games only — exclude Youth Olympics, test events, qualifiers.
    SeriesRule("olympic-games",       all_of(
                                          name_regex(r"olympic games"),
                                          not_(name_regex(r"youth|test event|qualification")),
                                      )),
    SeriesRule("commonwealth-games",  name_regex(r"commonwealth (games|youth games)")),
    # FISU listed before world-championships so its recurring slug wins.
    # FISU events have cat 345 ("Recognised Event"), not cat 348, so they
    # don't collide with the world-championships rule below.
    SeriesRule("fisu-games",          name_regex(r"\bfisu\b"), recurring=True),
    # Age-Group Worlds split out from the elite/U23/junior/youth series.
    SeriesRule("ag-world-champs",     all_of(
                                          name_regex(r"age[\s-]?group"),
                                          any_of(
                                              has_cat_id(CAT_WORLD_CHAMPS),
                                              name_regex(r"world triathlon age[\s-]?group championship"),
                                          ),
                                      ),
                                      recurring=True),
    # World champs: cat 348, plus the "Championship Finals" name fallback for
    # 2024 Torremolinos which is missing cat 348 in source data. Excludes AG.
    SeriesRule("world-championships", all_of(
                                          any_of(
                                              has_cat_id(CAT_WORLD_CHAMPS),
                                              name_regex(r"world triathlon championship finals"),
                                          ),
                                          not_(name_regex(r"age[\s-]?group")),
                                      ),
                                      recurring=True),
    SeriesRule("wtcs",                any_of(
                                          name_regex(r"world triathlon championship series"),
                                          name_regex(r"\bwtcs\b"),
                                          name_regex(r"grand final"),
                                      ),
                                      recurring=True),
    # World Cup rule must not match "World Championships"; require "cup" not followed by championships
    SeriesRule("world-cup",           all_of(
                                          name_regex(r"world (triathlon )?cup"),
                                          not_(name_regex(r"world championships")),
                                      ),
                                      recurring=True),
    SeriesRule("european-champs",     all_of(has_cat_id(CAT_CONTINENTAL_CHAMPS), continent_is("Europe")),   recurring=True),
    SeriesRule("african-champs",      all_of(has_cat_id(CAT_CONTINENTAL_CHAMPS), continent_is("Africa")),   recurring=True),
    SeriesRule("americas-champs",     all_of(has_cat_id(CAT_CONTINENTAL_CHAMPS), continent_is("Americas")), recurring=True),
    SeriesRule("asian-champs",        all_of(has_cat_id(CAT_CONTINENTAL_CHAMPS), continent_is("Asia")),     recurring=True),
    SeriesRule("oceania-champs",      all_of(has_cat_id(CAT_CONTINENTAL_CHAMPS), continent_is("Oceania")),  recurring=True),
    SeriesRule("european-cup",        all_of(has_cat_id(CAT_CONTINENTAL_CUP), continent_is("Europe")),   recurring=True),
    SeriesRule("african-cup",         all_of(has_cat_id(CAT_CONTINENTAL_CUP), continent_is("Africa")),   recurring=True),
    SeriesRule("americas-cup",        all_of(has_cat_id(CAT_CONTINENTAL_CUP), continent_is("Americas")), recurring=True),
    SeriesRule("asian-cup",           all_of(has_cat_id(CAT_CONTINENTAL_CUP), continent_is("Asia")),     recurring=True),
    SeriesRule("oceania-cup",         all_of(has_cat_id(CAT_CONTINENTAL_CUP), continent_is("Oceania")),  recurring=True),
]


# --- Venue key normalization ---
# Strip common series/org/discipline tokens so the remainder is the venue.
_STRIP_TOKENS = re.compile(
    r'\b(?:'
    r'world|triathlon|cup|championship|championships|continental|'
    r'grand|final|finals|series|wtcs|itu|wt|etu|atu|camtri|asiatri|'
    r'europe|european|africa|african|asia|asian|americas|american|oceania|'
    r'sprint|standard|middle|long|distance|olympic|games|team|relay|mixed|'
    r'u23|junior|juniors|youth|elite|paratriathlon|para|development|premium|'
    r'winter|duathlon|aquathlon|aquabike|multisport|tour|open|age|group|'
    r'national|federation|fisu|commonwealth|university|universiade'
    r')\b',
    re.IGNORECASE,
)


def venue_key(event_name):
    """Normalize an event title into a short venue slug.

    "2024 Lanzarote World Triathlon Cup" -> "lanzarote"
    "2019 Lanzarote ITU World Cup"        -> "lanzarote"
    "Abu Dhabi WTCS 2024"                 -> "abu-dhabi"
    """
    s = re.sub(r'\b\d{4}\b', ' ', event_name)  # drop years
    s = _STRIP_TOKENS.sub(' ', s)
    # Fold diacritics: "Zürich" -> "Zurich"
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    # Keep only letters/numbers/spaces, lowercase
    s = re.sub(r'[^a-zA-Z0-9\s]', ' ', s).lower()
    parts = s.split()
    return '-'.join(parts) if parts else 'unknown'


# --- Main entry point ---

def apply(conn):
    """Populate event_series + recurring_events from RULES.

    Expects `series` table already populated (call db.load_series_defs first).
    Clears event_recurring, event_series, and recurring_events before rebuilding.
    """
    # FK ordering: event_recurring references both events and recurring_events,
    # event_series references events — clear children before parents.
    conn.execute("DELETE FROM event_recurring")
    conn.execute("DELETE FROM event_series")
    conn.execute("DELETE FROM recurring_events")

    series_lookup = dict(conn.execute("SELECT slug, series_id FROM series").fetchall())
    for rule in RULES:
        if rule.series_slug not in series_lookup:
            raise RuntimeError(
                f"series_rules references unknown series slug '{rule.series_slug}'. "
                "Add it to ptd_data/data/series.csv."
            )

    # cat_ids / event_spec_ids are per-race; aggregate to event level.
    # ANY_VALUE is fine since these are event-level attributes repeated on each race.
    events = conn.execute("""
        SELECT e.event_id, e.name, e.country, e.continent,
               COALESCE(ANY_VALUE(r.cat_ids), '[]'),
               COALESCE(ANY_VALUE(r.event_spec_ids), '[]')
        FROM events e
        LEFT JOIN races r ON r.event_id = e.event_id
        GROUP BY e.event_id, e.name, e.country, e.continent
    """).fetchall()

    per_series = {}  # slug -> count
    recurring_rows = {}  # recurring_id -> (slug, name, venue_key)
    event_links = []     # (event_id, series_id)
    event_recurring = {} # event_id -> recurring_id (first recurring-aware match wins)

    for event_id, name, country, continent, cat_ids_str, spec_ids_str in events:
        try:
            cat_ids = frozenset(literal_eval(cat_ids_str) if cat_ids_str else [])
        except (ValueError, SyntaxError):
            cat_ids = frozenset()
        try:
            spec_ids = frozenset(literal_eval(spec_ids_str) if spec_ids_str else [])
        except (ValueError, SyntaxError):
            spec_ids = frozenset()

        row = EventRow(event_id, name or '', country or '', continent or '', cat_ids, spec_ids)

        for rule in RULES:
            if not rule.match(row):
                continue
            event_links.append((event_id, series_lookup[rule.series_slug]))
            per_series[rule.series_slug] = per_series.get(rule.series_slug, 0) + 1

            if rule.recurring and event_id not in event_recurring:
                vkey = venue_key(name or '')
                rslug = f"{vkey}-{rule.series_slug}"
                rid = db.slug_id(rslug)
                rname = f"{db._title_from_slug(vkey)} {db._title_from_slug(rule.series_slug)}"
                recurring_rows[rid] = (rslug, rname, vkey)
                event_recurring[event_id] = rid

    # Bulk write
    if recurring_rows:
        conn.executemany(
            """INSERT OR IGNORE INTO recurring_events (recurring_event_id, slug, name, venue_key)
               VALUES (?, ?, ?, ?)""",
            [(rid, slug, name, vk) for rid, (slug, name, vk) in recurring_rows.items()],
        )
    if event_recurring:
        conn.executemany(
            """INSERT INTO event_recurring (event_id, recurring_event_id) VALUES (?, ?)
               ON CONFLICT (event_id) DO UPDATE SET recurring_event_id = excluded.recurring_event_id""",
            [(eid, rid) for eid, rid in event_recurring.items()],
        )
    if event_links:
        conn.executemany(
            "INSERT OR IGNORE INTO event_series (event_id, series_id) VALUES (?, ?)",
            event_links,
        )

    for slug, count in sorted(per_series.items()):
        print(f"  {slug}: {count} events")
    print(f"Rule-based series: {len(event_links)} mappings, {len(recurring_rows)} recurring groups")


if __name__ == "__main__":
    conn = db.get_conn(read_only=False)
    db.load_series_defs(conn)
    apply(conn)
    db.load_event_series_csv(conn)
    conn.close()
