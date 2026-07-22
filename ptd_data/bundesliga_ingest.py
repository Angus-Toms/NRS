"""
German Triathlon-Bundesliga ingest — pulls the 1. Triathlon-Bundesliga (top-tier
draft-legal short-course club league, run by Deutsche Triathlon gGmbH / DTU) from
the race|result timing platform and inserts the individual results into the
shared DB schema alongside WT / FGP short-course data.

Usage:
    python -m ptd_data.bundesliga_ingest

Runs after the FGP ingest and before the PTO ingest (see scripts/build_db.sh):
same rationale as FGP — Bundesliga athletes must exist before PTO matching scans
unlinked rows, and matching wants the freshest WT + FGP roster (many Bundesliga
starters also race the French Grand Prix and World Triathlon).

Source shape (verified against race|result):
  - Each race weekend is a SEPARATE race|result event with its own numeric id,
    contest numbering, and result-list name — there is no single uniform feed
    (unlike the French series' one API). So RACES below is a hand-maintained
    registry, one row per race, carrying the event id + the individual-list
    name + which contest numbers are the 1. Bundesliga men/women. Only races
    published on race|result are covered; Hannover (mikatiming) and the
    pre-race|result / PDF-only history are out of scope for now.
  - Config: GET {API}/{event}/RRPublish/data/config?page=results  -> {key, contests, lists}
  - Results: GET {API}/{event}/RRPublish/data/list?key=..&listname=..&contest=N&r=all&l=0
    (301-redirects to /{event}/results/list; requests follows it). Returns
    {list:{Fields:[{Label,Expression}...]}, data:[[...],...]}. Each data row has
    two leading internal columns, then one value per Field in order, so
    data column for field i = (len(row) - len(Fields)) + i. Columns are located
    by Field label (aliases below) so a reordered list still parses.

Fields available per athlete: finish place, bib, name ("LASTNAME Firstname"),
nationality (IOC code), club, and swim / T1 / bike / T2 / run / total splits.
There is NO date of birth in the published lists, so athlete matching runs on
name + nationality only (same matcher as fgp_ingest / pto_ingest: exact
name+country, fold-key transliteration equality, added/removed-name token
subset, then fuzzy scoring — the yob-dependent branches are inert here). New
athletes are minted keyed on name+country; plausible-but-unconfirmed pairs go to
data/bundesliga_merge_candidates.csv for review via data/athlete_merges.csv.
"""

import csv
import re
import time
from pathlib import Path

import pycountry
import requests

from ptd_data import db
from ptd_data.fgp_ingest import IOC_TO_ISO, _ALPHA3_NAME_OVERRIDES, _loc_slug
from ptd_data.pto_ingest import (
    _NAME_SIM_GAP,
    _NAME_SIM_NICK,
    _fold_key,
    _name_similarity,
    _normalize_name,
    _parse_time,
    _unique_subset_match,
)

FETCH_DELAY = 0.3  # race|result asks for <= ~1 req/s; stay well under

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; ptd-ingest/1.0)",
    "Accept-Language": "en-US,en;q=0.9",
})

