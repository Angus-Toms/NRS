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
CAT_AG                = 483   # Age-Group flag (paired with another tier cat_id)


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
    """Match an event into a series and/or a recurring group.

    `series_slug=None` means the rule does not assign the event to any
    series - it only groups it into a recurring_event. Useful for
    standalone races (e.g. Collins Cup, the PTO Opens) that share a
    distance with a wider tour but aren't part of it.

    `recurring_slug` / `recurring_name` override the auto-derived
    recurring slug+name (which would otherwise be venue_key + series_slug).
    Recurring-only rules need these because there is no series suffix to
    fall back on.
    """
    series_slug: str | None
    match: Callable
    recurring: bool = False
    recurring_slug: str | None = None
    recurring_name: str | None = None


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
    # Age-Group European Championships. Matches dual-tier events that host
    # both elite and AG races (e.g. "2024 Europe Triathlon Championships
    # Vichy"); _scope_clauses filters series-scoped queries to AG races
    # only when the series has tier='ag-championship'.
    SeriesRule("ag-european-champs",  all_of(
                                          has_cat_id(CAT_CONTINENTAL_CHAMPS),
                                          has_cat_id(CAT_AG),
                                          name_regex(r"\beurope(an)?\b"),
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
    # Ironman 70.3 World Championships: series only, not recurring. The race
    # is at a different venue every year, so a single "recurring" group would
    # just duplicate the series page. recurring_events.py also has a guard
    # so the fuzzy fallback doesn't re-cluster the identically-named events.
    SeriesRule("im-703-world-championships",
                                      name_regex(r"ironman\s+70\.?3\s+world\s+championship")),
    # WT Long Distance Championships: same shape — annual championship at a
    # rotating venue, so it's a series, not a recurring. Includes the older
    # "ITU Long Distance" naming (PTO-scraped pre-rebrand editions) since
    # World Triathlon was ITU before 2020.
    SeriesRule("wt-long-distance-championships",
                                      any_of(
                                          name_regex(r"\b(wt|world\s+triathlon)\s+long\s+distance\s+championship"),
                                          name_regex(r"\bitu\s+long\s+distance\b"),
                                      )),
    # Ironman (full) World Championships: historically Kona only, St George 2022,
    # then Kona + Nice alternating genders from 2023. Names vary, so match each form.
    SeriesRule("im-world-championships",
                                      any_of(
                                          name_regex(r"ironman\s+hawaii"),
                                          name_regex(r"ironman\s+st\.?\s*george\s+world\s+championship"),
                                          name_regex(r"ironman\s+world\s+championship.*\bnice\b"),
                                      ),
                                      recurring=True),
    # T100 Triathlon World Tour. Only events explicitly branded "T100" -
    # the predecessor PTO Opens, Collins Cup, Challenge Daytona/Miami,
    # Clash Daytona, and Hervey Bay 100 share the 100 km distance for
    # rating purposes (see ratings.py distance enum) but they are not
    # part of the T100 series. Each of those gets its own recurring-only
    # rule below so they still group across editions on the recurring
    # detail page.
    SeriesRule("t100",                name_regex(r"\bT100\b"),
                                      recurring=True),
    # Standalone 100 km / PTO-era events. series_slug=None so these do
    # not show up under any series, only as recurring events.
    SeriesRule(None, name_regex(r"^\s*Collins\s+Cup\s*$"),
               recurring=True, recurring_slug="collins-cup", recurring_name="Collins Cup"),
    SeriesRule(None, name_regex(r"^\s*PTO\s+US\s+Open\s*$"),
               recurring=True, recurring_slug="pto-us-open", recurring_name="PTO US Open"),
    SeriesRule(None, name_regex(r"^\s*PTO\s+Canadian\s+Open\s*$"),
               recurring=True, recurring_slug="pto-canadian-open", recurring_name="PTO Canadian Open"),
    SeriesRule(None, name_regex(r"^\s*PTO\s+Asian\s+Open\s*$"),
               recurring=True, recurring_slug="pto-asian-open", recurring_name="PTO Asian Open"),
    SeriesRule(None, name_regex(r"^\s*PTO\s+European\s+Open\s*$"),
               recurring=True, recurring_slug="pto-european-open", recurring_name="PTO European Open"),
    SeriesRule(None, name_regex(r"^\s*Challenge\s+Daytona\s*$"),
               recurring=True, recurring_slug="challenge-daytona", recurring_name="Challenge Daytona"),
    SeriesRule(None, name_regex(r"^\s*Challenge\s+Miami\s*$"),
               recurring=True, recurring_slug="challenge-miami", recurring_name="Challenge Miami"),
    SeriesRule(None, name_regex(r"^\s*Clash\s+Daytona\s*$"),
               recurring=True, recurring_slug="clash-daytona", recurring_name="Clash Daytona"),
    SeriesRule(None, name_regex(r"^\s*Hervey\s+Bay\s+100\s*$"),
               recurring=True, recurring_slug="hervey-bay-100", recurring_name="Hervey Bay 100"),
    # Development Regional Cup: regional development tier below the
    # standard World Cup. Listed before world-cup; the world-cup regex
    # requires contiguous "world (triathlon )?cup" which these names
    # don't have, but ordering is defensive.
    SeriesRule("dev-regional-cup",    name_regex(r"(development\s+regional|regional\s+development)\s+cup"),
                                      recurring=True),
    # World Cup. Matches:
    #   - "World Cup" / "World Triathlon Cup" naming
    #   - WTS-era "ITU World Triathlon {Venue}" (2009-2020 World Triathlon
    #     Series rounds). These names contain no other modifier — anything
    #     with championship/series/cup/junior/youth/u23/age/para/development/
    #     regional/relay/grand-final is some other category and excluded.
    # Excludes "World Championships" outright.
    SeriesRule("world-cup",           all_of(
                                          any_of(
                                              name_regex(r"world (triathlon )?cup"),
                                              all_of(
                                                  name_regex(r"world\s+triathlon\b"),
                                                  not_(name_regex(
                                                      r"\b(championship|championships|series|"
                                                      r"junior|youth|u23|age[\s-]?group|para|"
                                                      r"development|regional|relay|grand)\b"
                                                  )),
                                              ),
                                          ),
                                          not_(name_regex(r"world championships")),
                                      ),
                                      recurring=True),
    # Continental rules key off the event *name* rather than the host's continent:
    # continental championships occasionally cross geographies (e.g. Asia Triathlon
    # ran its 2025 Championships in Istanbul, which lives in Europe on our map).
    # "Panamerican" / "Pan-American" is the Americas federation's older branding.
    SeriesRule("european-champs",     all_of(has_cat_id(CAT_CONTINENTAL_CHAMPS), name_regex(r"\beurope(an)?\b")),                     recurring=True),
    SeriesRule("african-champs",      all_of(has_cat_id(CAT_CONTINENTAL_CHAMPS), name_regex(r"\bafrica(n)?\b")),                      recurring=True),
    SeriesRule("americas-champs",     all_of(has_cat_id(CAT_CONTINENTAL_CHAMPS), name_regex(r"\bamerica(n|s)?\b|\bpan[\s-]?american\b")), recurring=True),
    SeriesRule("asian-champs",        all_of(has_cat_id(CAT_CONTINENTAL_CHAMPS), name_regex(r"\basia(n)?\b")),                        recurring=True),
    SeriesRule("oceania-champs",      all_of(has_cat_id(CAT_CONTINENTAL_CHAMPS), name_regex(r"\boceania\b")),                         recurring=True),
    SeriesRule("european-cup",        all_of(has_cat_id(CAT_CONTINENTAL_CUP), name_regex(r"\beurope(an)?\b")),                        recurring=True),
    SeriesRule("african-cup",         all_of(has_cat_id(CAT_CONTINENTAL_CUP), name_regex(r"\bafrica(n)?\b")),                         recurring=True),
    SeriesRule("americas-cup",        all_of(has_cat_id(CAT_CONTINENTAL_CUP), name_regex(r"\bamerica(n|s)?\b|\bpan[\s-]?american\b")), recurring=True),
    SeriesRule("asian-cup",           all_of(has_cat_id(CAT_CONTINENTAL_CUP), name_regex(r"\basia(n)?\b")),                           recurring=True),
    SeriesRule("oceania-cup",         all_of(has_cat_id(CAT_CONTINENTAL_CUP), name_regex(r"\boceania\b")),                            recurring=True),
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
    r'national|federation|fisu|commonwealth|university|universiade|regional|'
    r'ironman|challenge|'
    # Common sponsor / org / regional-qualifier tokens. Stripping these
    # collapses sponsor-polluted slugs ("Hamburg BG", "AJ Bell Leeds",
    # "Dextro Energy Beijing", "Barfoot and Thompson Auckland") onto the
    # plain venue, and merges regional-qualifier names like "{venue} PATCO
    # Triathlon Pan-American Central American and Caribbean Cup" down to
    # the venue. "and" is stripped so that multi-tier names like
    # "U23 and Youth European Championships" reduce to the venue.
    r'aj|bell|dextro|energy|barfoot|thompson|bg|wasser|'
    r'patco|astc|otu|panamerican|iberoamerican|'
    r'pan|central|caribbean|yog|qualifier|and'
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

    series_rows = conn.execute("SELECT slug, series_id, name FROM series").fetchall()
    series_lookup = {slug: sid for slug, sid, _ in series_rows}
    series_names  = {slug: name for slug, _, name in series_rows}
    for rule in RULES:
        if rule.series_slug is None:
            continue  # recurring-only rule, no series link required
        if rule.series_slug not in series_lookup:
            raise RuntimeError(
                f"series_rules references unknown series slug '{rule.series_slug}'. "
                "Add it to ptd_data/data/series.csv."
            )

    # cat_ids / event_spec_ids are per-race. For dual-tier events (e.g. an
    # elite + age-group event sharing one event_id, where each tier has a
    # different cat_id set on its races) we need the UNION across races,
    # not ANY_VALUE — otherwise the AG cat (483) gets dropped half the
    # time and the AG-tier rules miss the event.
    events = conn.execute("""
        SELECT e.event_id, e.name, e.country, e.continent,
               STRING_AGG(r.cat_ids,        '|') AS cat_ids_concat,
               STRING_AGG(r.event_spec_ids, '|') AS spec_ids_concat
        FROM events e
        LEFT JOIN races r ON r.event_id = e.event_id
        GROUP BY e.event_id, e.name, e.country, e.continent
    """).fetchall()

    def _union_ids(concat):
        if not concat:
            return frozenset()
        out = set()
        for piece in concat.split('|'):
            try:
                out.update(literal_eval(piece) if piece else [])
            except (ValueError, SyntaxError):
                continue
        return frozenset(out)

    per_series = {}  # slug -> count
    recurring_rows = {}  # recurring_id -> (slug, name, venue_key)
    event_links = []     # (event_id, series_id)
    event_recurring = {} # event_id -> recurring_id (first recurring-aware match wins)

    for event_id, name, country, continent, cat_ids_concat, spec_ids_concat in events:
        cat_ids  = _union_ids(cat_ids_concat)
        spec_ids = _union_ids(spec_ids_concat)

        row = EventRow(event_id, name or '', country or '', continent or '', cat_ids, spec_ids)

        for rule in RULES:
            if not rule.match(row):
                continue
            if rule.series_slug is not None:
                event_links.append((event_id, series_lookup[rule.series_slug]))
                per_series[rule.series_slug] = per_series.get(rule.series_slug, 0) + 1

            if rule.recurring and event_id not in event_recurring:
                vkey = venue_key(name or '')
                # Recurring-only rules supply explicit slug + display
                # name; series-bound rules derive both from venue + series.
                if rule.recurring_slug:
                    rslug = rule.recurring_slug
                    rname = rule.recurring_name or db._title_from_slug(rslug)
                else:
                    rslug = f"{vkey}-{rule.series_slug}"
                    rname = f"{db._title_from_slug(vkey)} {series_names[rule.series_slug]}"
                rid = db.slug_id(rslug)
                recurring_rows[rid] = (rslug, rname, vkey)
                event_recurring[event_id] = rid

    # Suppress one-off recurring rows: a "recurring" with a single edition
    # (e.g. Vichy 2024 European Champs, Torremolinos 2024 AG Worlds) just
    # duplicates the event page. Drop both the recurring_events row and
    # the event_recurring assignment so the event has no recurring link.
    from collections import Counter
    rid_event_count = Counter(event_recurring.values())
    keep_rids = {rid for rid, n in rid_event_count.items() if n >= 2}
    recurring_rows  = {rid: v for rid, v in recurring_rows.items() if rid in keep_rids}
    event_recurring = {eid: rid for eid, rid in event_recurring.items() if rid in keep_rids}

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
    recurring_only = sum(1 for r in RULES if r.series_slug is None and r.recurring)
    print(f"Rule-based series: {len(event_links)} mappings, "
          f"{len(recurring_rows)} recurring groups "
          f"({recurring_only} recurring-only rules registered)")


if __name__ == "__main__":
    conn = db.get_conn(read_only=False)
    db.load_series_defs(conn)
    apply(conn)
    db.load_event_series_csv(conn)
    conn.close()
