"""
Detects races that should be excluded from ELO calculations.

Two detection strategies:
1. Subset detection - races whose athletes are a subset of another same-date, same-gender race
   (e.g. U23 results extracted from Elite race, championship results within a larger cup)
2. Oversized races - >100 entries, likely combined AG + elite, needs manual review

Usage:
    python -m ptd_data.ignored
"""

from collections import defaultdict

from ptd_data import db
from ptd_data.db import load_manual_ignored


MAX_RACE_SIZE = 100


def detect_all(conn):
    """Run all detection strategies and populate ignored_races table."""
    conn.execute("DELETE FROM ignored_races")

    ignored = {}  # race_id -> (reason, parent_race_id)

    # --- Subset detection ---
    # Group races by (race_date, gender), then check for athlete subset relationships
    races = conn.execute("""
        SELECT race_id, race_date, gender, prog_name, race_title
        FROM races
        ORDER BY race_date
    """).fetchall()

    groups = defaultdict(list)
    for race_id, race_date, gender, prog_name, race_title in races:
        groups[(race_date, gender)].append((race_id, prog_name, race_title))

    for (race_date, gender), group_races in groups.items():
        if len(group_races) < 2:
            continue

        # Fetch athlete sets for each race in the group
        race_athletes = {}
        for race_id, prog_name, race_title in group_races:
            athletes = set(
                r[0] for r in conn.execute(
                    "SELECT athlete_id FROM results WHERE race_id = ?", [race_id]
                ).fetchall()
            )
            race_athletes[race_id] = athletes

        # Check all pairs for subset relationships
        for i, (rid_a, prog_a, title_a) in enumerate(group_races):
            if rid_a in ignored:
                continue
            athletes_a = race_athletes[rid_a]
            if not athletes_a:
                continue

            for rid_b, prog_b, title_b in group_races[i + 1:]:
                if rid_b in ignored:
                    continue
                athletes_b = race_athletes[rid_b]
                if not athletes_b:
                    continue

                if athletes_a < athletes_b:
                    # A is a strict subset of B - ignore A, B is the parent
                    ignored[rid_a] = (f"{prog_a} results are a subset of {title_b}", rid_b)
                    break
                elif athletes_b < athletes_a:
                    # B is a strict subset of A - ignore B, A is the parent
                    ignored[rid_b] = (f"{prog_b} results are a subset of {title_a}", rid_a)

    # --- Oversized race detection ---
    # AG races legitimately have huge fields (an AG world champ band can
    # easily exceed 100 finishers), so the cap only applies to non-AG.
    oversized = conn.execute(f"""
        SELECT r.race_id, r.race_title, COUNT(*) as n
        FROM results res
        JOIN races r ON res.race_id = r.race_id
        WHERE r.sub_category != 'ag'
        GROUP BY r.race_id, r.race_title
        HAVING n > {MAX_RACE_SIZE}
    """).fetchall()

    for race_id, race_title, n in oversized:
        if race_id not in ignored:
            ignored[race_id] = (f"Race has {n} entries (likely combined AG/Elite), needs manual review", None)

    # --- Insert into DB ---
    if ignored:
        conn.executemany(
            "INSERT OR IGNORE INTO ignored_races (race_id, reason, parent_race_id) VALUES (?, ?, ?)",
            [(race_id, reason, parent_id) for race_id, (reason, parent_id) in ignored.items()],
        )

    print(f"Detected {len(ignored)} ignored races")
    for race_id, (reason, parent_id) in sorted(ignored.items()):
        print(f"  {race_id}: {reason}" + (f" (parent: {parent_id})" if parent_id else ""))

    # Always finish by re-applying manual overrides - detect_all cleared the table above.
    load_manual_ignored(conn)


if __name__ == "__main__":
    conn = db.get_conn(read_only=False)
    detect_all(conn)
    conn.close()
