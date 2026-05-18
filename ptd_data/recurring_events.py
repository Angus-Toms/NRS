"""Fallback recurring-event detection.

Runs after `series_rules.apply()` during db build. For events with no
`event_recurring` row (i.e. not caught by any rule in series_rules.RULES),
cluster orphan events by fuzzy name match, restricted to:
  - same course bucket (short vs long), and
  - same first normalized token (cheap brand-prefix bucket).

A cluster of >= 2 events becomes a new `recurring_events` row.

Bias is toward under-detection: high similarity threshold, course bucketing,
first-token bucketing, and clusters of size 1 are left untouched.

    from ptd_data import recurring_events
    recurring_events.apply_fallback(conn)
"""
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from ptd_data import db


SIMILARITY_THRESHOLD = 0.94
NEAR_MISS_BAND = 0.04        # pairs within [threshold - band, threshold) are flagged
LOW_INTERNAL_PAIR = 0.96     # within accepted clusters, flag any internal pair below this

SHORT_DISTANCES = {"sprint", "standard"}
LONG_DISTANCES  = {"middle", "t100", "long"}

# Series whose member events should never be grouped into a fuzzy-fallback
# recurring. Used for championships that change venue every year — every
# edition has an identical name (e.g. "Ironman 70.3 World Championship") so
# the fuzzy clusterer would otherwise re-create the recurring we suppressed
# in series_rules.py.
NO_RECURRING_SERIES_SLUGS = {
    "im-703-world-championships",
    "wt-long-distance-championships",
}


def _normalize_name(name):
    """Strip year + diacritics + punctuation, lowercase, collapse whitespace.

    Brand tokens (ironman, challenge) are intentionally kept since they are
    part of franchise identity.
    """
    s = re.sub(r"\b\d{4}\b", " ", name)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9\s]", " ", s).lower()
    return " ".join(s.split())


def _course_for_event(distances):
    """Return 'short', 'long', or None given the distances seen on the event's races."""
    has_short = bool(distances & SHORT_DISTANCES)
    has_long  = bool(distances & LONG_DISTANCES)
    if has_short and not has_long:
        return "short"
    if has_long and not has_short:
        return "long"
    return None  # mixed or unknown - skip


