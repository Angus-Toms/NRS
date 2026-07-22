"""
PTO ingest — scrapes stats.protriathletes.org and inserts long-course results
into the shared DB schema alongside WT short-course data.

Usage:
    python -m ptd_data.pto_ingest                      # full 1979-present
    python -m ptd_data.pto_ingest --year 2024          # single year
    python -m ptd_data.pto_ingest --athletes-only      # backfill athlete profiles

Each new PTO athlete's profile is fetched inline during race ingest so that
yob/height/weight/nickname are available at match-time (otherwise two
same-name same-country athletes can be conflated — the 1972 vs 1995 Thomas
Davies bug). The per-athlete fetch is cached in memory for the run.
"""

import argparse
import csv
import re
import time
import unicodedata
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from ptd_data import db

BASE_URL = "https://stats.protriathletes.org"
SCRAPE_DELAY = .15  # seconds between requests

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; ptd-ingest/1.0)",
    "Accept-Language": "en-US,en;q=0.9",
})

# PTO site labels → distance_enum values
_DISTANCE_MAP = {
    "iron (140.6 miles)": "long",
    "other long distances": "long",
    "long distance": "long",
    "itu long distance": "long",
    "half-iron (70.3 miles)": "middle",
    "other middle distances": "middle",
    "middle distance": "middle",
    "half iron": "middle",
    "70.3 miles": "middle",
    "100 km": "t100",
    "100km": "t100",
    "short course": "sprint",
}

# PTO brand labels → canonical brand slug stored in events.brand
_BRAND_MAP = {
    "t100 triathlon": "t100",
    "t100": "t100",
    "ironman": "ironman",
    "iron man": "ironman",
    "challenge": "challenge",
    "independent": "independent",
}

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url, retries=3):
    """Fetch URL with rate limiting and basic retry."""
    for attempt in range(1, retries + 1):
        time.sleep(SCRAPE_DELAY)
        try:
            resp = _session.get(url, timeout=30)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml")
        except requests.RequestException as e:
            print(f"  [HTTP] Attempt {attempt}/{retries} failed for {url}: {e}")
            if attempt == retries:
                raise
            time.sleep(SCRAPE_DELAY * attempt)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_distance(label):
    """Map PTO distance label to distance_enum value, or None if unrecognised."""
    return _DISTANCE_MAP.get(label.strip().lower())


def _parse_brand(label):
    """Map PTO brand label to canonical slug."""
    for key, slug in _BRAND_MAP.items():
        if key in label.strip().lower():
            return slug
    return "independent"


def _parse_time(s):
    """Convert HH:MM:SS or MM:SS string to seconds. Returns 0.0 on failure."""
    if not s or not s.strip() or s.strip() == "-":
        return 0.0
    parts = s.strip().split(":")
    try:
        if len(parts) == 3:
            h, m, sec = map(float, parts)
            return h * 3600 + m * 60 + sec
        if len(parts) == 2:
            m, sec = map(float, parts)
            return m * 60 + sec
        return float(parts[0])
    except ValueError:
        return 0.0


def _normalize_name(name):
    """Lowercase, strip accents, collapse whitespace — used for WT matching."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = nfkd.encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", ascii_name.lower().strip())


def _name_similarity(a, b):
    """0-1 similarity on normalised names; 1.0 == identical."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, _normalize_name(a), _normalize_name(b)).ratio()


def _name_tokens(name):
    """Accent-folded, lowercased word tokens of a name (hyphens split too)."""
    return frozenset(t for t in re.split(r"[^a-z0-9]+", _normalize_name(name)) if t)


# Letters that NFKD can't decompose to ASCII, plus the spelled-out umlaut/eszett
# forms PTO/WT use interchangeably. Pre-mapped before NFKD so nothing is dropped.
_TRANSLIT_PRE = str.maketrans({
    "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe",
    "ł": "l", "Ł": "l", "đ": "d", "Đ": "d", "ð": "d", "þ": "th", "ı": "i", "ß": "ss",
})


def _fold_key(name):
    """Transliteration-insensitive key; equal keys ⇒ accent/spelling variant.

    Folds diacritics, the spelled-out umlaut digraphs (ue/oe/ae → u/o/a), and
    strips spacing/punctuation — so 'Müller-Hörner' and 'Mueller-Horner' share
    a key, while a genuine letter change ('Paul' vs 'Paula', 'Owen' vs 'Cowen',
    'Simon' vs 'Simone') does not. This is what makes the missing-yob auto-link
    safe where a raw similarity score cannot be: a one-character edit in a short
    name scores ~0.95 whether it's an accent or a different person.
    """
    s = name.translate(_TRANSLIT_PRE)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    for dig, rep in (("ue", "u"), ("oe", "o"), ("ae", "a")):
        s = s.replace(dig, rep)
    return re.sub(r"[^a-z0-9]", "", s)


def _unique_subset_match(name, candidates):
    """Return the single candidate whose name is an added/removed-name variant.

    'name' matches candidate c when one's token set is a subset of the other's
    ('Lars Thomsen' ⊆ 'Lars Ole Thomsen'; equal sets differing only in
    punctuation also qualify). This is the safe discriminator for the
    added/removed-name case: it accepts a dropped/added forename or middle
    name but rejects a swapped surname ('Daniel Luna' vs 'Daniel Pla', whose
    token sets are disjoint), which pure similarity cannot separate. Requires
    a unique qualifying candidate and at least two tokens a side so a lone
    shared surname can't trigger a match. `candidates` are (id, name, yob).
    """
    a = _name_tokens(name)
    if len(a) < 2:
        return None
    hits = [c for c in candidates
            if len(b := _name_tokens(c[1])) >= 2 and (a <= b or b <= a)]
    return hits[0] if len(hits) == 1 else None


