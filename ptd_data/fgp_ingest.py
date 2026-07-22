"""
French Grand Prix ingest — pulls the FFTRI Triathlon Series (ex "Grand Prix de
Triathlon", the French D1/D2 club championship) from the ProLiveSport API that
backs triathlonseries.fr, and inserts the individual "Scratch" results into
the shared DB schema alongside WT short-course data.

Usage:
    python -m ptd_data.fgp_ingest

Runs after the WT ingest and before the PTO ingest (see scripts/build_db.sh):
FGP athletes must exist before PTO matching scans unlinked rows, and FGP
matching itself wants the freshest WT roster.

Source quirks (all verified against the API + archived live pages):
  - Discovery: POST {API}//0/X/saison/ lists every stage of every season with
    eventId + the four races (D1F/D1H/D2F/D2H). Results: GET
    {API}/{eventId}/{race}/resultIndiv/. Athlete detail (full birth date):
    GET {API}//0/{fftri_id}/athlete/" — the trailing double-quote is literally
    part of the URL the official frontend requests.
  - 2021 is skipped entirely: the API's rank/time columns for 2021 hold the
    position at T2, not the finish (checked vs contemporary Wayback snapshots
    of the live pages — the real Dunkerque winner sits at API rank 16), and
    run splits are absent, so true finish order is unrecoverable.
  - 2022-2023 store splits as swim/t1/bike in T1-T3, the cumulative time after
    the bike in T4, and t2 in T5; the run split is derived as
    total - cumulative - t2. 2024+ store the five plain segment durations.
  - Metz 2022 D1F/D1H ran as a team time-trial (rows are clubs, not athletes)
    and is skipped. Saint-Jean-de-Monts 2023 rows carry no licence/splits;
    identity is recovered by joining the startlist on bib number.
  - Event dates are not exposed by the public API. EVENT_DATES below was
    pulled once from their ERP endpoint (plus FFTRI news for the 2023 rows the
    ERP returns as null) and is maintained by hand: a stage missing from it
    fails the run loudly.

Athlete identity mirrors the PTO ingest: match on nationality + year of birth
(±1, yob from the athlete-detail birth date) + fuzzy accent-insensitive name
(same thresholds), keyed to athletes.fftri_id so resolution is sticky across
runs. Unmatched athletes are minted as new rows; plausible-but-unconfirmed
pairs are written to data/fgp_merge_candidates.csv for manual review via
data/athlete_merges.csv.
"""

import csv
import re
import time
from datetime import date
from pathlib import Path

import pycountry
import requests

from ptd_data import db
from ptd_data.pto_ingest import (
    _NAME_SIM_GAP,
    _NAME_SIM_NICK,
    _fold_key,
    _name_similarity,
    _normalize_name,
    _parse_time,
    _unique_subset_match,
)

API = "https://api.prolivesport.fr/Graphics_TV_API"
FETCH_DELAY = 0.1  # seconds between requests; the API is small, be polite

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; ptd-ingest/1.0)",
    "Accept-Language": "en-US,en;q=0.9",
})

RACE_GENDER = {"D1F": "female", "D1H": "male", "D2F": "female", "D2H": "male"}
def _fgp_handle(location, season):
    """Event-level short handle, matching the site convention "{Venue} {Abbr}
    {YY}" (cf. "Melilla CC 24"). Division (D1/D2) and gender are race-level and
    live in prog_name, not the handle — all four races of an event share it."""
    return f"{location} FGP {season % 100:02d}"
# First word 'Elite' keeps db.backfill_sub_category classifying these as elite.
RACE_PROG = {"D1F": "Elite Women (D1)", "D1H": "Elite Men (D1)",
             "D2F": "Elite Women (D2)", "D2H": "Elite Men (D2)"}

# 2021 finish order is corrupt at source — see module docstring.
MIN_SEASON = 2022

