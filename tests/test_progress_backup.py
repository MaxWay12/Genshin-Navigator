from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from genshin_navigator.data_store import SqliteDataProvider
from genshin_navigator.poi import MapSpaceMetric, PointOfInterest
from genshin_navigator.position import CoordinateSpace
from genshin_navigator.progress_backup import (
    DatabaseBackupManager,
    ProgressTransferService,
)


def poi(identifier: str) -> PointOfInterest:
    return PointOfInterest(
        identifier, "chest", identifier, "fontaine", "surface",
        CoordinateSpace.SURFACE_ATLAS, 10.0, 20.0,
    )


def metric() -> MapSpaceMetric:
    return MapSpaceMetric(
        "fontaine", "surface", CoordinateSpace.SURFACE_ATLAS,
        ((1.0, 0.0), (0.0, 1.0)),
    )


class ProgressBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "navigator.db"
        self.backups = self.root / "backups"
        self.provider = SqliteDataProvider(self.database)
        self.provider.replace_content(
            "fontaine", [poi("hoyolab:1"), poi("hoyolab:2")], [metric()],
            content_version="fixture-v1",
        )
        self.transfer = ProgressTransferService(
            self.database, backup_dir=self.backups, backup_retention=2
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_schema_migration_creates_consistent_backup(self) -> None:
        self.provider.progress().mark_collected("hoyolab:1")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("PRAGMA user_version=2")

        SqliteDataProvider(
            self.database, backup_dir=self.backups, backup_retention=2
        )

        copies = list(self.backups.glob("navigator_*.db"))
        self.assertEqual(len(copies), 1)
        with closing(sqlite3.connect(copies[0])) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(
                connection.execute(
                    "SELECT collected FROM progress WHERE poi_id='hoyolab:1'"
                ).fetchone()[0], 1,
            )

    def test_export_and_merge_import_preserve_user_intent(self) -> None:
        progress = self.provider.progress()
        progress.mark_collected("hoyolab:1")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "INSERT INTO progress VALUES ('hoyolab:2',0,'local','now',1)"
            )
        exported = self.transfer.export(self.root / "progress.json", "fontaine")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DELETE FROM progress")

        plan = self.transfer.preview_import(exported, "fontaine")
        self.transfer.apply_import(plan)

        with closing(sqlite3.connect(self.database)) as connection:
            rows = connection.execute(
                "SELECT poi_id,collected,sync_state,remote_ignored FROM progress "
                "ORDER BY poi_id"
            ).fetchall()
        self.assertEqual(rows, [
            ("hoyolab:1", 1, "pending_push", 0),
            ("hoyolab:2", 0, "local", 1),
        ])

    def test_replace_is_backed_up_and_unknown_ids_are_skipped(self) -> None:
        self.provider.progress().mark_collected("hoyolab:1")
        source = self.root / "incoming.json"
        source.write_text(json.dumps({
            "format_version": 1, "region_id": "fontaine",
            "collected_ids": ["hoyolab:2", "hoyolab:999"],
            "remote_ignored_ids": [],
        }), encoding="utf-8")

        plan = self.transfer.preview_import(source, "fontaine", replace=True)
        self.assertEqual(plan.unknown_ids, ("hoyolab:999",))
        self.transfer.apply_import(plan)

        self.assertEqual(self.provider.progress().collected_ids, {"hoyolab:2"})
        self.assertEqual(len(list(self.backups.glob("navigator_*.db"))), 1)

    def test_invalid_file_does_not_mutate_database(self) -> None:
        self.provider.progress().mark_collected("hoyolab:1")
        source = self.root / "bad.json"
        source.write_text('{"format_version": 99}', encoding="utf-8")

        with self.assertRaises(ValueError):
            self.transfer.preview_import(source, "fontaine")

        self.assertEqual(self.provider.progress().collected_ids, {"hoyolab:1"})

    def test_backup_retention_removes_oldest_copy(self) -> None:
        manager = DatabaseBackupManager(self.database, self.backups, retention=2)
        manager.create("one")
        manager.create("two")
        manager.create("three")

        self.assertEqual(len(list(self.backups.glob("navigator_*.db"))), 2)


if __name__ == "__main__":
    unittest.main()
