import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import numpy as np

from genshin_navigator.calibration import DistanceCalibration
from genshin_navigator.data_store import SqliteDataProvider
from genshin_navigator.hoyolab_poi import build_catalog
from genshin_navigator.poi import PointOfInterest, MapSpaceMetric
from genshin_navigator.position import CoordinateSpace
from genshin_navigator.region_sources import fetch_surface_metadata, regional_groups, surface_bounds
from genshin_navigator.surface_sections import build_surface_sections
from genshin_navigator.sumeru_upgrade import upgrade_config
from genshin_navigator.underground_pyramid import build_underground_pyramid


class SumeruRegionTests(unittest.TestCase):
    def test_metadata_requires_revision_and_origin(self):
        payload = {"all_map_list": [{"id": 2, "detail_v2": {"origin": [10, 20], "map_version": "a" * 32}}]}
        with patch("genshin_navigator.region_sources._fetch", return_value=payload):
            self.assertEqual(fetch_surface_metadata()["revision"], "a" * 32)
            self.assertEqual(fetch_surface_metadata(revision="b" * 32)["revision"], "b" * 32)
            with self.assertRaises(ValueError):
                fetch_surface_metadata(revision="../escape")

    def test_group_membership_uses_links_not_name_or_location(self):
        groups = [{"id": 7, "name": "unrelated", "floors": [{"id": 8, "point_ids": [1], "overlay": {"url": "image"}}]}]
        points = [{"id": 1, "area_id": 4}]
        selected, audit = regional_groups(groups, points, 4)
        self.assertEqual(selected, groups)
        self.assertEqual(audit[0]["evidence_point_ids"], [1])
        with self.assertRaises(ValueError):
            regional_groups(groups, points + [{"id": 2, "area_id": 8, "point_group": {"group_id": 7}}], 4)

    def test_unknown_group_aborts(self):
        with self.assertRaises(ValueError):
            regional_groups([], [{"id": 1, "area_id": 4, "point_group": {"group_id": 9}}], 4)

    def test_unavailable_overlay_is_explicit_not_surface(self):
        groups = [{"id": 7, "floors": [{"id": 0, "point_ids": [1], "overlay": {"url": ""}}]}]
        points = [{"id": 1, "area_id": 4, "label_id": 17, "x_pos": 2, "y_pos": 3,
                   "point_group": {"group_id": 7, "floor_id": 0}}]
        self.assertEqual(regional_groups(groups, points, 4)[1][0]["unavailable_floor_ids"], [0])
        surface = {"region_id": "sumeru", "atlas_size": [20, 20], "world_to_atlas": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
        pois, stats = build_catalog(points, [], surface, {"unavailable_floors": [{"group_id": 7, "floor_id": 0}]}, area_id=4)
        self.assertEqual(pois, [])
        self.assertEqual(stats["unavailable_floor"], 1)
        self.assertEqual(stats["skipped_unknown_floor"], 0)

    def test_bounds_exclude_other_regions_and_underground(self):
        points = [{"id": 1, "area_id": 4, "x_pos": 100, "y_pos": 200},
                  {"id": 2, "area_id": 8, "x_pos": 100000, "y_pos": 100000},
                  {"id": 3, "area_id": 4, "x_pos": 100000, "y_pos": 100000, "point_group": {"group_id": 1}}]
        x, y = surface_bounds(points, 4, [0, 0])
        self.assertEqual((x.start, x.stop, y.start, y.stop), (-1, 2, -1, 2))

    def test_section_coordinates_and_combined_pyramid_preserve_transform(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            atlas = Image.fromarray(np.random.default_rng(42).integers(0, 255, (100, 200, 3), dtype=np.uint8))
            levels = build_surface_sections(atlas, root, "sumeru", size=100, overlap=20)
            self.assertEqual(levels[1]["local_to_canonical"][0][2], 80)
            (root / "base.json").write_text(json.dumps({"levels": levels}))
            (root / "metadata.json").write_text(json.dumps({"region_id": "sumeru", "atlas_size": [200,100], "world_to_atlas": [[1,0,0],[0,1,0],[0,0,1]]}))
            (root / "underground.json").write_text('{"groups": []}')
            merged = build_underground_pyramid(root / "metadata.json", root / "base.json", root / "underground.json", root / "pyramid.json")
            self.assertEqual(merged["levels"][1]["local_to_canonical"], levels[1]["local_to_canonical"])
            self.assertTrue(all(l["id"].startswith("sumeru_") for l in merged["levels"]))

    def test_config_upgrade_preserves_user_settings_and_input(self):
        root = Path(__file__).resolve().parents[1]
        old = json.loads((root / "config.sumeru.example.json").read_text())
        new = json.loads((root / "config.sumeru-full.example.json").read_text())
        old["performance"]["mode"] = "low_cpu"
        old["future"] = {"keep": True}
        result = upgrade_config(old, new)
        self.assertEqual(result["data"]["region_id"], "sumeru")
        self.assertEqual(result["performance"]["mode"], "low_cpu")
        self.assertEqual(result["future"], {"keep": True})
        self.assertFalse(result["edge_correlation"]["enabled"])
        self.assertEqual(old["data"]["region_id"], "sumeru_desert")

    def test_world_calibration_survives_sumeru_rename_only(self):
        calibration = DistanceCalibration("sumeru_desert", 1.01)
        self.assertTrue(calibration.supports_region("sumeru"))
        self.assertFalse(calibration.supports_region("fontaine"))

    def test_migration_preserves_progress_and_hints_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = SqliteDataProvider(root / "test.db", backup_dir=root / "backups")
            def update(region, x):
                metric = MapSpaceMetric(region, "surface", CoordinateSpace.SURFACE_ATLAS, ((2, 0), (0, 2)))
                poi = PointOfInterest("hoyolab:1", "chest", "test", region, "surface", CoordinateSpace.SURFACE_ATLAS, x, 4)
                provider.replace_content(region, [poi], [metric], content_version="test")
            update("sumeru_desert", 10)
            provider.progress().mark_collected("hoyolab:1")
            with closing(sqlite3.connect(provider.database)) as conn, conn:
                conn.execute("UPDATE progress SET collected=0, sync_state='synced', remote_ignored=1")
                conn.execute("INSERT INTO poi_hints(poi_id,content,source,fetched_at) VALUES ('hoyolab:1','keep','test','2026-01-01')")
                before = conn.execute("SELECT * FROM progress").fetchall()
            update("sumeru", 100)
            update("sumeru", 100)
            self.assertEqual(len(list((root / "backups").glob('*.db'))), 1)
            with closing(sqlite3.connect(provider.database)) as conn:
                self.assertEqual(conn.execute("SELECT * FROM progress").fetchall(), before)
                self.assertEqual(conn.execute("SELECT content FROM poi_hints").fetchone()[0], "keep")
                self.assertEqual(conn.execute("SELECT region_id,x FROM pois").fetchall(), [("sumeru",100)])
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            with self.assertRaises(ValueError):
                update("sumeru_desert", 10)

    def test_bad_update_does_not_change_legacy_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = SqliteDataProvider(Path(tmp) / "test.db")
            with self.assertRaises(ValueError):
                provider.replace_content("sumeru", [], [], content_version="bad")
            self.assertTrue(provider.is_empty())


if __name__ == "__main__":
    unittest.main()