# Thresholds for fuzzy name matching during ingest. Mirrors pto_matcher.py's
# values so behaviour is consistent whether an athlete is linked at ingest
# time or later via overlap-race matching.
_NAME_SIM_NICK = 0.70   # treated as a valid fuzzy match
_NAME_SIM_GAP  = 0.10   # margin required over second-best to accept a match


# ---------------------------------------------------------------------------
# Main ingester
# ---------------------------------------------------------------------------

class PTOIngester:
    def __init__(self, conn):
        self.conn = conn
        # Load existing WT athletes once for matching: {norm_name -> [(id, country, yob)]}
        self._wt_athletes = self._load_wt_athletes()
        print(f"Loaded {len(self._wt_athletes)} unique normalised athlete names from WT data")
        # Per-run in-memory cache of profile fetches so an athlete showing up
        # in multiple races in the same run is only fetched once.
        self._profile_cache: dict[str, dict] = {}
        # Same-name same-country WT athletes we suspected but refused to
        # auto-merge (usually because PTO had no yob). Written to
        # data/merge_candidates.csv at end of run for manual review.
        self._merge_candidates: list[dict] = []

    def _load_wt_athletes(self):
        """Build a country-keyed lookup of unmatched WT athletes for fuzzy name+yob matching.

        Returns {country_full: [(athlete_id, name, yob), ...]}. Indexing by
        country lets _match_wt scan a tiny subset per lookup rather than the
        full roster, keeping fuzzy matching cheap.
        """
        rows = self.conn.execute(
            "SELECT athlete_id, name, country_full, year_of_birth FROM athletes "
            "WHERE pto_slug IS NULL"  # only unmatched WT athletes
        ).fetchall()
        index = {}
        for athlete_id, name, country, yob in rows:
            index.setdefault(country, []).append((athlete_id, name, yob))
        return index

    def run(self, years=None):
        if years is None:
            years = list(range(1979, date.today().year + 1))

        print()
        print("=" * 60)
        print(f"PTO INGEST  —  years: {years}")
        print("=" * 60)

        all_races = []
        for year in years:
            races = self._discover_races(year)
            all_races.extend(races)

        print()
        print(f"Total races discovered across all years: {len(all_races)}")

        # Decide what to skip, annotate each race with a reason, and dump the
        # full discovery set to all_pto_events.csv for overview (mirrors the
        # all_events.csv dump the WT ingest produces).
        known_race_ids = {
            r[0] for r in self.conn.execute(
                "SELECT race_id FROM races WHERE distance IN ('middle','t100','long')"
            ).fetchall()
        }
        for race in all_races:
            race["skip_reason"] = self._skip_reason(race, known_race_ids)

        _dump_races_csv(all_races, "all_pto_events.csv")
        print(f"Wrote all_pto_events.csv ({len(all_races)} rows)")

        # Breakdown of skips for at-a-glance summary
        skip_counts = {}
        for r in all_races:
            skip_counts[r["skip_reason"] or "(to ingest)"] = (
                skip_counts.get(r["skip_reason"] or "(to ingest)", 0) + 1
            )
        for reason, n in sorted(skip_counts.items(), key=lambda x: -x[1]):
            print(f"  {n:5d}  {reason}")

        new_races = [r for r in all_races if r["skip_reason"] is None]
        # Ingest oldest → newest so nationality history builds a correct timeline.
        new_races.sort(key=lambda r: r["sort_date"])
        print(f"To ingest: {len(new_races)}")

        ingested = 0
        skipped = 0
        for i, race in enumerate(new_races, 1):
            print()
            print(f"[{i}/{len(new_races)}] {race['name']} ({race['slug']}/{race['year']})")
            try:
                ok = self._ingest_race(race)
                if ok:
                    ingested += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"  ERROR: {e}")
                skipped += 1

        print()
        print("=" * 60)
        print(f"DONE  ingested={ingested}  skipped={skipped}")
        print("=" * 60)
        self._dump_merge_candidates()
        db.reconcile_athlete_nationality(self.conn)

    def _dump_merge_candidates(self):
        """Write same-name same-country WT/PTO pairs we refused to auto-merge.

        Reviewer copies confirmed rows into athlete_merges.csv. Overwritten
        each run so the file always reflects the latest ingest's findings.
        """
        path = Path(__file__).parent / 'data' / 'merge_candidates.csv'
        if not self._merge_candidates:
            if path.exists():
                path.unlink()
            return
        cols = ["wt_athlete_id", "wt_name", "wt_yob", "pto_name", "pto_yob",
                "country_full", "similarity", "reason"]
        # Sort by similarity descending so the strongest candidates surface first.
        rows = sorted(self._merge_candidates, key=lambda x: -x["similarity"])
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"Wrote {len(rows)} merge candidates to {path.name} for manual review")

    def _skip_reason(self, race, known_race_ids):
        """Why would we skip this race? Returns None if we'd ingest it."""
        if _is_wtcs_slug(race["slug"]):
            return "wtcs (covered by WT ingest)"
        if race["distance"] not in ("middle", "t100", "long"):
            return f"non-long-course ({race['distance']})"
        if race["race_id"] in known_race_ids:
            return "already in DB"
        return None

    # ------------------------------------------------------------------
    # Phase 1: discover races for one year
    # ------------------------------------------------------------------

    def _discover_races(self, year):
        """Scrape the results listing for one year and return a list of race dicts.

        Card structure on stats.protriathletes.org/results?year=YYYY:

            <div class="race-name-and-tier">
                <a class="racename" href="/race/<slug>/<year>/results">
                    <span><b>2025 Qatar T100 ...</b></span>
                </a>
                <span>Diamond Tier | 100 km</span>
            </div>

        Past races have the second span with "<Tier> Tier | <Distance>"; upcoming
        races use the `.upcoming-race` class instead and omit tier/distance — we
        skip those.
        """
        url = f"{BASE_URL}/results?year={year}"
        print(f"\nDiscovering {year} races from {url} ...")
        soup = _get(url)

        cards = soup.select("div.race-name-and-tier")
        print(f"  Found {len(cards)} past-race cards on the page")

        races = []
        seen_slugs = set()
        for card in cards:
            link = card.select_one("a.racename[href*='/race/']")
            if not link:
                continue
            m = re.match(r"/race/([^/]+)/(\d{4})/results", link.get("href", ""))
            if not m:
                continue
            slug, race_year = m.group(1), int(m.group(2))
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            name_raw = link.get_text(strip=True)
            # Names are prefixed with the year (e.g. "2025 Qatar T100 ..."); strip it
            name = re.sub(r"^\d{4}\s+", "", name_raw)

            # Second span in the card: "Diamond Tier | 100 km" or just "100 km"
            meta_spans = card.find_all("span", recursive=False)
            tier_distance_span = None
            for sp in meta_spans:
                txt = sp.get_text(" ", strip=True)
                if "|" in txt or any(w in txt.lower() for w in ("km", "iron", "sprint", "long", "middle", "half", "mile")):
                    tier_distance_span = sp
                    break

            tier = ""
            distance_label = ""
            if tier_distance_span:
                txt = tier_distance_span.get_text(" ", strip=True)
                parts = [p.strip() for p in txt.split("|")]
                if len(parts) == 2:
                    tier_part, distance_label = parts
                    # "Diamond Tier" → "diamond"
                    tier = re.sub(r"\bTier\b", "", tier_part, flags=re.I).strip().lower()
                else:
                    distance_label = parts[0]

            distance = _parse_distance(distance_label)
            if distance is None:
                print(f"    [WARN] {slug}: unknown distance label {distance_label!r} — skipping")
                continue

            brand = _brand_from_slug_or_name(slug, name)

            # Date string from the details span (e.g. "MPRO | 12 Dec")
            date_span = card.find_next("span", class_="details")
            date_label = ""
            if date_span:
                txt = date_span.get_text(" ", strip=True)
                m_date = re.search(r"\d{1,2}\s+[A-Za-z]{3,}", txt)
                date_label = m_date.group(0) if m_date else ""

            # Best-effort sort key: combine "12 Dec" with the race year so we
            # can order races chronologically before ingest. Falls back to
            # Jan 1 of the year when the date label is missing.
            sort_date = _parse_date(f"{date_label} {race_year}") or date(race_year, 1, 1)

            # Placeholder event_id; per-gender race_ids minted in _ingest_race
            event_id = db.slug_id(f"{slug}-{race_year}")
            races.append({
                "slug": slug,
                "year": race_year,
                "name": name,
                "distance": distance,
                "distance_label": distance_label,
                "brand": brand,
                "tier": tier,
                "date_label": date_label,
                "sort_date": sort_date,
                "prize_usd": 0,  # not on listing card; filled from race page
                "event_id": event_id,
                "race_id": event_id,
            })
            print(f"    {slug}/{race_year}  dist={distance}  brand={brand}  tier={tier or '-'}")

        print(f"  Discovered {len(races)} unique past races for {year}")
        return races

    # ------------------------------------------------------------------
    # Phase 2: ingest one race
    # ------------------------------------------------------------------

    def _ingest_race(self, race):
        """Fetch result page and insert event, races, athletes, results.

        Race page layout:
          <h3>Race Info</h3>
          <div>
            <div><b>Location:</b>  <a>...City, Country</a></div>
            <div><b>Distance:</b>  Half-Iron (70.3 miles)</div>
            <div><b>Organizer:</b> Ironman™</div>
            <div><b>Date:</b>      26 Apr 2026</div>
            <div><b>Tier:</b>      Bronze</div>
            <div><b>Prize Money:</b> 15,000 USD</div>
          </div>

        Result tables live inside <div id="MPRO"> and <div id="FPRO"> as
        <table class="race-results">. Future races have no tables → skip.
        """
        slug, year = race["slug"], race["year"]
        url = f"{BASE_URL}/race/{slug}/{year}/results"
        print(f"  Fetching {url}")
        soup = _get(url)

        info = _parse_race_info(soup)
        print(f"  Race Info: {info}")

        # Override listing-derived fields with authoritative race-page values
        distance = _parse_distance(info.get("distance", "")) or race["distance"]
        brand = _parse_brand(info.get("organizer", "")) or race["brand"]
        tier = info.get("tier", "").lower() or race["tier"]
        prize_usd = _parse_prize(info.get("prize_money", "")) or race["prize_usd"]

        venue, country_raw = _split_location(info.get("location", ""))
        country_full = _resolve_pto_country(country_raw)
        race_date = _parse_date(info.get("date", "")) or date(year, 1, 1)

        # Skip races with no results tables (future races)
        if not soup.find("table", class_="race-results"):
            print(f"  No race-results tables — race hasn't happened yet, skipping")
            return False

        print(f"  Venue: {venue!r}  Country: {country_full!r}  Date: {race_date}  "
              f"Brand: {brand}  Tier: {tier or '-'}  Prize: ${prize_usd}")

        db.upsert_nationality(self.conn, country_full)

        event_id = race["event_id"]
        db.insert_event(
            self.conn,
            event_id=event_id,
            name=race["name"],
            venue=venue,
            country=country_full,
            continent=_country_to_continent(country_full),
            start_date=str(race_date),
            end_date=str(race_date),
            longitude=0,
            latitude=0,
            brand=brand,
            prize_money_usd=prize_usd,
        )

        # --- Results tables (MPRO and FPRO) ---
        # Each gender is wrapped in <div id="MPRO"> / <div id="FPRO">, containing
        # one <table class="race-results">.
        total_inserted = 0
        for gender_label, gender_val in [("MPRO", "male"), ("FPRO", "female")]:
            results = self._parse_gender_results(soup, gender_label)
            if not results:
                print(f"  {gender_label}: no results found — skipping")
                continue
            print(f"  {gender_label}: {len(results)} rows found")

            race_id = db.slug_id(f"{slug}-{year}-{gender_val}")

            db.insert_race(
                self.conn,
                race_id=race_id,
                event_id=event_id,
                race_title=race["name"],
                prog_name="Pro Men" if gender_val == "male" else "Pro Women",
                race_date=str(race_date),
                gender=gender_val,
                category="elite",
                sub_category="elite",
                cat_ids="[]",
                distance=distance,
                race_handle=_short_race_handle(race["name"], slug, year),
                event_spec_ids="[]",
            )

            result_rows = []
            new_athlete_slugs = []
            for r in results:
                country_full = _alpha2_to_country(r.get("country_alpha2", ""))
                athlete_id, is_new = self._resolve_athlete(
                    pto_slug=r["pto_slug"],
                    name=r["name"],
                    country_full=country_full,
                    gender=gender_val,
                )
                if is_new:
                    new_athlete_slugs.append(r["pto_slug"])

                status = r.get("status", "Finished")
                result_rows.append((
                    race_id, athlete_id, r["position"], status, 0,
                    r["overall_s"], r["swim_s"], r["bike_s"], r["run_s"],
                    r["t1_s"], r["t2_s"], r.get("pto_points", 0.0),
                ))
                db.record_athlete_nationality(self.conn, athlete_id, country_full, str(race_date))

            insert_results_bulk(self.conn, result_rows)
            print(f"  {gender_label}: inserted {len(result_rows)} results  ({len(new_athlete_slugs)} new athletes)")
            if new_athlete_slugs:
                print(f"    new athletes: {new_athlete_slugs[:10]}" +
                      (f"  ...+{len(new_athlete_slugs)-10}" if len(new_athlete_slugs) > 10 else ""))
            total_inserted += len(result_rows)

        return total_inserted > 0

    def _parse_gender_results(self, soup, gender_label):
        """Extract result rows for one gender from the race results page.

        Results are inside <div id="MPRO"> / <div id="FPRO"> as a single
        <table class="race-results">. Column order varies by event: some races
        omit T1 (Chattanooga 2024), some have no transitions at all (older
        races), some have the full swim/T1/bike/T2/run layout. We parse the
        <thead> row and map columns by label, so any present column is found
        in the right place regardless of position.

        Cols 0 and 1 are always position and athlete (unlabelled); the rest
        are keyed by <td>/<th> text: Swim, T1, Bike, T2, Run, Overall, PTO Pts.

        Split-time cells may have a rank suffix like '55:04(7)' or include a
        '<span class="points">(7)</span>' sibling — both are stripped.
        """
        section = soup.find(id=gender_label)  # 'MPRO' or 'FPRO'
        if not section:
            return []

        table = section.find("table", class_="race-results")
        if not table:
            return []

        # Header → column-index map (by label, lowercase).
        header_row = table.find("tr")
        if not header_row:
            return []
        header_labels = [(td.get_text(strip=True) or "").lower()
                         for td in header_row.find_all(["td", "th"])]

        # Label aliases — tolerate "PTO Pts", "Points", "Finish" vs "Overall", etc.
        LABEL_ALIASES = {
            "swim":      ("swim",),
            "t1":        ("t1",),
            "bike":      ("bike",),
            "t2":        ("t2",),
            "run":       ("run",),
            "overall":   ("overall", "finish", "time"),
            "pts":       ("pto pts", "points", "pts"),
        }
        COL = {k: None for k in LABEL_ALIASES}
        for i, label in enumerate(header_labels):
            for key, aliases in LABEL_ALIASES.items():
                if COL[key] is None and label in aliases:
                    COL[key] = i
                    break

        # Must have at least position+athlete (cols 0, 1) plus an overall time.
        if COL["overall"] is None:
            print(f"    [WARN] {gender_label}: no 'Overall' column in header {header_labels!r} — skipping")
            return []

        min_cells = max(v for v in COL.values() if v is not None) + 1

        rows = []
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) < min_cells:
                continue

            # Position / status
            pos_text = cells[0].get_text(strip=True).upper()
            if pos_text in ("DNF", "DNS", "DQ"):
                position, status = None, pos_text
            else:
                try:
                    position, status = int(pos_text), "Finished"
                except ValueError:
                    continue

            # Athlete block: <a class="headline" href="/athlete/<slug>">
            # with inner <span>First</span><span>Last</span>, and a sibling
            # <span class="flag-icon flag-icon-xx"> preceding the name wrapper.
            athlete_cell = cells[1]
            a = athlete_cell.find("a", class_="headline") or athlete_cell.find(
                "a", href=re.compile(r"/athlete/")
            )
            if not a:
                continue
            pto_slug = re.search(r"/athlete/([^/?#]+)", a["href"]).group(1)
            # Join inner <span> texts with a space; falls back to full text
            name_parts = [s.get_text(strip=True) for s in a.find_all("span")]
            name = " ".join(p for p in name_parts if p) or a.get_text(strip=True)

            # Country — flag-icon class suffix is ISO 3166 alpha-2
            flag_el = athlete_cell.select_one("[class*='flag-icon-']")
            country_alpha2 = ""
            if flag_el:
                for c in flag_el.get("class", []):
                    if c.startswith("flag-icon-") and len(c) == len("flag-icon-") + 2:
                        country_alpha2 = c[-2:].upper()
                        break

            def _time(i):
                if i is None:
                    return 0.0
                cell = cells[i]
                for sp in cell.find_all("span", class_="points"):
                    sp.extract()
                for im in cell.find_all("img"):
                    im.extract()
                txt = re.sub(r"\(\d+\)\s*$", "", cell.get_text(strip=True)).strip()
                return _parse_time(txt)

            swim_s    = _time(COL["swim"])
            t1_s      = _time(COL["t1"])
            bike_s    = _time(COL["bike"])
            t2_s      = _time(COL["t2"])
            run_s     = _time(COL["run"])
            overall_s = _time(COL["overall"])

            pto_points = 0.0
            if COL["pts"] is not None and len(cells) > COL["pts"]:
                try:
                    pto_points = float(cells[COL["pts"]].get_text(strip=True).replace(",", ""))
                except ValueError:
                    pass

            rows.append({
                "pto_slug": pto_slug,
                "name": name,
                "country_alpha2": country_alpha2,
                "position": position,
                "status": status,
                "swim_s": swim_s,
                "t1_s": t1_s,
                "bike_s": bike_s,
                "t2_s": t2_s,
                "run_s": run_s,
                "overall_s": overall_s,
                "pto_points": pto_points,
            })

        return rows

    # ------------------------------------------------------------------
    # Phase 3: athlete enrichment
    # ------------------------------------------------------------------

    def enrich_athletes(self):
        """Fetch profile pages for all athletes with pto_slug but missing meta."""
        todo = self.conn.execute(
            "SELECT athlete_id, pto_slug FROM athletes "
            "WHERE pto_slug IS NOT NULL AND (height_cm IS NULL OR year_of_birth = 0)"
        ).fetchall()

        print(f"\nAthletes needing profile enrichment: {len(todo)}")

        enriched = 0
        failed = 0
        for i, (athlete_id, pto_slug) in enumerate(todo, 1):
            print(f"  [{i}/{len(todo)}] Fetching /athlete/{pto_slug} ...")
            try:
                meta = self._fetch_athlete_meta(pto_slug)
                db.upsert_athlete_pto_fields(
                    self.conn, athlete_id,
                    pto_slug=pto_slug,
                    height_cm=meta.get("height_cm"),
                    weight_kg=meta.get("weight_kg"),
                    nickname=meta.get("nickname", ""),
                )
                if meta.get("yob") and meta["yob"] > 0:
                    self.conn.execute(
                        "UPDATE athletes SET year_of_birth=? WHERE athlete_id=? AND year_of_birth=0",
                        [meta["yob"], athlete_id],
                    )
                enriched += 1
                yob = meta.get("yob", "?")
                h = meta.get("height_cm", "?")
                w = meta.get("weight_kg", "?")
                nick = meta.get("nickname", "")
                print(f"    yob={yob}  height={h}cm  weight={w}kg  nickname={nick!r}")
            except Exception as e:
                print(f"    FAILED: {e}")
                failed += 1

        print(f"  Enriched: {enriched}  Failed: {failed}")

    def _get_profile(self, pto_slug):
        """Return profile meta for a PTO slug, fetching on first ask and caching.

        Swallows HTTP errors and returns an empty dict so one bad profile
        doesn't abort a race ingest.
        """
        if pto_slug in self._profile_cache:
            return self._profile_cache[pto_slug]
        try:
            meta = self._fetch_athlete_meta(pto_slug)
        except Exception as e:
            print(f"    [profile-fetch FAILED] {pto_slug}: {e}")
            meta = {}
        self._profile_cache[pto_slug] = meta
        return meta

    def _fetch_athlete_meta(self, pto_slug):
        """Scrape /athlete/<slug> and return a dict of profile fields.

        Profile data lives in <div class="athlete-info"> with per-field blocks:
            <div class="attribute">
                <div class="name">Weight</div>
                <div class="value">73<span class="value">kg</span></div>
            </div>
        Labels we care about: Weight, Height (in metres), Born (year), Age.
        Nickname is mentioned in the prose biography in single-quotes.
        """
        url = f"{BASE_URL}/athlete/{pto_slug}"
        soup = _get(url)

        meta = {}

        info = soup.select_one(".athlete-info")
        if info:
            for attr in info.find_all("div", class_="attribute"):
                name_el = attr.find("div", class_="name")
                if not name_el:
                    continue
                label = name_el.get_text(strip=True).lower()
                # Concatenate all .value bits, strip the label itself if it leaks in
                value_text = " ".join(v.get_text(strip=True) for v in attr.find_all(class_="value"))

                if label == "born":
                    m = re.search(r"\b(19|20)\d{2}\b", value_text)
                    if m:
                        meta["yob"] = int(m.group(0))
                elif label == "height":
                    m = re.search(r"([\d.]+)\s*m\b", value_text)
                    if m:
                        meta["height_cm"] = int(round(float(m.group(1)) * 100))
                    else:
                        m = re.search(r"(\d+)\s*cm", value_text)
                        if m:
                            meta["height_cm"] = int(m.group(1))
                elif label == "weight":
                    m = re.search(r"(\d+)\s*kg", value_text)
                    if m:
                        meta["weight_kg"] = int(m.group(1))

        # Nickname — biographies put it in curly-single-quotes, almost always
        # as "The <X>" (e.g. 'The Colonel', 'The Iron Cowboy'). Match only that
        # pattern to avoid catching random quoted phrases in prose.
        for s in soup.find_all(string=re.compile(r"[\u2018\u2019']The\s+[A-Z][\w\s-]{1,25}?[\u2018\u2019']")):
            m = re.search(r"[\u2018\u2019'](The\s+[A-Z][\w\s-]{1,25}?)[\u2018\u2019']", s)
            if m:
                meta["nickname"] = m.group(1).strip()
                break

        return meta

    # ------------------------------------------------------------------
    # Athlete identity resolution
    # ------------------------------------------------------------------

    def _resolve_athlete(self, pto_slug, name, country_full, gender):
        """Return (athlete_id, is_new).

        Resolution order:
        1. pto_slug already in DB → existing match (previous ingest run)
        2. Fetch the PTO profile page so we have yob / height / weight /
           nickname before deciding how to link. The per-athlete HTTP cost is
           borne once per run thanks to `_profile_cache`, and we used to pay
           the same cost in `enrich_athletes` afterward — this just shifts it
           earlier so matching has real ages to work with.
        3. WT match: exact normalised name + same country + yob within ±1 year.
        4. New PTO-only athlete minted from slug_id(pto_slug).
        """
        # 1. Already in DB by slug
        existing = self.conn.execute(
            "SELECT athlete_id FROM athletes WHERE pto_slug = ?", [pto_slug]
        ).fetchone()
        if existing:
            return existing[0], False

        # 2. Profile fetch (yob + enrichment fields)
        profile    = self._get_profile(pto_slug)
        yob        = profile.get("yob") or 0
        height_cm  = profile.get("height_cm")
        weight_kg  = profile.get("weight_kg")
        nickname   = profile.get("nickname", "") or ""

        # 3. WT match with real yob
        wt_id, confidence = self._match_wt(name, country_full, yob)
        if wt_id:
            print(f"    Matched '{name}' (yob={yob or '?'}) → WT athlete {wt_id} [{confidence}]")
            db.upsert_athlete_pto_fields(
                self.conn, wt_id, pto_slug, height_cm, weight_kg, nickname,
            )
            # If WT didn't know the yob, adopt PTO's
            if yob > 0:
                self.conn.execute(
                    "UPDATE athletes SET year_of_birth=? WHERE athlete_id=? AND year_of_birth=0",
                    [yob, wt_id],
                )
            # Remove from in-memory WT index so subsequent races can't
            # re-use this athlete row against a different namesake.
            lst = self._wt_athletes.get(country_full, [])
            self._wt_athletes[country_full] = [c for c in lst if c[0] != wt_id]
            return wt_id, False

        # 4. New PTO-only athlete
        athlete_id = db.slug_id(pto_slug)
        # Collision guard: slug_id should be unique but fail loudly if not
        collider = self.conn.execute(
            "SELECT name FROM athletes WHERE athlete_id = ?", [athlete_id]
        ).fetchone()
        if collider:
            raise RuntimeError(
                f"slug_id collision: pto_slug={pto_slug!r} hashes to {athlete_id} "
                f"already held by {collider[0]!r}"
            )

        db.upsert_nationality(self.conn, country_full)
        db.upsert_athlete(self.conn, athlete_id, name, country_full, yob, "", gender)
        db.upsert_athlete_pto_fields(
            self.conn, athlete_id, pto_slug, height_cm, weight_kg, nickname,
        )
        print(f"    New PTO athlete: {name!r} yob={yob or '?'} ({pto_slug})  id={athlete_id}")
        return athlete_id, True

    def _match_wt(self, name, country_full, yob):
        """Try to match against a WT athlete by country + yob + fuzzy name.

        Returns (athlete_id, confidence_str) or (None, None).

        Rules:
        - Prefilter by exact country match. No country → no match (the old
          name-alone fallback conflated namesakes like Thomas Davies GBR 1972
          vs GBR 1995, and country is cheap and reliable).
        - Exact-name fallback: if there's a unique normalised-name match in
          the same country AND one side has yob=0/NULL, accept (we can't
          contradict on yob and the unique-name + same-country pairing is
          strong).
        - Otherwise PTO yob required. Without it we can't distinguish a pro
          from a namesake AGer; the overlap-race matcher (ptd_data.pto_matcher)
          is the safer cross-reference path for yob-less PTO athletes.
        - Within country+yob (±1), require name similarity ≥ 0.70 and a
          clear margin over the runner-up (≥ _NAME_SIM_GAP). If no single
          winner, skip — conservative by design.

        When we refuse due to missing yob, a high-similarity same-country
        WT match is recorded as a merge candidate for manual review (see
        self._merge_candidates).
        """
        candidates = self._wt_athletes.get(country_full, [])
        if not candidates:
            return None, None

        # Exact-name + same-country fallback when one side has no yob.
        # Country is already enforced by the per-country index. If there's a
        # unique normalised-name match and at least one of the two yobs is
        # missing (0/NULL), trust the pairing — same name + same country +
        # no contradicting yob is strong enough to auto-link.
        pto_norm = _normalize_name(name)
        exact_name = [c for c in candidates if _normalize_name(c[1]) == pto_norm]
        if len(exact_name) == 1 and (not yob or not exact_name[0][2]):
            top = exact_name[0]
            return top[0], f"exact_name_country (wt_yob={top[2] or '?'}, pto_yob={yob or '?'})"

        if not yob:
            # No PTO yob to disambiguate, so name + country carries the match.
            # Two rules are safe to auto-link (a missing yob can't contradict);
            # everything else becomes a merge candidate.

            # Rule A: transliteration/accent variant — same fold-key, unique.
            fk = _fold_key(name)
            fold_hits = [c for c in candidates if _fold_key(c[1]) == fk]
            if len(fold_hits) == 1:
                return fold_hits[0][0], f"noyob_translit (wt_yob={fold_hits[0][2] or '?'})"
            # Rule B: added/removed name (token subset) — safe at any score.
            subset = _unique_subset_match(name, candidates)
            if subset:
                return subset[0], f"noyob_subset (wt_yob={subset[2] or '?'})"

            # Otherwise flag the strongest same-country namesake for review.
            scored = sorted(
                ((c, _name_similarity(c[1], name)) for c in candidates),
                key=lambda x: -x[1],
            )
            top, top_sim = scored[0]
            runner_up_sim = scored[1][1] if len(scored) > 1 else 0.0
            if top_sim >= 0.85 and top_sim - runner_up_sim >= _NAME_SIM_GAP:
                self._merge_candidates.append({
                    "wt_athlete_id": top[0],
                    "wt_name":       top[1],
                    "wt_yob":        top[2] or 0,
                    "pto_name":      name,
                    "pto_yob":       0,
                    "country_full":  country_full,
                    "similarity":    round(top_sim, 3),
                    "reason":        "pto_yob_missing",
                })
                print(f"    MERGE CANDIDATE: '{name}' (PTO yob=?) ~ "
                      f"WT '{top[1]}' (id={top[0]}, yob={top[2] or '?'}) "
                      f"sim={top_sim:.2f}")
            return None, None

        yob_close = [c for c in candidates if c[2] and abs(c[2] - yob) <= 1]
        if not yob_close:
            wt_yobs = sorted({c[2] for c in candidates if c[2]})
            # Only print when there were candidates at all — avoids spamming
            # lines for PTO athletes with no WT presence in their country.
            if wt_yobs:
                print(f"    YOB mismatch: '{name}' country={country_full} "
                      f"WT_yobs={wt_yobs[:8]}{'…' if len(wt_yobs) > 8 else ''} "
                      f"PTO_yob={yob} — skipping auto-match")
            return None, None

        scored = sorted(
            ((c, _name_similarity(c[1], name)) for c in yob_close),
            key=lambda x: -x[1],
        )
        top, top_sim = scored[0]
        if top_sim < _NAME_SIM_NICK:
            return None, None
        if len(scored) > 1 and (top_sim - scored[1][1]) < _NAME_SIM_GAP:
            print(f"    Ambiguous: '{name}' country={country_full} yob={yob} — "
                  f"top candidates {[(c[1], f'{s:.2f}') for c, s in scored[:3]]}")
            return None, None

        confidence = "name+country+yob" if top_sim >= 0.95 else f"fuzzy(sim={top_sim:.2f})"
        return top[0], confidence


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def _parse_race_info(soup):
    """Extract {label: value} from the 'Race Info' block on a race page.

    The block follows an <h3>Race Info</h3> and contains <div> rows with
    <b>Label:</b> value-text. Returns a dict with lowercased label keys:
    location, distance, organizer, date, tier, prize_money.
    """
    info = {}
    h3 = soup.find("h3", string=lambda s: s and "Race Info" in s)
    if not h3:
        return info
    container = h3.parent
    for div in container.find_all("div"):
        b = div.find("b")
        if not b:
            continue
        label = b.get_text(strip=True).rstrip(":").strip().lower().replace(" ", "_")
        # Value = all text after the <b>, minus the <b> itself
        b.extract()
        value = div.get_text(" ", strip=True)
        if label and value:
            info[label] = value
    return info


