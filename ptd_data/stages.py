"""
Detects multi-stage events (heats/semifinals/A-B finals/day-2 finals) and:

1. Flags the combined "Elite Men/Women" rollup row with is_multi_stage=TRUE
   so the UI can surface the nuance.
2. Adds stage rows (semifinals, A/B finals, day-2 finals, heats, ...) to
   ignored_races with parent_race_id pointing at the combined row, so they
   are excluded from ELO and collapsed under the combined on athlete history.

Must run AFTER ptd_data.ignored (which clears ignored_races at the start of
its run) so its inserts survive.

Usage:
    python -m ptd_data.stages

Rule: within one (event_id, gender), if there is a plain "Elite Men/Women"
(or similar category+gender) combined row AND at least one sibling row whose
prog_name names a sub-stage for the same category (or for no specific
category, like "Final Men"), flag the combined row and ignore the stages.
"""

import re

from ptd_data import db


_PLAIN_RE = re.compile(
    r'^\s*(elite|u23|junior|youth)\s+(?:men|women)\s*$',
    re.I,
)

# Sub-stage names. We keep `\bfinal\s+[ab]\b` (A/B finals) and
# `\bfinal\s+(men|women)\b` (day-2 gender-named finals like "Final Men");
# a bare `\bfinal\b` is too loose — plenty of age-group races use it too.
_STAGE_RE = re.compile(
    r'\b(?:'
    r'semi[- ]?final'
    r'|final\s+[a-c]\b'
    r'|final\s+[a-c]\s+(?:elite|u23|junior|youth)\s+(?:men|women)\b'
    r'|final\s+(?:elite|u23|junior|youth)?\s*(?:men|women)\b'
    r'|heat'
    r'|repechage'
    r'|qualifier'
    r'|last\s+chance'
    r'|quarter[- ]?final'
    r')\b',
    re.I,
)

_CAT_RE = re.compile(r'\b(elite|u23|junior|youth)\b', re.I)

# Bare "Final Men" / "Final Women" — used in older events (e.g. 2012) where
# this IS the combined/final row rather than "Elite Men/Women".
_BARE_FINAL_RE = re.compile(
    r'^\s*final\s+(?:(?:elite|u23|junior|youth)\s+)?(?:men|women)\s*$',
    re.I,
)


def combined_category(prog_name: str) -> str | None:
    """Returns 'elite'/'u23'/... if prog_name is a combined rollup, else None."""
    m = _PLAIN_RE.match(prog_name or '')
    return m.group(1).lower() if m else None


def stage_category(prog_name: str) -> tuple[bool, str | None]:
    """(is_stage, category_mentioned_in_name_or_None)."""
    if not _STAGE_RE.search(prog_name or ''):
        return (False, None)
    cat_m = _CAT_RE.search(prog_name)
    return (True, cat_m.group(1).lower() if cat_m else None)


def mark_multi_stage(conn) -> None:
    """Flag multi-stage combined rows and ignore their stage siblings."""
    rows = conn.execute("""
        SELECT race_id, event_id, gender, prog_name, race_title
        FROM races
    """).fetchall()

    groups: dict[tuple, list[tuple[int, str, str]]] = {}
    for race_id, event_id, gender, prog_name, race_title in rows:
        groups.setdefault((event_id, gender), []).append((race_id, prog_name, race_title))

    multi_stage_ids: list[int] = []
    # stage race_id -> (reason, parent combined race_id, combined sub_category)
    stage_to_parent: dict[int, tuple[str, int, str]] = {}

    for group_rows in groups.values():
        combined = [(rid, combined_category(pn), title)
                    for rid, pn, title in group_rows if combined_category(pn)]
        stages = [(rid, pn, *stage_category(pn)) for rid, pn, _ in group_rows]
        stages = [(rid, pn, cat) for rid, pn, is_s, cat in stages if is_s]

        # Fallback: older events (e.g. Tiszaujvaros 2012) use "Final Men/Women"
        # as the combined row rather than "Elite Men/Women". Treat it as combined
        # only when there is no plain "Elite/U23/..." combined in the same group.
        if not combined and stages:
            combined = []
            for rid, pn, title in group_rows:
                m = _BARE_FINAL_RE.match(pn)
                if not m:
                    continue
                tier_m = _CAT_RE.search(pn)
                combined.append((rid, tier_m.group(1).lower() if tier_m else 'elite', title))
            # These bare-final rows also matched _STAGE_RE so remove them from stages.
            bare_ids = {rid for rid, _, _ in combined}
            stages = [(rid, pn, cat) for rid, pn, cat in stages if rid not in bare_ids]

        if not combined or not stages:
            continue

        for crid, ccat, ctitle in combined:
            # Stages match a combined if they mention the same category, OR
            # have no explicit category (then they belong to the primary combined).
            matched_stages = [(rid, pn) for rid, pn, scat in stages
                              if scat == ccat or scat is None]
            if not matched_stages:
                continue
            multi_stage_ids.append(crid)
            for srid, spn in matched_stages:
                stage_to_parent[srid] = (
                    f"{spn} is a stage of multi-round event {ctitle}", crid, ccat,
                )

    conn.execute("UPDATE races SET is_multi_stage = FALSE")
    if multi_stage_ids:
        conn.executemany(
            "UPDATE races SET is_multi_stage = TRUE WHERE race_id = ?",
            [(rid,) for rid in multi_stage_ids],
        )
        # Also reclassify combined rows that matched the bare-final fallback
        # ("Final Men/Women") — ingest left them as 'ag'.
        conn.executemany(
            "UPDATE races SET category = 'elite', sub_category = 'elite'"
            " WHERE race_id = ? AND category = 'ag'",
            [(rid,) for rid in multi_stage_ids],
        )

    # Reclassify stage rows to match their combined parent. The first-word
    # ingest classifier tags them 'ag' because prog_name starts with
    # "Semifinal"/"Final", but they're really elite/u23/junior/youth fields.
    # category is always 'elite' for any elite-tier row; sub_category carries
    # the specific tier.
    if stage_to_parent:
        conn.executemany(
            "UPDATE races SET category = 'elite', sub_category = ? WHERE race_id = ?",
            [(ccat, rid) for rid, (_, _, ccat) in stage_to_parent.items()],
        )

    # Upsert stage rows into ignored_races. Delete first so we overwrite any
    # weaker reason/parent that ignored.py's generic subset detection wrote.
    if stage_to_parent:
        conn.executemany(
            "DELETE FROM ignored_races WHERE race_id = ?",
            [(rid,) for rid in stage_to_parent],
        )
        conn.executemany(
            "INSERT INTO ignored_races (race_id, reason, parent_race_id) VALUES (?, ?, ?)",
            [(rid, reason, parent) for rid, (reason, parent, _) in stage_to_parent.items()],
        )

    print(f"Flagged {len(multi_stage_ids)} combined multi-stage race rows, "
          f"ignored {len(stage_to_parent)} stage rows")


if __name__ == "__main__":
    conn = db.get_conn(read_only=False)
    mark_multi_stage(conn)
    conn.close()