# (eventId, race) pairs that must not be ingested as individual results.
SKIP_RACES = {
    ("693", "D1F"): "Metz 2022 D1 ran as a team time-trial; rows are clubs",
    ("693", "D1H"): "Metz 2022 D1 ran as a team time-trial; rows are clubs",
}

# eventId -> race date. Fetched once from the ERP get-stage endpoint
# (2023 nulls filled from FFTRI news posts); maintained by hand — add a row
# per new stage each season.
EVENT_DATES = {
    "691": "2022-05-14",   # Fréjus
    "692": "2022-06-19",   # Dunkerque
    "693": "2022-07-03",   # Metz
    "694": "2022-09-03",   # Quiberon
    "695": "2022-09-11",   # Saint-Jean-de-Monts
    "861": "2023-05-13",   # Fréjus
    "862": "2023-06-17",   # Bordeaux
    "863": "2023-07-02",   # Metz
    "864": "2023-09-02",   # Quiberon
    "865": "2023-09-10",   # Saint-Jean-de-Monts
    "931": "2024-05-04",   # Fréjus
    "932": "2024-06-08",   # Metz
    "933": "2024-06-29",   # Bordeaux
    "934": "2024-09-07",   # Quiberon
    "935": "2024-09-14",   # Saint-Jean-de-Monts
    "1011": "2025-06-08",  # Albi
    "1012": "2025-06-28",  # Vichy
    "1013": "2025-09-06",  # Quiberon
    "1014": "2025-09-20",  # La Baule
    "1015": "2025-09-27",  # Cabourg
    "1151": "2026-05-30",  # Metz-Moselle
    "1152": "2026-06-14",  # Albi
    "1153": "2026-07-04",  # Vichy
    "1155": "2026-10-03",  # Cabourg
}

# Marketing names normalised back to the venue so recurring grouping and
# handles stay stable across editions.
LOCATION_ALIASES = {"Metz-Moselle": "Metz"}

# Source feeds mix IOC codes with ISO codes. Map the full canonical set of IOC
# codes that differ from ISO alpha-3 (plus a couple of observed typos) so nation
# resolution never silently fails or has to be extended race by race.
IOC_TO_ISO = {
    "ALG": "DZA", "ANG": "AGO", "ANT": "ATG", "ARU": "ABW", "ASA": "ASM",
    "BAH": "BHS", "BAN": "BGD", "BAR": "BRB", "BER": "BMU", "BHU": "BTN",
    "BIZ": "BLZ", "BOT": "BWA", "BRU": "BRN", "BUL": "BGR", "BUR": "BFA",
    "CAM": "KHM", "CAY": "CYM", "CGO": "COG", "CHA": "TCD", "CHI": "CHL",
    "CRC": "CRI", "CRO": "HRV", "DEN": "DNK", "ESA": "SLV", "FIJ": "FJI",
    "GAM": "GMB", "GBS": "GNB", "GEQ": "GNQ", "GER": "DEU", "GRE": "GRC",
    "GRN": "GRD", "GUA": "GTM", "GUI": "GIN", "HAI": "HTI", "HON": "HND",
    "INA": "IDN", "IRI": "IRN", "ISV": "VIR", "KSA": "SAU", "KUW": "KWT",
    "LAT": "LVA", "LBA": "LBY", "LES": "LSO", "LIB": "LBN", "MAD": "MDG",
    "MAS": "MYS", "MAW": "MWI", "MGL": "MNG", "MON": "MCO", "MRI": "MUS",
    "MTN": "MRT", "MYA": "MMR", "NCA": "NIC", "NED": "NLD", "NEP": "NPL",
    "NGR": "NGA", "NIG": "NER", "OMA": "OMN", "PAR": "PRY", "PHI": "PHL",
    "PLE": "PSE", "POR": "PRT", "PUR": "PRI", "RSA": "ZAF", "SAM": "WSM",
    "SEY": "SYC", "SIN": "SGP", "SLO": "SVN", "SOL": "SLB", "SRI": "LKA",
    "SUD": "SDN", "SUI": "CHE", "TAN": "TZA", "TOG": "TGO", "TPE": "TWN",
    "UAE": "ARE", "URU": "URY", "VAN": "VUT", "VIE": "VNM", "VIN": "VCT",
    "ZAM": "ZMB", "ZIM": "ZWE",
    "JAP": "JPN",  # observed typo (should be JPN)
}