# Per-race registry. One entry per 1. Bundesliga race published on race|result.
#   event : race|result event id.
#   host  : race|result subdomain ("my" default, some events on "my4").
#   list  : exact individual-results list name (the "|" and spacing are part of it).
#   men / women : contest numbers for the 1. Bundesliga men's / women's contest.
# Contest numbering and list names vary per event, so each is recorded explicitly
# (verified against the config endpoint when added). Extend by adding rows.
# Known gaps (different platform / no clean individual list, not ingestible here):
# Kraichgau (all years, on racepedia.de), Hannover (all years, mikatiming), and
# the 2022-2024 "Die Finals" rounds (Berlin/Düsseldorf/Dresden, datasport).
_STD_LIST = "Ergebnislisten|Ergebnisliste BuLi Einzel"
RACES = [
    {"season": 2022, "venue": "Schliersee", "date": "2022-07-17", "event": "211249", "list": _STD_LIST, "men": 1, "women": 2},
    # Nürnberg 2022 ran Prolog + Verfolgung; contests 3/4 are the pursuit final.
    {"season": 2022, "venue": "Nürnberg",   "date": "2022-08-07", "event": "213808", "list": _STD_LIST, "men": 3, "women": 4},
    {"season": 2023, "venue": "Schliersee", "date": "2023-06-25", "event": "250162", "list": _STD_LIST, "men": 1, "women": 2},
    {"season": 2023, "venue": "Tübingen",   "date": "2023-07-23", "event": "253500", "list": _STD_LIST, "men": 1, "women": 2},
    {"season": 2024, "venue": "Tübingen",   "date": "2024-07-21", "event": "295241", "list": _STD_LIST, "men": 1, "women": 2},
    {"season": 2025, "venue": "Tübingen",   "date": "2025-07-20", "event": "351344", "list": _STD_LIST, "men": 1, "women": 2},
    {"season": 2025, "venue": "Allgäu",     "date": "2025-08-15", "event": "353270",
     "list": "15 TEAMWERTUNG Buli|Ergebnisliste BuLi Einzel", "men": 10, "women": 11},
    # Dresden 2025 "Die Finals" — on the my4 host, Elite contests 30/31.
    {"season": 2025, "venue": "Dresden",    "date": "2025-08-03", "event": "349784", "host": "my4",
     "list": "14 Ergebnislisten UW2 Buli|Ergebnisliste OA UW2 BuLi", "men": 30, "women": 31},
]

# race|result Field labels -> our column keys. Matched case-insensitively; first
# alias hit wins. Lets a reordered/renamed list still parse.
_COL_ALIASES = {
    "place": ("platz", "rang", "pos", "place"),
    "name":  ("name", "athlet", "athlete", "anzeigename"),
    "nat":   ("nat", "nation", "land", "country"),
    "club":  ("verein", "team", "club", "mannschaft"),
    "swim":  ("swim", "schwimmen", "natation"),
    "t1":    ("t1", "wechsel1"),
    "bike":  ("bike", "rad", "radfahren", "velo"),
    "t2":    ("t2", "wechsel2"),
    "run":   ("run", "laufen", "lauf", "course"),
    "total": ("total", "gesamt", "finish", "endzeit", "zeit"),
}


# ---------------------------------------------------------------------------
# HTTP + parsing helpers
# ---------------------------------------------------------------------------

def _api(path, host="my", retries=3):
    """GET a race|result JSON endpoint (follows the data/list 301). Raises on
    failure — a moved/removed endpoint should stop the run, not ingest nothing."""
    url = f"https://{host}.raceresult.com{path}"
    for attempt in range(1, retries + 1):
        time.sleep(FETCH_DELAY)
        try:
            resp = _session.get(url, timeout=30, allow_redirects=True)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            print(f"  [HTTP] attempt {attempt}/{retries} failed for {url}: {e}")
            if attempt == retries:
                raise
            time.sleep(attempt)


def _col_index_map(fields, row_len):
    """Map our column keys -> data-row index, via Field labels + the leading
    two internal columns (offset = row_len - len(fields))."""
    offset = row_len - len(fields)
    labels = [str(f.get("Label", "")).strip().lower() for f in fields]
    out = {}
    for key, aliases in _COL_ALIASES.items():
        for i, lbl in enumerate(labels):
            if lbl in aliases:
                out[key] = offset + i
                break
    return out


def _split_name(display):
    """'DEVAY Mark' -> 'Mark Devay'. The race|result display name is
    LASTNAME (all-caps) followed by the given name(s); reorder to First Last and
    title-case. Falls back to a plain title-case if the pattern doesn't hold."""
    parts = display.strip().split()
    surname, given, i = [], [], 0
    while i < len(parts) and parts[i] == parts[i].upper() and any(c.isalpha() for c in parts[i]):
        surname.append(parts[i]); i += 1
    given = parts[i:]
    if surname and given:
        return " ".join(given + surname).title()
    return display.strip().title()


