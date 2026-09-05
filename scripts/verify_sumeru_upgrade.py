"""Verify migration on an isolated SQLite Backup API snapshot, never the source."""
import argparse
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from genshin_navigator.data_store import SqliteDataProvider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source_database", type=Path)
    parser.add_argument("catalog", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sumeru-upgrade-check-") as tmp:
        db = Path(tmp) / "snapshot.db"
        with closing(sqlite3.connect(args.source_database.resolve().as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(db)) as dest:
            source.backup(dest)
        preserved = ("progress", "poi_hints", "poi_hint_assets", "progress_sync_runs", "remote_progress_unknown")
        with closing(sqlite3.connect(db)) as conn:
            before = {table: conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in preserved}
        provider = SqliteDataProvider(db, backup_dir=Path(tmp) / "backups")
        provider.import_legacy(args.catalog, region_id="sumeru")
        provider.import_legacy(args.catalog, region_id="sumeru")
        with closing(sqlite3.connect(db)) as conn:
            for table in preserved:
                assert before[table] == conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall(), table
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        print(json.dumps({"passed": True, "preserved_rows": {t: len(v) for t,v in before.items()},
                          "sumeru": provider.status("sumeru")["poi_count"]}, indent=2))


if __name__ == "__main__":
    main()