# Canonical country_full for alpha3s that map to several names in the DB.
_ALPHA3_NAME_OVERRIDES = {
    "GBR": "Great Britain",
    "USA": "United States",
    "RUS": "Russia",
}

# Result-row nation codes that are known feed junk; the athlete-detail nation
# is used instead (e.g. rows tagged MRI are Portuguese athletes whose detail
# record says PRT).
JUNK_ROW_CODES = {"MRI", "FRZ"}

_FFTRI_ID_RE = re.compile(r"^[A-Z]\d{5}$")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _api_call(path, method="GET", retries=3):
    """Call the ProLiveSport API and return the parsed `result` payload.

    Raises on HTTP errors and on success=false responses — a changed endpoint
    should stop the pipeline, not silently produce an empty ingest.
    """
    url = f"{API}{path}"
    for attempt in range(1, retries + 1):
        time.sleep(FETCH_DELAY)
        try:
            resp = _session.request(method, url, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
            break
        except (requests.RequestException, ValueError) as e:
            print(f"  [HTTP] Attempt {attempt}/{retries} failed for {url}: {e}")
            if attempt == retries:
                raise
            time.sleep(attempt)
    if not payload.get("success"):
        raise RuntimeError(f"API call failed: {method} {url} -> {payload}")
    return payload["result"]


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------

def _parse_status(rank_str):
    """Map the API rank column to (position, result_status_enum)."""
    s = str(rank_str).strip()
    if s.isdigit():
        return int(s), "Finished"
    return None, {"DNF": "DNF", "DNS": "DNS", "DSQ": "DQ"}.get(s.upper(), "NC")


def _parse_splits(row, season):
    """Return (overall, swim, t1, bike, t2, run) seconds for one result row.

    Layouts by era (see module docstring). Splits that fail their internal
    consistency check are zeroed rather than stored wrong; the overall time is
    always trusted for 2022+.
    """
    total = _parse_time(row.get("time", ""))
    t = [_parse_time(row.get(f"timeT{i}", "")) for i in range(1, 6)]

    if season >= 2024:
        swim, t1, bike, t2, run = t
        if total and sum(t) and abs(sum(t) - total) > 5:
            swim = t1 = bike = t2 = run = 0.0
        return total, swim, t1, bike, t2, run

    # 2022-2023: T4 is cumulative-after-bike, T5 is t2, run column absent.
    swim, t1, bike, cum, t2 = t
    run = 0.0
    if total and cum and 0 < total - cum - t2 < total:
        run = total - cum - t2
    if cum and abs((swim + t1 + bike) - cum) > 5:
        swim = t1 = bike = 0.0
    return total, swim, t1, bike, t2, run


def _display_name(firstname, lastname):
    """'Jessica' + 'FULLAGAR' -> 'Jessica Fullagar' (WT-style capitalisation).

    '.' placeholders (anonymised half-names on a few rows) are dropped.
    """
    parts = [str(p).strip().title() for p in (firstname, lastname)]
    return " ".join(p for p in parts if p.replace(".", "")).strip()


def _loc_slug(location):
    return re.sub(r"[^a-z0-9]+", "-", location.lower()).strip("-")


# ---------------------------------------------------------------------------
# Main ingester
# ---------------------------------------------------------------------------

class FGPIngester:
    def __init__(self, conn):
        self.conn = conn
        # Sticky source-id links from previous runs.
        self._fftri_map = dict(conn.execute(
            "SELECT fftri_id, athlete_id FROM athletes WHERE fftri_id IS NOT NULL"
        ).fetchall())
        # Candidate index for matching, keyed by ISO alpha3 so IOC/name-form
        # differences can't break the prefilter. Already-linked athletes stay
        # matchable: FFTRI licence ids are NOT stable per person (relicensing
        # gives the same athlete a new id), so a second id must be able to
        # resolve onto the athlete the first id already claimed.
        rows = conn.execute("""
            SELECT a.athlete_id, a.name, a.year_of_birth, n.alpha3
            FROM athletes a JOIN nationalities n USING (country_full)
        """).fetchall()
        self._candidates = {}
        self._global_names = {}
        for athlete_id, name, yob, alpha3 in rows:
            self._candidates.setdefault(alpha3, []).append((athlete_id, name, yob))
            self._global_names.setdefault(_normalize_name(name), []).append(
                (athlete_id, name, yob, alpha3))
        print(f"Loaded {len(self._fftri_map)} linked FFTRI ids, "
              f"{len(rows)} match candidates")
        # alpha3 -> canonical country_full (most-used name wins, overrides trump).
        self._alpha3_names = {}
        for country_full, alpha3, n in conn.execute("""
            SELECT n.country_full, n.alpha3, COUNT(a.athlete_id) AS n
            FROM nationalities n LEFT JOIN athletes a USING (country_full)
            GROUP BY 1, 2 ORDER BY n
        """).fetchall():
            self._alpha3_names[alpha3] = country_full
        self._alpha3_names.update(_ALPHA3_NAME_OVERRIDES)
        self._detail_cache = {}
        self._merge_candidates = []

    # -- country handling ---------------------------------------------------

    def _country_from_code(self, code):
        """API nation code -> country_full name used by the athletes table."""
        alpha3 = IOC_TO_ISO.get(code, code)
        if alpha3 in self._alpha3_names:
            return self._alpha3_names[alpha3]
        country = pycountry.countries.get(alpha_3=alpha3)
        if country is None:
            raise RuntimeError(f"Unknown nation code {code!r} — extend IOC_TO_ISO")
        self._alpha3_names[alpha3] = country.name
        return country.name

    def _alpha3_from_code(self, code):
        return IOC_TO_ISO.get(code, code)

    # -- athlete resolution ---------------------------------------------------

    def _fetch_detail(self, fftri_id):
        """Athlete-detail payload (has full birthDate + clean nation), cached.

        Returns {} when the API has no record — the row still ingests, just
        without a yob for matching.
        """
        if fftri_id in self._detail_cache:
            return self._detail_cache[fftri_id]
        try:
            result = _api_call(f'//0/{fftri_id}/athlete/"')
            detail = result.get("athleteData") or {}
        except (requests.RequestException, RuntimeError) as e:
            print(f"    [athlete-detail FAILED] {fftri_id}: {e}")
            detail = {}
        self._detail_cache[fftri_id] = detail
        return detail

    def _resolve_athlete(self, fftri_id, row, gender):
        """Return (athlete_id, country_full, is_new) for one result row.

        Nation handling: the result-row nation is the racing/display
        nationality (matches WT convention); the athlete-detail nation is the
        FFTRI licence country, which can differ (foreign pros racing on a
        French licence). The row nation leads; matching also tries the licence
        country, plus a global exact-name+yob fallback for nationality
        switchers whose WT row sits under a third country.
        """
        if fftri_id and fftri_id in self._fftri_map:
            return self._fftri_map[fftri_id], "", False

        detail = self._fetch_detail(fftri_id) if fftri_id else {}
        row_code = str(row.get("nation") or "").strip()
        detail_code = str(detail.get("nation") or "").strip()
        if row_code in JUNK_ROW_CODES and detail_code:
            row_code = detail_code
        nation_code = row_code or detail_code
        if not nation_code:
            raise RuntimeError(f"Result row with no nation: {row}")
        country_full = self._country_from_code(nation_code)

        name = _display_name(row["firstname"], row["lastname"])
        yob = 0
        birth = str(detail.get("birthDate") or "")
        if re.match(r"^(19|20)\d{2}-", birth):
            yob = int(birth[:4])

        matched_id = None
        for code in dict.fromkeys([nation_code, detail_code]):
            if not code:
                continue
            matched_id = self._match_existing(name, self._alpha3_from_code(code),
                                              country_full, yob, fftri_id)
            if matched_id:
                break
        if not matched_id and yob:
            matched_id = self._match_global(name, yob)
        if matched_id:
            if fftri_id:
                # Keep the first licence id on the row (relicensed athletes
                # carry several); the in-memory map still resolves this one.
                self.conn.execute(
                    "UPDATE athletes SET fftri_id = ? WHERE athlete_id = ? AND fftri_id IS NULL",
                    [fftri_id, matched_id])
                self._fftri_map[fftri_id] = matched_id
            if yob:
                self.conn.execute(
                    "UPDATE athletes SET year_of_birth = ? WHERE athlete_id = ? AND year_of_birth = 0",
                    [yob, matched_id])
            return matched_id, country_full, False

        # Mint a new athlete. Keyed on the FFTRI licence id when present;
        # rows without one (Saint-Jean-de-Monts 2023 leftovers, the odd
        # anonymised entry) fall back to name+country, so re-encountering the
        # same unlicensed athlete resolves to the row already minted for them.
        slug = f"fftri-{fftri_id}" if fftri_id else \
               f"fgp-{_loc_slug(_normalize_name(name))}-{self._alpha3_from_code(nation_code).lower()}"
        athlete_id = db.slug_id(slug)
        collider = self.conn.execute(
            "SELECT name FROM athletes WHERE athlete_id = ?", [athlete_id]).fetchone()
        if collider:
            if not fftri_id and _normalize_name(collider[0]) == _normalize_name(name):
                return athlete_id, country_full, False
            raise RuntimeError(
                f"slug_id collision: {slug!r} hashes to {athlete_id} "
                f"already held by {collider[0]!r}")

        db.upsert_nationality(self.conn, country_full)
        db.upsert_athlete(self.conn, athlete_id, name, country_full, yob, "", gender)
        if fftri_id:
            self.conn.execute("UPDATE athletes SET fftri_id = ? WHERE athlete_id = ?",
                              [fftri_id, athlete_id])
            self._fftri_map[fftri_id] = athlete_id
        print(f"    New FGP athlete: {name!r} yob={yob or '?'} "
              f"({country_full})  id={athlete_id}")
        return athlete_id, country_full, True

    def _match_existing(self, name, alpha3, country_full, yob, fftri_id):
        """Match one FGP athlete against existing unlinked athletes.

        Same rules as pto_ingest._match_wt: country prefilter, exact-name
        fallback when a yob is missing, otherwise yob ±1 + fuzzy name with a
        clear margin. Near-misses land in fgp_merge_candidates.csv.
        """
        candidates = self._candidates.get(alpha3, [])
        if not candidates:
            return None

        norm = _normalize_name(name)
        exact_name = [c for c in candidates if _normalize_name(c[1]) == norm]
        if len(exact_name) == 1 and (not yob or not exact_name[0][2]):
            return self._accept(exact_name[0],
                                f"exact_name_country (db_yob={exact_name[0][2] or '?'}, fgp_yob={yob or '?'})",
                                name)

        if not yob:
            # Many club athletes have no record in the source's athlete DB, so
            # no birth date. Two rules are safe to auto-link without one (same
            # logic as pto_ingest): a transliteration/accent variant (fold-key
            # equality), or an added/removed name (token subset). Everything
            # else goes to review.
            fk = _fold_key(name)
            fold_hits = [c for c in candidates if _fold_key(c[1]) == fk]
            if len(fold_hits) == 1:
                return self._accept(fold_hits[0], "noyob_translit", name)
            subset = _unique_subset_match(name, candidates)
            if subset:
                return self._accept(subset, "noyob_subset", name)
            self._suggest_candidate(candidates, name, yob, country_full, fftri_id,
                                    "fgp_yob_missing")
            return None

        yob_close = [c for c in candidates if c[2] and abs(c[2] - yob) <= 1]
        if not yob_close:
            # Same country + strong name but yob off by 2+: likely a bad DOB
            # on one side — flag for manual review instead of auto-linking.
            self._suggest_candidate(candidates, name, yob, country_full, fftri_id,
                                    "yob_mismatch")
            return None

        scored = sorted(((c, _name_similarity(c[1], name)) for c in yob_close),
                        key=lambda x: -x[1])
        top, top_sim = scored[0]
        if top_sim < _NAME_SIM_NICK:
            return None
        if len(scored) > 1 and (top_sim - scored[1][1]) < _NAME_SIM_GAP:
            print(f"    Ambiguous: {name!r} {alpha3} yob={yob} — "
                  f"top {[(c[1], f'{s:.2f}') for c, s in scored[:3]]}")
            return None
        conf = "name+country+yob" if top_sim >= 0.95 else f"fuzzy(sim={top_sim:.2f})"
        return self._accept(top, conf, name)

    def _match_global(self, name, yob):
        """Country-free fallback: unique exact-name + yob(±1) match anywhere.

        Catches nationality switchers (e.g. an athlete racing FGP under a
        French licence whose WT row sits under Morocco or Romania) that the
        country prefilter can never bridge. Exact normalised name only —
        fuzzy matching without a country constraint is too loose.
        """
        matches = [c for c in self._global_names.get(_normalize_name(name), [])
                   if c[2] and abs(c[2] - yob) <= 1]
        if len(matches) != 1:
            return None
        athlete_id, db_name, db_yob, alpha3 = matches[0]
        return self._accept((athlete_id, db_name, db_yob),
                            f"name+yob_global (db_country={alpha3})", name)

    def _accept(self, candidate, confidence, fgp_name):
        # Accepted candidates deliberately stay in the index: the same athlete
        # can legitimately match again under a different FFTRI licence id.
        athlete_id, db_name, _ = candidate
        print(f"    Matched {fgp_name!r} -> existing athlete {db_name!r} "
              f"(id={athlete_id}) [{confidence}]")
        return athlete_id

    def _suggest_candidate(self, candidates, name, yob, country_full, fftri_id, reason):
        scored = sorted(((c, _name_similarity(c[1], name)) for c in candidates),
                        key=lambda x: -x[1])
        if not scored or scored[0][1] < 0.85:
            return
        top, top_sim = scored[0]
        runner_up = scored[1][1] if len(scored) > 1 else 0.0
        if top_sim - runner_up < _NAME_SIM_GAP:
            return
        self._merge_candidates.append({
            "db_athlete_id": top[0],
            "db_name":       top[1],
            "db_yob":        top[2] or 0,
            "fgp_name":      name,
            "fgp_yob":       yob,
            "fftri_id":      fftri_id or "",
            "country_full":  country_full,
            "similarity":    round(top_sim, 3),
            "reason":        reason,
        })
        print(f"    MERGE CANDIDATE: {name!r} (yob={yob or '?'}) ~ "
              f"{top[1]!r} (id={top[0]}, yob={top[2] or '?'}) sim={top_sim:.2f} [{reason}]")

    def _dump_merge_candidates(self):
        """Merge new candidates into the review CSV.

        Unlike the PTO flow (full rescrape each run, so overwrite is fine),
        FGP candidates only surface the first time an athlete is resolved —
        an incremental run must not clobber rows still awaiting review.
        Remove rows by hand once actioned (athlete_merges.csv) or dismissed.
        """
        data_dir = Path(__file__).parent / "data"
        path = data_dir / "fgp_merge_candidates.csv"
        cols = ["db_athlete_id", "db_name", "db_yob", "fgp_name", "fgp_yob",
                "fftri_id", "country_full", "similarity", "reason"]

        # Athletes already actioned in athlete_merges.csv are resolved — drop
        # them from the review queue so a re-mint on a later run can't re-list
        # a pair the reviewer has already handled.
        resolved_keeps = set()
        merges_path = data_dir / "athlete_merges.csv"
        if merges_path.exists():
            with open(merges_path, newline="") as f:
                for r in csv.DictReader(f):
                    resolved_keeps.add(str(r.get("keep_athlete_id", "")).strip())

        rows = {}
        if path.exists():
            with open(path, newline="") as f:
                for r in csv.DictReader(f):
                    rows[(r["db_athlete_id"], r["fgp_name"])] = r
        for r in self._merge_candidates:
            rows[(str(r["db_athlete_id"]), r["fgp_name"])] = {k: str(r[k]) for k in cols}
        rows = {k: v for k, v in rows.items() if v["db_athlete_id"] not in resolved_keeps}
        if not rows:
            if path.exists():
                path.unlink()
            return
        ordered = sorted(rows.values(), key=lambda x: -float(x["similarity"]))
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in ordered:
                w.writerow(r)
        print(f"Merge-candidate review file: {path.name} "
              f"({len(self._merge_candidates)} new, {len(ordered)} total)")

    # -- race ingest ----------------------------------------------------------

    def run(self):
        print()
        print("=" * 60)
        print("FGP INGEST  —  triathlonseries.fr (ProLiveSport API)")
        print("=" * 60)

        stages = _api_call("//0/X/saison/", method="POST")
        # Canonical location per event: the odd stage row has an empty
        # stageLocation (e.g. Saint-Jean-de-Monts 2023 D2F), so take any
        # non-empty sibling's value.
        event_locations = {}
        for st in stages:
            if st["sport"] == "Triathlon" and st["stageLocationLowercase"]:
                event_locations.setdefault(st["eventId"], st["stageLocationLowercase"])
        todo = []
        for st in stages:
            if st["sport"] != "Triathlon" or st["race"] not in RACE_GENDER:
                continue
            season = int(st["saison"])
            event_id_src, race = st["eventId"], st["race"]
            if season < MIN_SEASON:
                continue
            if (event_id_src, race) in SKIP_RACES:
                print(f"  Skipping {season} {st['stageLocationLowercase']} {race}: "
                      f"{SKIP_RACES[(event_id_src, race)]}")
                continue
            if event_id_src not in EVENT_DATES:
                raise RuntimeError(
                    f"No date for FGP event {event_id_src} "
                    f"({season} {st['stageLocationLowercase']}) — add it to EVENT_DATES")
            race_date = EVENT_DATES[event_id_src]
            if date.fromisoformat(race_date) >= date.today():
                continue
            location = event_locations[event_id_src]
            location = LOCATION_ALIASES.get(location, location)
            todo.append((race_date, season, event_id_src, location, race))

        todo.sort()
        known_race_ids = {r[0] for r in self.conn.execute("SELECT race_id FROM races").fetchall()}

        ingested = skipped = 0
        for race_date, season, event_id_src, location, race in todo:
            race_id = db.slug_id(f"fgp-{season}-{_loc_slug(location)}-{race.lower()}")
            if race_id in known_race_ids:
                # Self-heal the handle so a format change reaches already-ingested
                # races (insert path is skipped for them).
                self.conn.execute("UPDATE races SET race_handle = ? WHERE race_id = ?",
                                  [_fgp_handle(location, season), race_id])
                skipped += 1
                continue
            print(f"\n{race_date}  {location} {race}  (source event {event_id_src})")
            self._ingest_race(race_id, race_date, season, event_id_src, location, race)
            ingested += 1

        print()
        print("=" * 60)
        print(f"DONE  ingested={ingested}  already-present={skipped}")
        print("=" * 60)
        self._dump_merge_candidates()
        db.reconcile_athlete_nationality(self.conn)

    def _ingest_race(self, race_id, race_date, season, event_id_src, location, race):
        rows = _api_call(f"/{event_id_src}/{race}/resultIndiv/")
        if not isinstance(rows, list) or not rows:
            print("  No results at source — skipping (race likely cancelled)")
            return

        # Some rows carry no licence (all of Saint-Jean-de-Monts 2023, the odd
        # anonymised entry elsewhere) — recover identity from the startlist by
        # bib number.
        bib_to_licence = {}
        if any(not r.get("FFTRI_athleteId") for r in rows):
            startlist = _api_call(f'/{event_id_src}/{race}/startlist/"')
            for s in startlist or []:
                lic = str(s.get("license", "")).strip()[:6]
                if _FFTRI_ID_RE.match(lic):
                    bib_to_licence[str(int(s["bib"]))] = lic
            print(f"  Rows missing licence ids; joined startlist by bib "
                  f"({len(bib_to_licence)} entries)")

        gender = RACE_GENDER[race]
        result_rows = []
        new_count = 0
        for r in rows:
            name_letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", f"{r.get('firstname', '')}{r.get('lastname', '')}")
            if str(r.get("rank", "")).strip() == "99999" or not name_letters:
                continue  # unfilled reserve bib slots
            fftri_id = str(r.get("FFTRI_athleteId") or "").strip()
            if not fftri_id:
                bib = str(r.get("bib", "")).strip().lstrip("0")
                fftri_id = bib_to_licence.get(bib, "")
            athlete_id, country_full, is_new = self._resolve_athlete(fftri_id, r, gender)
            new_count += is_new

            position, status = _parse_status(r["rank"])
            total, swim, t1, bike, t2, run = _parse_splits(r, season)
            try:
                start_num = int(str(r.get("bib", "0")).strip() or 0)
            except ValueError:
                start_num = 0

            result_rows.append((race_id, athlete_id, position, status, start_num,
                                total, swim, bike, run, t1, t2))
            # Nationality history only for FGP-minted athletes: for matched
            # WT/PTO athletes the FGP nation is often the licence country
            # (foreign pros on French licences), not a nationality change.
            if is_new:
                db.record_athlete_nationality(self.conn, athlete_id, country_full, race_date)

        # Event/race rows go in last so a crash mid-resolution can't leave a
        # results-less race behind that later runs would skip as ingested.
        event_id = db.slug_id(f"fgp-{season}-{_loc_slug(location)}")
        event_name = f"{season} French Grand Prix {location}"
        db.upsert_nationality(self.conn, "France")
        db.insert_event(
            self.conn,
            event_id=event_id,
            name=event_name,
            venue=location,
            country="France",
            continent="Europe",
            start_date=race_date,
            end_date=race_date,
            longitude=0,
            latitude=0,
        )
        db.insert_race(
            self.conn,
            race_id=race_id,
            event_id=event_id,
            race_title=event_name,
            prog_name=RACE_PROG[race],
            race_date=race_date,
            gender=gender,
            category="elite",
            sub_category="elite",
            cat_ids="[]",
            distance="sprint",
            race_handle=_fgp_handle(location, season),
        )
        db.insert_results_bulk(self.conn, result_rows)
        finishers = sum(1 for r in result_rows if r[2] is not None)
        print(f"  Inserted {len(result_rows)} results "
              f"({finishers} finishers, {new_count} new athletes)")


if __name__ == "__main__":
    conn = db.get_conn(read_only=False)
    try:
        FGPIngester(conn).run()
    finally:
        conn.close()