def _split_location(text):
    """Split 'Brasilia, Brazil' → ('Brasilia', 'Brazil'). If no comma, treat all as country."""
    if not text:
        return "", ""
    if "," in text:
        city, country = text.rsplit(",", 1)
        return city.strip(), country.strip()
    return "", text.strip()


def _dump_races_csv(races, path):
    """Write the full PTO discovery set to a CSV, mirroring all_events.csv role."""
    import csv as _csv
    fields = ["slug", "year", "name", "date_label", "distance", "distance_label",
              "brand", "tier", "event_id", "skip_reason"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in races:
            w.writerow({k: r.get(k, "") for k in fields})


def _is_wtcs_slug(slug):
    """True for World Triathlon-organised race slugs we already get from WT.

    PTO labels these as 'Short course' but they're actually WTCS events (mostly
    standard distance) and we have them from the WT API with correct distance
    and athlete IDs. Skip for now; revisit once we wire the cross-reference.
    """
    s = slug.lower()
    return (
        s.startswith("wtcs-")
        or s.startswith("wtcsgf-")
        or s.startswith("world-cup-")
        or s.startswith("wc-")
    )


def _brand_from_slug_or_name(slug, name):
    """Infer brand from slug/name when the listing card doesn't expose it."""
    s = f"{slug} {name}".lower()
    if "t100" in s:
        return "t100"
    if "ironman" in s or re.search(r"\bim\b|\bim-|-im-", s):
        return "ironman"
    if "challenge" in s:
        return "challenge"
    return "independent"


def _short_race_handle(race_name, slug, race_year):
    """Compact, link-friendly PTO race handle suffixed with a 2-digit year.

    Ironman branding is abbreviated ("Ironman 70.3 X" -> "IM 70.3 X",
    "Ironman X" -> "IM X"). Every other series keeps its full name so that
    'Challenge Roth' / 'Outlaw Triathlon' / 'Apfelland Triathlon' read
    naturally. If the listing didn't give us a name we fall back to a
    title-cased slug.

        'Ironman 70.3 Valencia'            -> 'IM 70.3 Valencia 25'
        'Ironman Nice'                     -> 'IM Nice 25'
        'Ironman 70.3 World Championship'  -> 'IM 70.3 World Championship 25'
        'Challenge Roth'                   -> 'Challenge Roth 25'
        'Vancouver T100'                   -> 'Vancouver T100 25'
        ''  (slug='im-703-swansea')        -> 'IM 70.3 Swansea 25'
    """
    t = (race_name or "").strip()
    if not t:
        # Derive from slug. Preserve common abbreviations that .title() would
        # otherwise down-case or that need specific spacing.
        t = slug.replace("-", " ").title()
        t = re.sub(r"\bIm\b",  "IM", t)
        t = re.sub(r"\b703\b", "70.3", t)
        t = re.sub(r"\bWt\b",  "WT", t)
        t = re.sub(r"\bT100\b", "T100", t, flags=re.I)
    # Ironman -> IM (the 70.3 form first so the plain rule doesn't eat the "70.3").
    t = re.sub(r"\bIronman\s+70\.?3\b", "IM 70.3", t, flags=re.I)
    t = re.sub(r"\bIronman\b",          "IM",       t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    yy = race_year % 100
    return f"{t} {yy:02d}" if t else f"Event {yy:02d}"


_ALPHA2_OVERRIDES = {
    "GB": "Great Britain",
    "US": "United States",
    "KR": "Republic of Korea",
    "CZ": "Czech Republic",
    "HK": "Hong Kong, China",
    "TW": "Chinese Taipei",
    "RU": "Russia",
    "MD": "Moldova",
    "IR": "Iran",
    "VE": "Venezuela",
    "BO": "Bolivia",
    "VN": "Vietnam",
    "SY": "Syria",
    "MM": "Myanmar (Burma)",
    "CI": "Cote d'Ivoire",
    "KP": "Democratic People's Republic of Korea",
    "MO": "Macau, China",
    "SX": "Saint Maarten",
    "PS": "Palestine",
}


def _alpha2_to_country(alpha2):
    """Map ISO 3166 alpha-2 code to our nationality name.

    Aligned with db._COUNTRY_SPECIAL_CASES so the nationality upsert hits an
    existing row where possible. Falls back to pycountry's full name.
    """
    if not alpha2:
        return "Unknown"
    alpha2 = alpha2.upper()
    if alpha2 in _ALPHA2_OVERRIDES:
        return _ALPHA2_OVERRIDES[alpha2]
    import pycountry
    c = pycountry.countries.get(alpha_2=alpha2)
    return c.name if c else "Unknown"


def _parse_prize(text):
    """Parse a prize money string like '$150,000' or '15,000 USD' into an integer USD amount."""
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else 0


def _parse_date(text):
    """Try common date formats, return date or None."""
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%B %d, %Y", "%d/%m/%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(text.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    # Fallback: find first 4-digit year in text
    m = re.search(r"\b(20\d{2})\b", text or "")
    return None


def _resolve_pto_country(raw):
    """Best-effort mapping of PTO country string to a nationalities-compatible name.

    PTO uses full English country names (e.g. 'Germany', 'United States').
    Falls back to 'Unknown' so upsert_nationality can handle it gracefully.
    """
    return raw.strip() if raw.strip() else "Unknown"


def _country_to_continent(country_full):
    """Very rough country → continent mapping for events.continent.

    Good enough for discovery; can be enriched later.
    """
    _EUROPE = {"Germany", "France", "United Kingdom", "Great Britain", "Spain", "Italy",
               "Netherlands", "Belgium", "Switzerland", "Austria", "Denmark", "Sweden",
               "Norway", "Finland", "Portugal", "Ireland", "Czech Republic", "Poland",
               "Hungary", "Romania", "Croatia", "Slovakia", "Slovenia", "Estonia",
               "Latvia", "Lithuania", "Luxembourg", "Malta", "Cyprus", "Bulgaria",
               "Serbia", "Bosnia and Herzegovina", "Montenegro", "North Macedonia",
               "Albania", "Moldova", "Ukraine", "Belarus", "Russia"}
    _AMERICAS = {"United States", "Canada", "Brazil", "Mexico", "Argentina", "Chile",
                 "Colombia", "Peru", "Venezuela", "Ecuador", "Uruguay", "Paraguay",
                 "Bolivia", "Cuba", "Costa Rica", "Panama", "Dominican Republic",
                 "Guatemala", "El Salvador", "Honduras", "Nicaragua", "Trinidad and Tobago"}
    _ASIA = {"Japan", "China", "South Korea", "Republic of Korea", "India", "Thailand",
             "Philippines", "Indonesia", "Malaysia", "Singapore", "Vietnam", "Hong Kong",
             "Taiwan", "Chinese Taipei", "Israel", "Kazakhstan", "Bahrain", "UAE",
             "United Arab Emirates", "Qatar", "Saudi Arabia"}
    _AFRICA = {"South Africa", "Egypt", "Kenya", "Morocco", "Algeria", "Tunisia",
               "Nigeria", "Ethiopia", "Uganda", "Ghana", "Cameroon", "Tanzania"}
    _OCEANIA = {"Australia", "New Zealand", "Fiji", "Papua New Guinea", "Samoa", "Tahiti"}

    if country_full in _EUROPE:
        return "Europe"
    if country_full in _AMERICAS:
        return "Americas"
    if country_full in _ASIA:
        return "Asia"
    if country_full in _AFRICA:
        return "Africa"
    if country_full in _OCEANIA:
        return "Oceania"
    return "Other"


# ---------------------------------------------------------------------------
# Bulk results insert — mirror of db.insert_results_bulk with pto_points
# ---------------------------------------------------------------------------

def insert_results_bulk(conn, rows):
    """Batch insert PTO results.

    Each row: (race_id, athlete_id, position, status, start_num,
               overall_s, swim_s, bike_s, run_s, t1_s, t2_s, pto_points)
    """
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR IGNORE INTO results
            (race_id, athlete_id, position, status, start_num,
             overall_s, swim_s, bike_s, run_s, t1_s, t2_s, pto_points)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape PTO long-course results into DB")
    parser.add_argument("--year", type=int, help="Single year to ingest")
    parser.add_argument("--recent", type=int, metavar="N",
                            help="Only scrape the last N years of listings (default: all from 1979). "
                                 "Race pages are always fetched only for unseen races; this just trims "
                                 "the per-year listing fetches for incremental weekly runs.")
    parser.add_argument("--athletes-only", action="store_true",
                            help="Skip race ingestion; only enrich athlete profiles")
    args = parser.parse_args()

    conn = db.get_conn(read_only=False)
    ingester = PTOIngester(conn)

    if args.athletes_only:
        ingester.enrich_athletes()
    else:
        if args.year:
            years = [args.year]
        elif args.recent:
            current = date.today().year
            years = list(range(current - args.recent + 1, current + 1))
        else:
            years = None  # defaults to full history 1979-present

        ingester.run(years=years)

        # Skip the full-roster enrichment pass on incremental runs. New
        # athletes already had their profile fetched inline in _resolve_athlete;
        # re-scanning every historical NULL-field athlete every week just
        # re-fetches the same pages that PTO doesn't expose data for. Run a
        # full ingest (no --recent / --year) to retry historical gaps.
        if not args.recent and not args.year:
            print("\nEnriching new athlete profiles...")
            ingester.enrich_athletes()

    conn.close()
    print("\nPTO ingest complete.")
