"""
PTO <-> WT athlete cross-reference via overlap races.

Races on both platforms (WTCS + World Cup — short-course, elite) have
identical finisher times. Pairing by overall_s (name similarity as tiebreak)
gives a reliable PTO-slug <-> WT-athlete-id map that doesn't rely on
position-numbering agreement between sources.

    python -m ptd_data.pto_matcher                    # last 2 years, read-only
    python -m ptd_data.pto_matcher --years 2020:2026
    python -m ptd_data.pto_matcher --apply            # write pto_slug to WT rows

With --apply, 'exact' and 'nickname_candidate' pairs are committed to the DB:
  - athletes.pto_slug      = PTO slug for the matched row
  - athletes.nickname      = PTO name (for nickname_candidate only; the WT
                             name stays canonical since WT data is richer)

Other statuses (country_mismatch, conflict, low_confidence) are never
auto-applied — inspect the CSV and decide manually.
"""
import argparse
import csv
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Optional

from bs4 import BeautifulSoup

from ptd_data import db
from ptd_data.pto_ingest import (
    BASE_URL, _get, _is_wtcs_slug, _parse_race_info, _split_location,
    _resolve_pto_country, _normalize_name, PTOIngester,
)
from ptd_data.ingest import (
    BASE_URL as WT_BASE_URL, _session as _wt_session,
    get_spec_ids, detect_gender, parse_position, time_to_seconds, clean_field,
)


OVERALL_TIME_TOLERANCE_S = 1    # finisher times agree to the second across sources
NAME_MATCH_EXACT   = 0.95
NAME_MATCH_NICK    = 0.70       # below this → different athletes
TIE_MIN_NAME_SIM   = 0.60       # reject tie-break candidates below this

