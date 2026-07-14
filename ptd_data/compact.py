import os
import shutil

import duckdb

from config import DB_PATH


def compact():
    """Rewrite the DB into a fresh file to reclaim space DuckDB never frees
    in place. Ratings/rankings/form are wiped and recomputed every build;
    DuckDB appends the new versions and strands the old row-group blocks, so
    the file only grows even though the live data doesn't. EXPORT/IMPORT
    rebuilds it from scratch - this has cut the file by more than half."""
    before = DB_PATH.stat().st_size

    export_dir = DB_PATH.parent / ".compact_export"
    new_path = DB_PATH.parent / ".compact_new.duckdb"
    if export_dir.exists():
        shutil.rmtree(export_dir)
    for p in (new_path, new_path.with_suffix(".duckdb.wal")):
        p.unlink(missing_ok=True)

    src = duckdb.connect(str(DB_PATH), read_only=True)
    src.execute(f"EXPORT DATABASE '{export_dir}' (FORMAT parquet)")
    src.close()

    new = duckdb.connect(str(new_path))
    new.execute(f"IMPORT DATABASE '{export_dir}'")
    new.execute("CHECKPOINT")
    new.close()
    shutil.rmtree(export_dir)

    # Row counts must match exactly before the rebuild is allowed to replace
    # the live file - a truncated or partial rebuild must never win.
    old = duckdb.connect(str(DB_PATH), read_only=True)
    check = duckdb.connect(str(new_path), read_only=True)
    tables = [r[0] for r in old.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall()]
    for table in tables:
        n_old = old.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        n_new = check.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        if n_old != n_new:
            raise RuntimeError(f"Compaction row-count mismatch on {table}: {n_old} -> {n_new}")
    old.close()
    check.close()

    os.replace(new_path, DB_PATH)

    after = DB_PATH.stat().st_size
    print(f"Compacted DB: {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB "
          f"({(before - after) / 1e6:.1f} MB reclaimed)")


if __name__ == "__main__":
    compact()