def _split_seconds(cell):
    """Parse a split cell like '24:25' or '08:26 (3)' to seconds (0 if blank)."""
    if not cell:
        return 0.0
    token = str(cell).strip().split()[0] if str(cell).strip() else ""
    return _parse_time(token)


def _parse_status(place):
    """race|result place cell -> (position, result_status_enum)."""
    s = str(place).strip().rstrip(".")
    if s.isdigit():
        return int(s), "Finished"
    return None, {"DNF": "DNF", "DNS": "DNS", "DSQ": "DQ"}.get(s.upper(), "NC")


def _buli_handle(venue, season):
    """Event-level short handle "{Venue} {Abbr} {YY}" (cf. "Melilla CC 24").
    Gender is race-level (in prog_name/gender), not in the handle — the men's
    and women's races of an event share it."""
    return f"{venue} BuLi {season % 100:02d}"


# ---------------------------------------------------------------------------
# Ingester
# ---------------------------------------------------------------------------

class BundesligaIngester:
    def __init__(self, conn):
        self.conn = conn
        # Match against ALL existing athletes by ISO alpha3 (WT + FGP + PTO).
        rows = conn.execute("""
            SELECT a.athlete_id, a.name, a.year_of_birth, n.alpha3
            FROM athletes a JOIN nationalities n USING (country_full)
        """).fetchall()
        self._candidates = {}
        for athlete_id, name, yob, alpha3 in rows:
            self._candidates.setdefault(alpha3, []).append((athlete_id, name, yob))
        print(f"Loaded {len(rows)} match candidates")
        # alpha3 -> canonical country_full (most-used name wins, overrides trump).
        self._alpha3_names = {}
        for country_full, alpha3, _ in conn.execute("""
            SELECT n.country_full, n.alpha3, COUNT(a.athlete_id) AS c
            FROM nationalities n LEFT JOIN athletes a USING (country_full)
            GROUP BY 1, 2 ORDER BY c
        """).fetchall():
            self._alpha3_names[alpha3] = country_full
        self._alpha3_names.update(_ALPHA3_NAME_OVERRIDES)
        self._merge_candidates = []

    # -- country ------------------------------------------------------------

    def _country_from_code(self, code):
        alpha3 = IOC_TO_ISO.get(code, code)
        if alpha3 in self._alpha3_names:
            return self._alpha3_names[alpha3]
        country = pycountry.countries.get(alpha_3=alpha3)
        if country is None:
            raise RuntimeError(f"Unknown nation code {code!r} — extend IOC_TO_ISO")
        self._alpha3_names[alpha3] = country.name
        return country.name

    # -- athlete resolution (mirrors fgp_ingest, no source id / no yob) ------

    def _resolve_athlete(self, name, nation_code, gender):
        """Return (athlete_id, country_full, is_new)."""
        alpha3 = IOC_TO_ISO.get(nation_code, nation_code)
        country_full = self._country_from_code(nation_code)

        matched = self._match_existing(name, alpha3, country_full)
        if matched:
            return matched, country_full, False

        # Mint keyed on name+country; collision with the same name is the same
        # athlete showing up in another race — reuse the row already minted.
        slug = f"buli-{_loc_slug(_normalize_name(name))}-{alpha3.lower()}"
        athlete_id = db.slug_id(slug)
        collider = self.conn.execute(
            "SELECT name FROM athletes WHERE athlete_id = ?", [athlete_id]).fetchone()
        if collider:
            if _normalize_name(collider[0]) == _normalize_name(name):
                return athlete_id, country_full, False
            raise RuntimeError(
                f"slug_id collision: {slug!r} hashes to {athlete_id} "
                f"already held by {collider[0]!r}")

        db.upsert_nationality(self.conn, country_full)
        db.upsert_athlete(self.conn, athlete_id, name, country_full, 0, "", gender)
        print(f"    New Bundesliga athlete: {name!r} ({country_full})  id={athlete_id}")
        return athlete_id, country_full, True

    def _match_existing(self, name, alpha3, country_full):
        """Match against existing athletes. Same logic as fgp_ingest: country
        prefilter, exact name+country, fold-key transliteration equality,
        added/removed-name token subset. No yob is published for the Bundesliga,
        so the yob-dependent fuzzy branch never fires; the near-miss goes to the
        merge-candidate review file instead."""
        candidates = self._candidates.get(alpha3, [])
        if not candidates:
            return None

        norm = _normalize_name(name)
        exact = [c for c in candidates if _normalize_name(c[1]) == norm]
        if len(exact) == 1:
            return self._accept(exact[0], "exact_name_country", name)

        fk = _fold_key(name)
        fold_hits = [c for c in candidates if _fold_key(c[1]) == fk]
        if len(fold_hits) == 1:
            return self._accept(fold_hits[0], "translit", name)

        subset = _unique_subset_match(name, candidates)
        if subset:
            return self._accept(subset, "subset", name)

        self._suggest_candidate(candidates, name, country_full)
        return None

    def _accept(self, candidate, confidence, name):
        athlete_id, db_name, _ = candidate
        print(f"    Matched {name!r} -> existing athlete {db_name!r} "
              f"(id={athlete_id}) [{confidence}]")
        return athlete_id

    def _suggest_candidate(self, candidates, name, country_full):
        scored = sorted(((c, _name_similarity(c[1], name)) for c in candidates),
                        key=lambda x: -x[1])
        top, top_sim = scored[0]
        runner_up = scored[1][1] if len(scored) > 1 else 0.0
        if top_sim < 0.85 or top_sim - runner_up < _NAME_SIM_GAP:
            return
        self._merge_candidates.append({
            "db_athlete_id": top[0], "db_name": top[1],
            "buli_name": name, "country_full": country_full,
            "similarity": round(top_sim, 3),
        })
        print(f"    MERGE CANDIDATE: {name!r} ~ {top[1]!r} "
              f"(id={top[0]}) sim={top_sim:.2f}")

    def _dump_merge_candidates(self):
        path = Path(__file__).parent / "data" / "bundesliga_merge_candidates.csv"
        resolved = set()
        merges = path.parent / "athlete_merges.csv"
        if merges.exists():
            with open(merges, newline="") as f:
                for r in csv.DictReader(f):
                    resolved.add(str(r.get("keep_athlete_id", "")).strip())
        # Dedupe: the same near-miss athlete recurs across races, so keep one
        # row per (db athlete, minted name) pair, and drop pairs already
        # actioned in athlete_merges.csv.
        seen, rows = set(), []
        for c in self._merge_candidates:
            k = (str(c["db_athlete_id"]), c["buli_name"])
            if k in seen or str(c["db_athlete_id"]) in resolved:
                continue
            seen.add(k)
            rows.append(c)
        if not rows:
            if path.exists():
                path.unlink()
            return
        cols = ["db_athlete_id", "db_name", "buli_name", "country_full", "similarity"]
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in sorted(rows, key=lambda x: -x["similarity"]):
                w.writerow(r)
        print(f"Wrote {len(rows)} merge candidates to {path.name} for review")

    # -- race ingest --------------------------------------------------------

    def run(self):
        print()
        print("=" * 60)
        print("BUNDESLIGA INGEST  —  1. Triathlon-Bundesliga (race|result)")
        print("=" * 60)

        known = {r[0] for r in self.conn.execute("SELECT race_id FROM races").fetchall()}
        ingested = skipped = 0
        for race in sorted(RACES, key=lambda r: r["date"]):
            key = None  # fetched lazily, only when a race actually needs ingesting
            for gender, contest in (("male", race["men"]), ("female", race["women"])):
                race_id = db.slug_id(
                    f"buli-{race['season']}-{_loc_slug(race['venue'])}-{gender}")
                if race_id in known:
                    # Self-heal the handle so a format change reaches already-
                    # ingested races (insert path is skipped for them).
                    self.conn.execute("UPDATE races SET race_handle = ? WHERE race_id = ?",
                                      [_buli_handle(race["venue"], race["season"]), race_id])
                    skipped += 1
                    continue
                if key is None:
                    key = self._event_key(race["event"], race.get("host", "my"))
                n = self._ingest_race(race, key, gender, contest, race_id)
                ingested += 1 if n else 0

        print()
        print("=" * 60)
        print(f"DONE  race-genders ingested={ingested}  already-present={skipped}")
        print("=" * 60)
        self._dump_merge_candidates()
        db.reconcile_athlete_nationality(self.conn)

    def _event_key(self, event, host):
        cfg = _api(f"/{event}/RRPublish/data/config?page=results", host)
        return cfg["key"]

    def _ingest_race(self, race, key, gender, contest, race_id):
        from urllib.parse import quote
        payload = _api(f"/{race['event']}/RRPublish/data/list?key={key}"
                       f"&listname={quote(race['list'])}&page=results&contest={contest}"
                       f"&r=all&l=0", race.get("host", "my"))
        fields = payload.get("list", {}).get("Fields") or []
        rows = payload.get("data") or []
        if not fields or not rows:
            print(f"  {race['season']} {race['venue']} {gender}: no data — skipping")
            return 0
        cols = _col_index_map(fields, len(rows[0]))
        if "place" not in cols or "name" not in cols:
            raise RuntimeError(
                f"{race['venue']} {race['season']} {gender}: could not locate "
                f"place/name columns in {[f.get('Label') for f in fields]}")

        def cell(row, col):
            i = cols.get(col)
            return row[i] if i is not None and i < len(row) else ""

        result_rows = []
        new_count = 0
        for row in rows:
            raw_name = str(cell(row, "name")).strip()
            nation_code = str(cell(row, "nat")).strip()
            # Non-starter placeholder rows carry a status word in the name cell
            # and no nation — skip them (real DNFs keep a name + nation + splits).
            if not nation_code or raw_name.upper() in ("DNS", "DNF", "DSQ", "LAP", ""):
                continue
            name = _split_name(raw_name)
            if not name:
                continue
            athlete_id, country_full, is_new = self._resolve_athlete(name, nation_code, gender)
            new_count += is_new
            position, status = _parse_status(cell(row, "place"))
            swim = _split_seconds(cell(row, "swim"))
            t1 = _split_seconds(cell(row, "t1"))
            bike = _split_seconds(cell(row, "bike"))
            t2 = _split_seconds(cell(row, "t2"))
            run = _split_seconds(cell(row, "run"))
            total = _split_seconds(cell(row, "total"))
            result_rows.append((race_id, athlete_id, position, status, 0,
                                total, swim, bike, run, t1, t2))
            if is_new:
                db.record_athlete_nationality(self.conn, athlete_id, country_full, race["date"])

        # Event + race rows last, so a crash mid-resolution can't leave a
        # results-less race that a later run would skip as already ingested.
        event_id = db.slug_id(f"buli-{race['season']}-{_loc_slug(race['venue'])}")
        event_name = f"{race['season']} Triathlon Bundesliga {race['venue']}"
        db.upsert_nationality(self.conn, "Germany")
        db.insert_event(
            self.conn, event_id=event_id, name=event_name, venue=race["venue"],
            country="Germany", continent="Europe",
            start_date=race["date"], end_date=race["date"], longitude=0, latitude=0)
        db.insert_race(
            self.conn, race_id=race_id, event_id=event_id, race_title=event_name,
            prog_name="Elite Men" if gender == "male" else "Elite Women",
            race_date=race["date"], gender=gender, category="elite",
            sub_category="elite", cat_ids="[]", distance="sprint",
            race_handle=_buli_handle(race["venue"], race["season"]))
        db.insert_results_bulk(self.conn, result_rows)
        finishers = sum(1 for r in result_rows if r[2] is not None)
        print(f"  {race['season']} {race['venue']} {gender}: {len(result_rows)} results "
              f"({finishers} finishers, {new_count} new athletes)")
        return len(result_rows)


if __name__ == "__main__":
    conn = db.get_conn(read_only=False)
    try:
        BundesligaIngester(conn).run()
    finally:
        conn.close()
