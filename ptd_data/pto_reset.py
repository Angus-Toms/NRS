"""
Clear all PTO-sourced data from the DB so the PTO ingest can be re-run
without re-fetching from the WT API.

Removes (in FK-safe order):
  - Results tied to long-course races
  - The long-course races themselves
  - Any event rows orphaned by the above (events reference no more races)
  - event_series / event_recurring rows for those events
  - Athletes that only ever existed in PTO data (have pto_slug, no remaining results)

Preserves WT-matched athletes; their PTO enrichment (pto_slug, height_cm,
weight_kg, nickname) is cleared so the next PTO run can re-populate it.

    python -m ptd_data.pto_reset
"""
from ptd_data import db


def reset_pto(conn):
    pto_distances = "('middle','t100','long')"

    # Headline counts before delete — for the summary print
    n_results = conn.execute(
        f"SELECT COUNT(*) FROM results WHERE race_id IN "
        f"(SELECT race_id FROM races WHERE distance IN {pto_distances})"
    ).fetchone()[0]
    n_races = conn.execute(
        f"SELECT COUNT(*) FROM races WHERE distance IN {pto_distances}"
    ).fetchone()[0]

    # 1. Results → races
    conn.execute(
        f"DELETE FROM results WHERE race_id IN "
        f"(SELECT race_id FROM races WHERE distance IN {pto_distances})"
    )
    conn.execute(f"DELETE FROM races WHERE distance IN {pto_distances}")

    # 2. Events orphaned by the race deletion. WT events and PTO events live in
    # disjoint id spaces (WT uses API integer ids, PTO uses slug_id), so any
    # event with zero races now is a PTO event.
    conn.execute(
        "DELETE FROM event_series WHERE event_id NOT IN (SELECT event_id FROM races)"
    )
    conn.execute(
        "DELETE FROM event_recurring WHERE event_id NOT IN (SELECT event_id FROM races)"
    )
    n_events = conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_id NOT IN (SELECT event_id FROM races)"
    ).fetchone()[0]
    conn.execute(
        "DELETE FROM events WHERE event_id NOT IN (SELECT event_id FROM races)"
    )

    # 3. PTO-only athletes — had pto_slug and no surviving results.
    n_pto_only = conn.execute(
        "SELECT COUNT(*) FROM athletes WHERE pto_slug IS NOT NULL "
        "AND athlete_id NOT IN (SELECT DISTINCT athlete_id FROM results)"
    ).fetchone()[0]
    conn.execute(
        "DELETE FROM athletes WHERE pto_slug IS NOT NULL "
        "AND athlete_id NOT IN (SELECT DISTINCT athlete_id FROM results)"
    )

    # 4. WT-matched athletes that survive — clear the PTO enrichment so the
    # next run can redo the linking with the current match logic.
    n_unlinked = conn.execute(
        "SELECT COUNT(*) FROM athletes WHERE pto_slug IS NOT NULL"
    ).fetchone()[0]
    conn.execute(
        "UPDATE athletes SET pto_slug = NULL, height_cm = NULL, "
        "weight_kg = NULL, nickname = '' WHERE pto_slug IS NOT NULL"
    )

    print(f"Cleared {n_results} results, {n_races} races, {n_events} events")
    print(f"Deleted {n_pto_only} PTO-only athletes, unlinked {n_unlinked} WT-matched athletes")


if __name__ == "__main__":
    conn = db.get_conn(read_only=False)
    reset_pto(conn)
    conn.close()
