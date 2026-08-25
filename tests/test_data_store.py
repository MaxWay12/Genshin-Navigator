from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from genshin_navigator.data_store import (
    SCHEMA_VERSION,
    SqliteDataProvider,
    open_data_bundle,
)
from genshin_navigator.navigation import NavigationController
from genshin_navigator.poi import MapSpaceMetric, PointOfInterest
from genshin_navigator.position import (
    CoordinateSpace,
    MapPosition,
    PositionState,
)
from genshin_navigator.tracker import TrackerSnapshot


def metric(layer: str = "surface") -> MapSpaceMetric:
    space = (
        CoordinateSpace.SURFACE_ATLAS
        if layer == "surface"
        else CoordinateSpace.LAYER_LOCAL
    )
    return MapSpaceMetric("fontaine", layer, space, ((2.0, 0.0), (0.0, 2.0)))


def poi(identifier: str, *, layer: str = "surface") -> PointOfInterest:
    space = (
        CoordinateSpace.SURFACE_ATLAS
        if layer == "surface"
        else CoordinateSpace.LAYER_LOCAL
    )
    return PointOfInterest(
        identifier, "chest", identifier, "fontaine", layer, space, 10.0, 20.0
    )


class SqliteDataProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "data.db"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_creates_schema_and_reopens_offline(self) -> None:
        provider = SqliteDataProvider(self.database)
        provider.replace_content(
            "fontaine", [poi("one")], [metric()], content_version="fixture-1"
        )

        reopened = SqliteDataProvider(self.database)

        self.assertEqual(reopened.catalog().pois[0].id, "one")
        self.assertEqual(reopened.status()["schema_version"], SCHEMA_VERSION)

    def test_migrates_v1_to_current_without_losing_content_or_progress(self) -> None:
        provider = SqliteDataProvider(self.database)
        provider.replace_content(
            "fontaine", [poi("one")], [metric()], content_version="fixture-1"
        )
        provider.progress().mark_collected("one")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DROP TABLE poi_hint_assets")
            connection.execute("DROP TABLE poi_hints")
            connection.execute("PRAGMA user_version = 1")

        migrated = SqliteDataProvider(self.database)

        self.assertEqual(migrated.catalog().pois[0].id, "one")
        self.assertEqual(migrated.progress().collected_ids, {"one"})
        self.assertEqual(migrated.status()["schema_version"], SCHEMA_VERSION)
        with closing(sqlite3.connect(self.database)) as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertIn("poi_hints", tables)
        self.assertIn("poi_hint_assets", tables)
        self.assertIn("progress_sync_runs", tables)

    def test_migrates_v2_progress_to_pending_push(self) -> None:
        provider = SqliteDataProvider(self.database)
        provider.replace_content(
            "fontaine", [poi("one")], [metric()], content_version="fixture-1"
        )
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("ALTER TABLE progress RENAME TO progress_v3")
            connection.execute(
                "CREATE TABLE progress(poi_id TEXT PRIMARY KEY, collected INTEGER "
                "NOT NULL, sync_state TEXT NOT NULL DEFAULT 'local', updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO progress VALUES ('one', 1, 'local', 'fixture-time')"
            )
            connection.execute("DROP TABLE progress_v3")
            connection.execute("DROP TABLE progress_sync_runs")
            connection.execute("DROP TABLE remote_progress_unknown")
            connection.execute("PRAGMA user_version = 2")

        migrated = SqliteDataProvider(self.database)

        self.assertEqual(migrated.progress().collected_ids, {"one"})
        self.assertEqual(migrated.status()["pending_sync_count"], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute(
                "SELECT sync_state, remote_ignored FROM progress WHERE poi_id='one'"
            ).fetchone()
        self.assertEqual(row, ("pending_push", 0))

    def test_auto_imports_legacy_catalog_and_progress_once(self) -> None:
        catalog_path = self.root / "catalog.json"
        progress_path = self.root / "progress.json"
        catalog_path.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "map_version": "legacy-6.8",
                    "spaces": [metric().to_dict()],
                    "pois": [poi("one").to_dict()],
                }
            ),
            encoding="utf-8",
        )
        progress_path.write_text(
            json.dumps({"format_version": 1, "collected_ids": ["one"]}),
            encoding="utf-8",
        )

        first = open_data_bundle(
            backend="auto", database_path=self.database,
            catalog_path=catalog_path, progress_path=progress_path,
        )
        progress_path.write_text(
            json.dumps({"format_version": 1, "collected_ids": []}),
            encoding="utf-8",
        )
        second = open_data_bundle(
            backend="auto", database_path=self.database,
            catalog_path=catalog_path, progress_path=progress_path,
        )

        self.assertEqual(first.backend, "sqlite")
        self.assertEqual(second.progress.collected_ids, {"one"})
        self.assertEqual(second.provider.status()["content_version"], "legacy-6.8")  # type: ignore[union-attr]

    def test_legacy_catalog_without_metrics_keeps_distance_uncalibrated(self) -> None:
        catalog_path = self.root / "catalog.json"
        catalog_path.write_text(
            json.dumps({"format_version": 1, "pois": [poi("legacy").to_dict()]}),
            encoding="utf-8",
        )

        bundle = open_data_bundle(
            backend="auto", database_path=self.database,
            catalog_path=catalog_path, progress_path=self.root / "progress.json",
        )

        self.assertEqual([item.id for item in bundle.catalog.pois], ["legacy"])
        self.assertEqual(bundle.catalog.metrics, ())

    def test_sync_is_idempotent_and_missing_poi_becomes_inactive(self) -> None:
        provider = SqliteDataProvider(self.database)
        provider.replace_content(
            "fontaine", [poi("keep"), poi("removed")], [metric()],
            content_version="one",
        )
        progress = provider.progress()
        progress.mark_collected("removed")

        provider.replace_content(
            "fontaine", [poi("keep")], [metric()], content_version="two"
        )
        provider.replace_content(
            "fontaine", [poi("keep")], [metric()], content_version="two"
        )

        self.assertEqual([item.id for item in provider.catalog().pois], ["keep"])
        self.assertEqual(provider.progress().collected_ids, {"removed"})
        self.assertEqual(provider.status()["inactive_poi_count"], 1)

    def test_unknown_map_space_rejects_update_and_preserves_snapshot(self) -> None:
        provider = SqliteDataProvider(self.database)
        provider.replace_content(
            "fontaine", [poi("good")], [metric()], content_version="good"
        )

        with self.assertRaisesRegex(ValueError, "unknown map space"):
            provider.replace_content(
                "fontaine", [poi("bad", layer="missing")], [metric()],
                content_version="bad",
            )

        self.assertEqual(provider.status()["content_version"], "good")
        self.assertEqual([item.id for item in provider.catalog().pois], ["good"])

    def test_json_backend_remains_explicit_fallback(self) -> None:
        catalog_path = self.root / "catalog.json"
        catalog_path.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "spaces": [metric().to_dict()],
                    "pois": [poi("legacy").to_dict()],
                }
            ),
            encoding="utf-8",
        )

        bundle = open_data_bundle(
            backend="json", database_path=self.database,
            catalog_path=catalog_path, progress_path=self.root / "progress.json",
        )

        self.assertEqual(bundle.backend, "json")
        self.assertFalse(self.database.exists())

    def test_sqlite_backend_refuses_empty_store(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty"):
            open_data_bundle(
                backend="sqlite", database_path=self.database,
                catalog_path=None, progress_path=self.root / "progress.json",
            )

    def test_unknown_schema_is_not_silently_replaced_by_json(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("PRAGMA user_version = 99")

        with self.assertRaisesRegex(ValueError, "schema version"):
            open_data_bundle(
                backend="auto", database_path=self.database,
                catalog_path=None, progress_path=self.root / "progress.json",
            )

    def test_navigation_uses_sqlite_catalog_and_persistent_progress(self) -> None:
        provider = SqliteDataProvider(self.database)
        far = PointOfInterest(
            "far", "chest", "far", "fontaine", "surface",
            CoordinateSpace.SURFACE_ATLAS, 30.0, 40.0,
        )
        provider.replace_content(
            "fontaine", [poi("near"), far], [metric()],
            content_version="fixture",
        )
        position = MapPosition(
            "fontaine", "surface", CoordinateSpace.SURFACE_ATLAS,
            0.0, 0.0, 1.0, PositionState.TRACKING, 1.0,
        )
        snapshot = TrackerSnapshot(
            state=PositionState.TRACKING, x_px=0.0, y_px=0.0,
            raw_x_px=0.0, raw_y_px=0.0, confidence=1.0,
            reference_id="fixture", map_layer_id="surface", accepted=True,
            stale=False, reason=None, position=position,
        )
        controller = NavigationController(provider.catalog(), provider.progress())

        first = controller.update(snapshot)
        controller.mark_collected()

        self.assertEqual(first.target.id, "near")  # type: ignore[union-attr]
        self.assertEqual(controller.current_target.id, "far")  # type: ignore[union-attr]
        self.assertEqual(provider.progress().collected_ids, {"near"})


if __name__ == "__main__":
    unittest.main()