# WT spec IDs that cover middle / long course events. Sprint (376) and
# standard (377) stay excluded — they're already in the DB via the normal
# WT ingest path. These feed the matcher only; nothing is persisted.
_WT_LONGCOURSE_SPEC_IDS = {
    356,  # Long Distance Triathlon
    369,  # Long Distance
    373,  # Middle Distance
    374,  # Long Distance
    450,  # Middle Distance
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _venue_slug(text):
    """Normalise a venue string into a short slug for fuzzy matching.

    "French Riviera" → "french-riviera"; "Costa Teguise" → "costa-teguise".
    Also strips diacritics so "Karlovy Vary" matches across sources.
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9\s]", " ", s).lower().strip()
    return re.sub(r"\s+", "-", s)


def _name_similarity(a, b):
    """0-1 similarity on normalised names. 1.0 == identical."""
    return SequenceMatcher(None, _normalize_name(a), _normalize_name(b)).ratio()


def _classify_pair(wt_name, pto_name, wt_country, pto_country, wt_yob, pto_yob,
                   wt_gender, race_gender):
    """Return (status, notes) describing a candidate pair."""
    sim = _name_similarity(wt_name, pto_name)
    if wt_gender != race_gender:
        # Pairing is per-gender by construction, so this only fires when our
        # stored gender for the WT athlete is wrong or they were already
        # mis-linked. Either way it needs eyes, not an auto-apply.
        return "gender_mismatch", f"sim={sim:.2f}  wt_gender={wt_gender} race={race_gender}"
    country_ok = (
        not wt_country or not pto_country
        or wt_country.lower() == pto_country.lower()
    )
    yob_ok = True
    if wt_yob and pto_yob:
        yob_ok = abs(wt_yob - pto_yob) <= 1

    if not country_ok and sim < NAME_MATCH_EXACT:
        return "country_and_name_mismatch", f"sim={sim:.2f}"
    if not country_ok:
        return "country_mismatch", f"sim={sim:.2f}  {wt_country!r} vs {pto_country!r}"
    if not yob_ok:
        return "yob_mismatch", f"sim={sim:.2f}  wt_yob={wt_yob} pto_yob={pto_yob}"
    if sim >= NAME_MATCH_EXACT:
        return "exact", f"sim={sim:.2f}"
    if sim >= NAME_MATCH_NICK:
        return "nickname_candidate", f"sim={sim:.2f}"
    return "low_confidence", f"sim={sim:.2f}"


# ---------------------------------------------------------------------------
# Main matcher
# ---------------------------------------------------------------------------

@dataclass
class _PairObservation:
    """One (wt_athlete, pto_slug) pairing seen in one race."""
    race_slug: str
    race_year: int
    position: int
    time_diff_s: float
    wt_athlete_id: int
    wt_name: str
    wt_country: str
    wt_yob: int
    pto_slug: str
    pto_name: str
    pto_country: str
    race_gender: str


class PTOMatcher:
    def __init__(self, conn, years):
        self.conn = conn
        self.years = years
        # WT races in our DB (sprint/standard, already ingested).
        # Plus — fetched fresh from the WT API for the matcher's year window —
        # middle / long / T100 WT races. The long-course ones aren't persisted;
        # we only need them to cross-reference PTO results.
        self._wt_longcourse_race_ids: set[int] = set()
        self._wt_longcourse_events: dict[int, int] = {}   # race_id → event_id
        self._longcourse_finishers_cache: dict[int, list] = {}
        self._db_athlete_ids = set(r[0] for r in self.conn.execute(
            "SELECT athlete_id FROM athletes"
        ).fetchall())
        self._wt_races = self._load_wt_race_index()
        print(f"Indexed {len(self._wt_races)} WT races for matching "
              f"({len(self._wt_longcourse_race_ids)} fetched live for long-course overlap)")

    def _load_wt_race_index(self):
        idx = {}
        # 1. Short-course races already in the DB (sprint + standard, elite).
        rows = self.conn.execute("""
            SELECT r.race_id, r.race_date, r.gender, e.venue, e.country
            FROM races r JOIN events e ON r.event_id = e.event_id
            WHERE r.distance IN ('sprint', 'standard')
              AND r.category = 'elite'
        """).fetchall()
        for race_id, race_date, gender, venue, country in rows:
            venue_key = _venue_slug(venue) or _venue_slug(country)
            if not venue_key:
                continue
            # Key by (date, venue, gender). WT sometimes has stale blank venues
            # — tolerate a ±1-day window at lookup time, not at index time.
            idx[(race_date, venue_key, gender)] = race_id

        # 2. Long-course (middle / long / T100) WT races fetched live from the
        # API for the matcher year window. These are transient — their
        # finishers are fetched lazily in _wt_race_finishers and cached per-run.
        for event in self._fetch_wt_longcourse_events():
            event_id = int(event['event_id'])
            venue_key = _venue_slug(clean_field(event.get('event_venue', ''))) \
                     or _venue_slug(clean_field(event.get('event_country', '')))
            if not venue_key:
                continue
            for prog in self._fetch_wt_programs(event_id):
                if not prog.get('is_race') or not prog.get('results'):
                    continue
                prog_name = str(prog.get('prog_name', ''))
                gender = detect_gender(prog_name)
                if gender is None:
                    continue
                race_date = prog.get('prog_date')
                if not race_date:
                    continue
                race_id = int(prog['prog_id'])
                from datetime import date as _date, datetime as _dt
                if isinstance(race_date, str):
                    try:
                        race_date = _dt.strptime(race_date[:10], '%Y-%m-%d').date()
                    except ValueError:
                        continue
                idx[(race_date, venue_key, gender)] = race_id
                self._wt_longcourse_race_ids.add(race_id)
                self._wt_longcourse_events[race_id] = event_id
        return idx

    def _fetch_wt_longcourse_events(self):
        """Paginate WT /events, keep those with a middle/long spec in this year window."""
        years_set = set(self.years)
        all_events = []
        page = 1
        print(f"Fetching WT events for long-course matcher overlap (years {min(years_set)}-{max(years_set)})...")
        while True:
            r = _wt_session.get(f"{WT_BASE_URL}/events", params={"page": page, "per_page": 500})
            data = r.json()
            rows = data.get('data') or []
            if not rows:
                break
            all_events.extend(rows)
            if not data.get('next_page_url'):
                break
            page += 1
        filtered = []
        for e in all_events:
            start = str(e.get('event_date', ''))[:4]
            if not start.isdigit() or int(start) not in years_set:
                continue
            specs = set(get_spec_ids(e))
            if specs & _WT_LONGCOURSE_SPEC_IDS:
                filtered.append(e)
        print(f"  {len(filtered)} WT long-course events in window")
        return filtered

    def _fetch_wt_programs(self, event_id):
        r = _wt_session.get(f"{WT_BASE_URL}/events/{event_id}/programs")
        data = r.json().get('data') or []
        return [p for p in data if isinstance(p, dict)]

    def _fetch_wt_finishers_api(self, event_id, prog_id):
        """Fetch WT results for a middle/long program, shape-match DB finishers."""
        url = f"{WT_BASE_URL}/events/{event_id}/programs/{prog_id}/results"
        r = _wt_session.get(url)
        results = r.json().get('data', {}).get('results') or []
        out = []
        for res in results:
            aid = res.get('athlete_id')
            if not aid:
                continue
            aid = int(aid)
            # Only pair against athletes we actually have — avoids writing
            # pto_slug to a row that doesn't exist in our roster.
            if aid not in self._db_athlete_ids:
                continue
            overall_s = time_to_seconds(res.get('total_time', ''))
            if not overall_s or overall_s == float('inf'):
                continue
            position_int, status = parse_position(res.get('position', ''))
            if status != 'Finished' or position_int is None:
                continue
            name = str(res.get('athlete_title', '')).replace('"', '').replace("'", "")
            country = str(res.get('athlete_country_name', ''))
            try:
                yob = int(float(res.get('athlete_yob', 0)))
            except (ValueError, TypeError):
                yob = 0
            out.append((position_int, overall_s, aid, name, country, yob))
        out.sort(key=lambda r: r[0])
        return out

    def _find_wt_race(self, race_date, venue_key, gender):
        """Look up WT race with ±1-day tolerance on the date."""
        for delta in (0, -1, 1):
            d = race_date + timedelta(days=delta)
            race_id = self._wt_races.get((d, venue_key, gender))
            if race_id:
                return race_id
        return None

    def _wt_race_finishers(self, race_id):
        if race_id in self._wt_longcourse_race_ids:
            cached = self._longcourse_finishers_cache.get(race_id)
            if cached is None:
                event_id = self._wt_longcourse_events[race_id]
                cached = self._fetch_wt_finishers_api(event_id, race_id)
                self._longcourse_finishers_cache[race_id] = cached
            return cached
        rows = self.conn.execute("""
            SELECT res.position, res.overall_s,
                   a.athlete_id, a.name, a.country_full, a.year_of_birth
            FROM results res
            JOIN athletes a ON res.athlete_id = a.athlete_id
            WHERE res.race_id = ? AND res.status = 'Finished' AND res.position IS NOT NULL
            ORDER BY res.position
        """, [race_id]).fetchall()
        return rows

    def run(self):
        # Reuse scrape helpers from PTOIngester without loading athletes.
        scraper = PTOIngester.__new__(PTOIngester)
        scraper.conn = self.conn

        observations: list[_PairObservation] = []
        matched_races = 0
        unmatched_pto_races = []

        for year in self.years:
            # WTCS / World Cup slugs (empty distance labels on the PTO listing,
            # so _discover_races skips them).
            wtcs_slugs = _discover_overlap_slugs(year)
            # Long-course slugs (T100, IM70.3, Challenge, etc.) — these are
            # what _discover_races returns. Dedupe against WTCS just in case.
            seen = {r['slug'] for r in wtcs_slugs}
            lc_slugs = [
                {'slug': r['slug'], 'year': r['year']}
                for r in scraper._discover_races(year)
                if r['slug'] not in seen
            ]
            overlap = wtcs_slugs + lc_slugs
            print(f"\n  year {year}: {len(wtcs_slugs)} WTCS/WC + {len(lc_slugs)} long-course "
                  f"PTO races to cross-reference")

            for r in overlap:
                slug, yr = r["slug"], r["year"]
                race_url = f"{BASE_URL}/race/{slug}/{yr}/results"
                soup = _get(race_url)
                info = _parse_race_info(soup)
                venue_txt, _ = _split_location(info.get("location", ""))
                venue_key = _venue_slug(venue_txt)
                race_date = _parse_info_date(info.get("date", ""), yr)

                if not race_date or not venue_key:
                    unmatched_pto_races.append(f"{slug}/{yr}  (no date/venue in Race Info)")
                    continue

                for gender_label, gender in (("MPRO", "male"), ("FPRO", "female")):
                    wt_race_id = self._find_wt_race(race_date, venue_key, gender)
                    if not wt_race_id:
                        unmatched_pto_races.append(f"{slug}/{yr} {gender}  (no WT race at {race_date} {venue_key})")
                        continue

                    pto_rows = scraper._parse_gender_results(soup, gender_label)
                    if not pto_rows:
                        continue

                    wt_rows = self._wt_race_finishers(wt_race_id)
                    # WT may list more finishers than PTO (non-PTO-pro "elites"
                    # pushing PTO positions down). Pair by overall_s instead —
                    # finisher times are identical across sources. Ties in time
                    # are broken by name similarity.

                    race_matches = 0
                    paired_wt_ids: set[int] = set()
                    for p in pto_rows:
                        if p.get("status") != "Finished":
                            continue
                        pto_t = p.get("overall_s") or 0
                        if pto_t == 0:
                            continue
                        # Candidates with time within tolerance, not already paired.
                        cands = [w for w in wt_rows
                                 if w[2] not in paired_wt_ids
                                 and abs((w[1] or 0) - pto_t) <= OVERALL_TIME_TOLERANCE_S]
                        if not cands:
                            continue
                        # Break ties by name similarity; require a minimum floor
                        # so a wildly-wrong name doesn't get paired on time alone.
                        best = max(cands, key=lambda w: _name_similarity(w[3], p["name"]))
                        if _name_similarity(best[3], p["name"]) < TIE_MIN_NAME_SIM and len(cands) > 1:
                            continue
                        _, wt_overall, wt_id, wt_name, wt_country, wt_yob = best
                        paired_wt_ids.add(wt_id)

                        pto_country_full = _resolve_pto_country(
                            _alpha2_to_name(p.get("country_alpha2", "")) or "")
                        observations.append(_PairObservation(
                            race_slug=slug, race_year=yr, position=p.get("position") or 0,
                            time_diff_s=abs((pto_t) - (wt_overall or 0)),
                            wt_athlete_id=wt_id, wt_name=wt_name,
                            wt_country=wt_country or "", wt_yob=wt_yob or 0,
                            pto_slug=p["pto_slug"], pto_name=p["name"],
                            pto_country=pto_country_full, race_gender=gender,
                        ))
                        race_matches += 1

                    matched_races += 1
                    print(f"    {slug}/{yr} {gender_label}: paired {race_matches}/{len(pto_rows)}"
                          f" (WT race_id={wt_race_id}, {len(wt_rows)} WT finishers)")

        print(f"\nTotal race-genders matched: {matched_races}")
        print(f"Unmatched PTO races (no WT counterpart found): {len(unmatched_pto_races)}")
        for u in unmatched_pto_races[:20]:
            print(f"  - {u}")
        if len(unmatched_pto_races) > 20:
            print(f"  ... +{len(unmatched_pto_races) - 20} more")

        return self._resolve(observations)

    def _resolve(self, observations):
        """Collapse per-race observations into a final pto_slug → wt_athlete map.

        Picks the most-frequent WT athlete per PTO slug; flags conflicting
        slugs where the top-2 counts are close.
        """
        by_slug: dict[str, Counter] = defaultdict(Counter)
        sample: dict[tuple, _PairObservation] = {}
        for o in observations:
            by_slug[o.pto_slug][o.wt_athlete_id] += 1
            sample.setdefault((o.pto_slug, o.wt_athlete_id), o)

        genders = dict(self.conn.execute("SELECT athlete_id, gender FROM athletes").fetchall())
        resolved = []
        for slug, counts in by_slug.items():
            (top_id, top_n), *rest = counts.most_common()
            conflict = rest and rest[0][1] >= max(2, top_n - 1)
            obs = sample[(slug, top_id)]
            status, notes = _classify_pair(
                obs.wt_name, obs.pto_name,
                obs.wt_country, obs.pto_country,
                obs.wt_yob, 0,  # yob not available from results pages
                genders[top_id], obs.race_gender,
            )
            if conflict:
                status = "conflict"
                notes += f" | also matched wt_athlete_id={rest[0][0]} x{rest[0][1]}"
            resolved.append({
                "pto_slug":      slug,
                "pto_name":      obs.pto_name,
                "pto_country":   obs.pto_country,
                "wt_athlete_id": top_id,
                "wt_name":       obs.wt_name,
                "wt_country":    obs.wt_country,
                "wt_yob":        obs.wt_yob,
                "n_races":       top_n,
                "status":        status,
                "notes":         notes,
            })

        return sorted(resolved, key=lambda r: (r["status"] != "exact", -r["n_races"], r["wt_name"]))


# ---------------------------------------------------------------------------
# Utilities borrowed from pto_ingest
# ---------------------------------------------------------------------------

def _discover_overlap_slugs(year):
    """Return a list of {'slug','year'} dicts for WTCS/World Cup races in `year`.

    Bypasses `_discover_races`'s distance-label filter (which drops WTCS cards
    because their distance text is empty). We only need slug + year here.
    """
    url = f"{BASE_URL}/results?year={year}"
    print(f"\nDiscovering overlap (WTCS/World Cup) slugs from {url} ...")
    soup = _get(url)
    cards = soup.select("div.race-name-and-tier")
    seen = set()
    out = []
    for card in cards:
        link = card.select_one("a.racename[href*='/race/']")
        if not link:
            continue
        m = re.match(r"/race/([^/]+)/(\d{4})/results", link.get("href", ""))
        if not m:
            continue
        slug, yr = m.group(1), int(m.group(2))
        if slug in seen:
            continue
        seen.add(slug)
        if _is_wtcs_slug(slug):
            out.append({"slug": slug, "year": yr})
    print(f"  Found {len(out)} WTCS/World Cup slugs for {year}")
    return out


def _parse_info_date(text, fallback_year):
    """Parse '25 Sep 2025' etc. into a date; fall back to Jan 1 if unparseable."""
    from datetime import datetime
    for fmt in ("%d %b %Y", "%Y-%m-%d", "%B %d, %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


# Shadow the small subset of the pto_ingest alpha2 map we need here —
# importing the constant directly keeps the matcher decoupled from future
# ingest-only additions.
from ptd_data.pto_ingest import _ALPHA2_OVERRIDES  # noqa: E402
import pycountry  # type: ignore  # already a transitive dep of nationalities loader

def _alpha2_to_name(alpha2):
    if not alpha2:
        return ""
    if alpha2 in _ALPHA2_OVERRIDES:
        return _ALPHA2_OVERRIDES[alpha2]
    try:
        c = pycountry.countries.get(alpha_2=alpha2)
        return c.name if c else ""
    except (AttributeError, LookupError):
        return ""


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_report(resolved, path):
    fields = ["status", "pto_slug", "pto_name", "pto_country",
              "wt_athlete_id", "wt_name", "wt_country", "wt_yob",
              "n_races", "notes"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in resolved:
            w.writerow(r)
    print(f"\nWrote {len(resolved)} matches to {path}")


def print_summary(resolved):
    bucket = Counter(r["status"] for r in resolved)
    print("\nMatch summary:")
    for status, n in bucket.most_common():
        print(f"  {n:5d}  {status}")


def apply_matches(conn, resolved):
    """Write pto_slug (+ nickname for nickname_candidate) to WT athlete rows.

    Only 'exact' and 'nickname_candidate' are committed. Skips rows whose
    WT athlete is already linked to a different PTO slug — those need human
    review — and rows data/athlete_no_merge.csv rejects.
    """
    no_merge = db.load_no_merge('pto')
    applied = 0
    conflicts = 0
    skipped_bad_status = 0
    for r in resolved:
        if r["status"] not in ("exact", "nickname_candidate"):
            skipped_bad_status += 1
            continue
        if r["wt_athlete_id"] in no_merge.get(r["pto_slug"], ()):
            print(f"  [skip] {r['pto_slug']!r} -> wt_athlete_id={r['wt_athlete_id']} "
                  "is blocked by athlete_no_merge.csv")
            continue
        existing = conn.execute(
            "SELECT pto_slug FROM athletes WHERE athlete_id = ?",
            [r["wt_athlete_id"]],
        ).fetchone()
        if existing and existing[0] and existing[0] != r["pto_slug"]:
            print(f"  [skip] wt_athlete_id={r['wt_athlete_id']} ({r['wt_name']}) "
                  f"already has pto_slug={existing[0]!r}, refusing to overwrite "
                  f"with {r['pto_slug']!r}")
            conflicts += 1
            continue
        nickname = r["pto_name"] if r["status"] == "nickname_candidate" else ""
        conn.execute(
            "UPDATE athletes SET pto_slug = ?, nickname = COALESCE(NULLIF(?, ''), nickname) "
            "WHERE athlete_id = ?",
            [r["pto_slug"], nickname, r["wt_athlete_id"]],
        )
        applied += 1
    print(f"\nApplied {applied} matches  (skipped {skipped_bad_status} by status, "
          f"{conflicts} conflicts)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_years(spec):
    if not spec:
        today = date.today()
        return [today.year - 1, today.year]
    if ":" in spec:
        lo, hi = spec.split(":")
        return list(range(int(lo), int(hi) + 1))
    return [int(y) for y in spec.split(",")]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Match PTO slugs to WT athletes via overlap races")
    parser.add_argument("--years", type=str, default="",
                        help="Years to scan, e.g. '2022:2026' or '2024,2025'. Default: last 2 years")
    parser.add_argument("--report-path", type=str, default="pto_match_report.csv")
    parser.add_argument("--apply", action="store_true",
                        help="Commit 'exact' and 'nickname_candidate' matches to the DB "
                             "(writes athletes.pto_slug + nickname). Default: read-only report.")
    args = parser.parse_args()

    years = _parse_years(args.years)
    conn = db.get_conn(read_only=not args.apply)
    matcher = PTOMatcher(conn, years)
    resolved = matcher.run()
    write_report(resolved, args.report_path)
    print_summary(resolved)
    if args.apply:
        apply_matches(conn, resolved)
    conn.close()