def _cluster_bucket(items, threshold):
    """Union-find cluster a single (course, first-token) bucket.

    items: [(event_id, normalized_name), ...]
    Returns: (clusters, near_misses, accepted_pair_ratios)
    """
    parent = {eid: eid for eid, _ in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Pre-extract suffix token (last word of normalized name). For most
    # franchise / venue-suffixed event names the trailing token is the venue
    # ("Ironman Nice" -> "nice"). Requiring suffix-equality stops long shared
    # prefixes from inflating the ratio across different venues, e.g.
    # "World Triathlon Development Regional Cup Saly" vs "... Limon".
    suffix = {eid: name.split()[-1] for eid, name in items}

    near_misses = []
    accepted = {}  # frozenset({a,b}) -> ratio
    n = len(items)
    for i in range(n):
        eid_a, name_a = items[i]
        len_a = len(name_a)
        suf_a = suffix[eid_a]
        for j in range(i + 1, n):
            eid_b, name_b = items[j]
            if suf_a != suffix[eid_b]:
                continue  # different venue suffix -> not the same recurring event
            len_b = len(name_b)
            if abs(len_a - len_b) > 0.4 * max(len_a, len_b):
                continue  # cheap length pre-filter
            ratio = SequenceMatcher(None, name_a, name_b).ratio()
            if ratio >= threshold:
                union(eid_a, eid_b)
                accepted[frozenset((eid_a, eid_b))] = ratio
            elif ratio >= threshold - NEAR_MISS_BAND:
                near_misses.append((eid_a, eid_b, ratio))

    groups = defaultdict(list)
    for eid, _ in items:
        groups[find(eid)].append(eid)
    clusters = [g for g in groups.values() if len(g) >= 2]
    return clusters, near_misses, accepted


def apply_fallback(conn):
    print("Recurring fallback: clustering orphan events by fuzzy name within course + first-token buckets")

    blocked_event_ids = set()
    if NO_RECURRING_SERIES_SLUGS:
        placeholders = ",".join("?" * len(NO_RECURRING_SERIES_SLUGS))
        blocked_event_ids = {
            eid for (eid,) in conn.execute(f"""
                SELECT DISTINCT es.event_id
                FROM event_series es
                JOIN series s ON s.series_id = es.series_id
                WHERE s.slug IN ({placeholders})
            """, list(NO_RECURRING_SERIES_SLUGS)).fetchall()
        }

    rows = conn.execute("""
        SELECT e.event_id, e.name,
               ARRAY_AGG(DISTINCT r.distance) FILTER (WHERE r.distance IS NOT NULL) AS dists
        FROM events e
        LEFT JOIN event_recurring er ON er.event_id = e.event_id
        LEFT JOIN races r            ON r.event_id  = e.event_id
        WHERE er.event_id IS NULL
        GROUP BY e.event_id, e.name
    """).fetchall()
    rows = [r for r in rows if r[0] not in blocked_event_ids]

    name_lookup = {}
    buckets = defaultdict(list)  # (course, first_token) -> [(event_id, normalized_name)]
    skipped_no_course = 0
    skipped_empty_norm = 0

    for eid, name, dists in rows:
        if not name:
            continue
        distances = {d for d in (dists or []) if d}
        course = _course_for_event(distances)
        if course is None:
            skipped_no_course += 1
            continue
        norm = _normalize_name(name)
        if not norm:
            skipped_empty_norm += 1
            continue
        first_tok = norm.split()[0]
        name_lookup[eid] = name
        buckets[(course, first_tok)].append((eid, norm))

    all_clusters = []   # (course, [event_ids], accepted_pairs)
    all_near_misses = []  # (course, eid_a, eid_b, ratio)
    for (course, _tok), items in buckets.items():
        if len(items) < 2:
            continue
        clusters, near_misses, accepted = _cluster_bucket(items, SIMILARITY_THRESHOLD)
        for c in clusters:
            all_clusters.append((course, sorted(c), accepted))
        for a, b, r in near_misses:
            all_near_misses.append((course, a, b, r))

    # Build canonical name + slug per cluster, then insert.
    recurring_rows = []        # (rid, slug, name, venue_key)
    event_recurring_rows = []  # (event_id, rid)
    cluster_reports = []

    existing_slugs = {r[0] for r in conn.execute("SELECT slug FROM recurring_events").fetchall()}

    for course, eids, accepted_pairs in all_clusters:
        names = [name_lookup[e] for e in eids]
        norms = [_normalize_name(n) for n in names]

        # Canonical: most common normalized form; tiebreak shortest length, then lex.
        common = Counter(norms).most_common()
        top_count = common[0][1]
        candidates = [n for n, c in common if c == top_count]
        canonical_norm = sorted(candidates, key=lambda x: (len(x), x))[0]
        canonical_name = next(n for n in names if _normalize_name(n) == canonical_norm)
        # Drop the (4-digit) year — events are titled "2016 ITU World ..."
        # but the recurring spans many years so the year doesn't belong.
        canonical_name = re.sub(r"\b\d{4}\b\s*", "", canonical_name).strip()

        slug = canonical_norm.replace(" ", "-")
        if slug in existing_slugs:
            # Defensive: don't collide with an existing rule-based group.
            cluster_reports.append({
                "size": len(eids), "min_internal": None, "skipped_collision": True,
            })
            continue

        rid = db.slug_id(slug)
        recurring_rows.append((rid, slug, canonical_name, slug))
        for eid in eids:
            event_recurring_rows.append((eid, rid))

        # Min ratio across the (n choose 2) pairs in this cluster — flags
        # transitive weakness where union-find chained on a weak link.
        pair_ratios = []
        for i in range(len(eids)):
            for j in range(i + 1, len(eids)):
                r = accepted_pairs.get(frozenset((eids[i], eids[j])))
                if r is not None:
                    pair_ratios.append(r)
        min_internal = min(pair_ratios) if pair_ratios else None

        cluster_reports.append({
            "size": len(eids), "min_internal": min_internal,
            "skipped_collision": False,
        })

    if recurring_rows:
        conn.executemany(
            """INSERT OR IGNORE INTO recurring_events (recurring_event_id, slug, name, venue_key)
               VALUES (?, ?, ?, ?)""",
            recurring_rows,
        )
    if event_recurring_rows:
        conn.executemany(
            """INSERT INTO event_recurring (event_id, recurring_event_id) VALUES (?, ?)
               ON CONFLICT (event_id) DO NOTHING""",
            event_recurring_rows,
        )

    n_low = sum(1 for c in cluster_reports
                if (not c["skipped_collision"]) and c["min_internal"] is not None
                and c["min_internal"] < LOW_INTERNAL_PAIR)
    n_collisions = sum(1 for c in cluster_reports if c["skipped_collision"])
    n_grouped = sum(c["size"] for c in cluster_reports if not c["skipped_collision"])
    print(f"  {len(cluster_reports) - n_collisions} fallback recurring groups "
          f"({n_grouped} events grouped, "
          f"{n_collisions} skipped on slug collision, "
          f"{skipped_no_course} skipped for no/mixed course, "
          f"{skipped_empty_norm} skipped empty-name, "
          f"{len(all_near_misses)} near-misses, "
          f"{n_low} clusters with internal pair < {LOW_INTERNAL_PAIR})")


if __name__ == "__main__":
    conn = db.get_conn(read_only=False)
    apply_fallback(conn)
    conn.close()
