import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from genshin_navigator.collection_plans import CollectionPlan, CollectionPlanRepository
from genshin_navigator.data_store import SqliteDataProvider
from genshin_navigator.map_viewport import MapViewport
from genshin_navigator.outing_progress import OutingProgress
from genshin_navigator.poi import PointOfInterest, MapSpaceMetric
from genshin_navigator.position import CoordinateSpace as CS
from genshin_navigator.progress_sync import SqliteProgressSyncStore
from genshin_navigator.navigation import NavigationController
from genshin_navigator.position import MapPosition, PositionState
from genshin_navigator.tracker import TrackerSnapshot


class ExplorationDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "data.db"
        self.provider = SqliteDataProvider(self.path)
        self.provider.replace_content("fontaine", [PointOfInterest(i, kind, i, "fontaine", "surface", CS.SURFACE_ATLAS, 1, 2)
                                      for i, kind in (("hoyolab:1", "chest"), ("hoyolab:2", "local_specialty"), ("hoyolab:3", "waypoint"))],
                                      [MapSpaceMetric("fontaine", "surface", CS.SURFACE_ATLAS, ((2,0),(0,2)))], content_version="test")

    def tearDown(self):
        self.temp.cleanup()

    def test_migration_backup_and_progress_preserved(self):
        self.provider.progress().mark_collected("hoyolab:1")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA user_version=3")
        SqliteDataProvider(self.path)
        self.assertTrue(list((self.root / "backups").iterdir()))
        self.assertEqual(self.provider.progress().collected_ids, {"hoyolab:1"})
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)

    def test_outing_is_separate_and_resettable(self):
        outing = OutingProgress(self.path)
        outing.mark_collected("hoyolab:2")
        self.assertEqual(outing.collected_ids, {"hoyolab:2"})
        self.assertEqual(self.provider.progress().collected_ids, set())
        self.assertEqual(SqliteProgressSyncStore(self.path).snapshot("fontaine")[0], {"hoyolab:1"})
        with self.assertRaises(ValueError): outing.mark_collected("hoyolab:3")
        outing.reset("fontaine")
        self.assertFalse(outing.collected_ids)

    def test_plan_roundtrip_and_invalid_import_no_write(self):
        repo = CollectionPlanRepository(self.path)
        plan = CollectionPlan("one", "Сбор", "fontaine", "surface", CS.SURFACE_ATLAS, ("hoyolab:1",))
        repo.save(plan)
        self.assertEqual(repo.get("one"), plan)
        path = self.root / "план.json"
        repo.export("one", path)
        imported, missing = repo.import_preview(path)
        self.assertNotEqual(imported.plan_id, plan.plan_id)
        self.assertFalse(missing)
        raw = plan.to_dict(); raw["poi_ids"] *= 2
        path.write_text(json.dumps(raw))
        with self.assertRaises(ValueError): repo.import_preview(path)
        self.assertEqual(repo.list("fontaine"), (plan,))

    def test_plan_rejects_cross_layer_and_keeps_missing_ids(self):
        repo = CollectionPlanRepository(self.path)
        with self.assertRaises(ValueError):
            repo.save(CollectionPlan("x", "x", "fontaine", "floor1", CS.LAYER_LOCAL, ("hoyolab:1",)))
        plan = CollectionPlan("one", "one", "fontaine", "surface", CS.SURFACE_ATLAS, ("absent",))
        with self.assertRaises(ValueError): repo.save(plan)
        repo.save(plan, allow_missing=True)
        self.assertEqual(repo.get("one"), plan)

    def test_viewport_cursor_anchor_and_pan(self):
        view = MapViewport.fit(800, 600, 2000, 1000)
        before = view.to_map(130, 210)
        zoomed = view.zoom(2, 130, 210)
        self.assertEqual(before, zoomed.to_map(130, 210))
        self.assertEqual(zoomed.to_map(*zoomed.to_screen(100,200)), (100,200))
        self.assertFalse(zoomed.pan(10, 5).following)

    def test_active_plan_collected_outing_landmark_and_undo(self):
        repo = CollectionPlanRepository(self.path)
        plan = CollectionPlan("one","one","fontaine","surface",CS.SURFACE_ATLAS,("hoyolab:1","hoyolab:2","hoyolab:3"))
        repo.save(plan); repo.activate("fontaine","one")
        def controller():
            return NavigationController(self.provider.catalog(),self.provider.progress(),plan_repository=repo,outing_progress=OutingProgress(self.path))
        nav = controller()
        p = MapPosition("fontaine","surface",CS.SURFACE_ATLAS,1,2,.9,PositionState.TRACKING,1)
        fix = TrackerSnapshot(PositionState.TRACKING,1,2,1,2,.9,None,"surface",True,False,None,p)
        self.assertEqual(nav.update(fix).target.id,"hoyolab:1")
        nav.mark_collected(); self.assertEqual(nav.current_target.id,"hoyolab:2")
        nav.mark_collected(); self.assertEqual(nav.current_target.id,"hoyolab:3")
        nav.undo(); self.assertEqual(nav.current_target.id,"hoyolab:2")
        self.assertEqual(repo.get("one").current_step,1)
        self.assertEqual(controller().update(fix).target.id,"hoyolab:2")
        nav.mark_collected(); nav.mark_collected()
        self.assertEqual(self.provider.progress().collected_ids,{"hoyolab:1"})
        self.assertEqual(repo.get("one").current_step,3)

    def test_previous_plan_step_skips_unavailable_entries(self):
        repo = CollectionPlanRepository(self.path)
        repo.save(CollectionPlan("one", "one", "fontaine", "surface", CS.SURFACE_ATLAS,
                                 ("hoyolab:1", "hoyolab:2", "hoyolab:3"), 2))
        repo.activate("fontaine", "one")
        outing = OutingProgress(self.path)
        outing.mark_collected("hoyolab:2")
        nav = NavigationController(self.provider.catalog(), self.provider.progress(),
                                   plan_repository=repo, outing_progress=outing)
        p = MapPosition("fontaine", "surface", CS.SURFACE_ATLAS, 1, 2, .9, PositionState.TRACKING, 1)
        fix = TrackerSnapshot(PositionState.TRACKING, 1, 2, 1, 2, .9, None, "surface", True, False, None, p)
        self.assertEqual(nav.update(fix).target.id, "hoyolab:3")
        self.assertEqual(nav.previous_target().id, "hoyolab:1")
        self.assertEqual(repo.get("one").current_step, 0)

    def test_schema_ddl_failure_is_rolled_back(self):
        from genshin_navigator.storage_schema import initialize_schema
        path = self.root/"empty.db"
        with closing(sqlite3.connect(path)) as db:
            db.set_authorizer(lambda action,a,b,c,d: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TABLE and a == "poi_categories" else sqlite3.SQLITE_OK)
            with self.assertRaises(sqlite3.DatabaseError):
                with db: initialize_schema(db)
            db.set_authorizer(None)
            self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],0)
