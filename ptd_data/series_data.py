"""Populate series metadata and race membership.

Run once (and re-run idempotently) to seed the series and race_series FK:

    python -m ptd_data.series_data
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ptd_data import db

WORLD_CHAMPS_EVENTS = [
    # 1989–2006 (standalone ITU World Championships)
    4905, 4904, 4903, 4900, 4899, 4897, 4896, 4892, 4889, 4849,
    4760, 4745, 4958, 4635, 4489, 4492, 4580, 4692,
    # 2007–2008 (BG World Championships)
    4792, 4793,
    # 2009–2025 Grand Finals / Championship Finals (no 2020 — COVID)
    5105, 4990, 45142, 54377, 66427, 74437, 90168, 97645, 107164,
    117107, 127488, 130051, 163568, 170132, 183767, 188992,
]

OLYMPIC_EVENTS = [4740, 4529, 4998, 60952, 100636, 131299, 163893]

# (series_id, name, slug, description, event_ids, prog_name_prefix)
SERIES_DEFS = [
    (1, "World Championship — Elite Men",
     "world-championship-elite-men",
     "Annual ITU/World Triathlon World Championship, Elite Men's race.",
     WORLD_CHAMPS_EVENTS, "Elite Men"),
    (2, "World Championship — Elite Women",
     "world-championship-elite-women",
     "Annual ITU/World Triathlon World Championship, Elite Women's race.",
     WORLD_CHAMPS_EVENTS, "Elite Women"),
    (3, "World Championship — U23 Men",
     "world-championship-u23-men",
     "Annual ITU/World Triathlon World Championship, U23 Men's race.",
     WORLD_CHAMPS_EVENTS, "U23 Men"),
    (4, "World Championship — U23 Women",
     "world-championship-u23-women",
     "Annual ITU/World Triathlon World Championship, U23 Women's race.",
     WORLD_CHAMPS_EVENTS, "U23 Women"),
    (5, "World Championship — Junior Men",
     "world-championship-junior-men",
     "Annual ITU/World Triathlon World Championship, Junior Men's race.",
     WORLD_CHAMPS_EVENTS, "Junior Men"),
    (6, "World Championship — Junior Women",
     "world-championship-junior-women",
     "Annual ITU/World Triathlon World Championship, Junior Women's race.",
     WORLD_CHAMPS_EVENTS, "Junior Women"),
    (7, "Olympic Games — Men",
     "olympic-games-men",
     "Olympic Games triathlon, Men's race (2000–present).",
     OLYMPIC_EVENTS, "Elite Men"),
    (8, "Olympic Games — Women",
     "olympic-games-women",
     "Olympic Games triathlon, Women's race (2000–present).",
     OLYMPIC_EVENTS, "Elite Women"),
]


def populate():
    conn = db.get_conn(read_only=False)

    # Clear existing membership so re-runs are idempotent
    conn.execute("DELETE FROM race_series")

    for series_id, name, slug, description, event_ids, prog_prefix in SERIES_DEFS:
        conn.execute("""
            INSERT OR REPLACE INTO series (series_id, name, slug, description)
            VALUES (?, ?, ?, ?)
        """, [series_id, name, slug, description])

        id_ph = ','.join(['?'] * len(event_ids))
        rows = conn.execute(f"""
            SELECT race_id FROM races
            WHERE event_id IN ({id_ph}) AND prog_name LIKE ?
        """, event_ids + [f"{prog_prefix}%"]).fetchall()

        for (race_id,) in rows:
            conn.execute(
                "INSERT OR IGNORE INTO race_series (race_id, series_id) VALUES (?, ?)",
                [race_id, series_id]
            )

        count = len(rows)
        if count:
            print(f"  Series {series_id} '{name}': {count} races")
        else:
            print(f"  Series {series_id} '{name}': NO RACES FOUND")

    conn.close()
    print("Done.")


if __name__ == "__main__":
    populate()
