from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from genshin_navigator.calibration import DistanceCalibration
from genshin_navigator.navigation import NavigationController, NavigationPreferencesStore
from genshin_navigator.poi import MapSpaceMetric, PoiCatalog, PoiProgress, PointOfInterest
from genshin_navigator.position import CoordinateSpace, MapPosition, PositionState
from genshin_navigator.tracker import TrackerSnapshot


def position(layer: str, space: CoordinateSpace, x: float, y: float) -> MapPosition:
    return MapPosition(
        "fontaine", layer, space, x, y, 1.0, PositionState.TRACKING, 1.0
    )


def snapshot(pos: MapPosition | None, *, state: PositionState = PositionState.TRACKING, stale: bool = False) -> TrackerSnapshot:
    return TrackerSnapshot(
        state=state,
        x_px=pos.x if pos else None,
        y_px=pos.y if pos else None,
        raw_x_px=None,
        raw_y_px=None,
        confidence=pos.confidence if pos else 0.0,
        reference_id=pos.reference_id if pos else None,
        map_layer_id=pos.layer_id if pos else None,
        accepted=not stale,
        stale=stale,
        reason=None,
        position=pos,
    )


def poi(identifier: str, x: float, *, layer: str = "surface", space: CoordinateSpace = CoordinateSpace.SURFACE_ATLAS, kind: str = "chest") -> PointOfInterest:
    return PointOfInterest(identifier, kind, identifier, "fontaine", layer, space, x, 0)


class NavigationTests(unittest.TestCase):
    def test_order_does_not_change_while_moving(self):
        self.controller.update(snapshot(self.surface))
        original = [row.poi.id for row in self.controller.candidates()]
        self.controller.update(snapshot(position("surface", CoordinateSpace.SURFACE_ATLAS, 29, 0)))
        self.assertEqual([row.poi.id for row in self.controller.candidates()], original)
        self.assertEqual(self.controller.next_target().id, "middle")
        self.controller.skip()
        self.assertEqual(self.controller.current_target.id, "far")
        self.controller.refresh_candidates()
        self.assertEqual(self.controller.candidates()[0].poi.id, "far")

    def test_explicit_selection_and_restore_are_layer_scoped(self):
        self.controller.update(snapshot(self.surface))
        self.assertFalse(self.controller.select_target("floor"))
        self.assertTrue(self.controller.select_target("middle"))
        self.controller.blacklist_current()
        self.assertTrue(self.controller.restore_target("middle", "hidden"))
        self.assertEqual(self.progress.collected_ids, set())
        self.assertTrue(self.controller.select_target("middle"))
        self.controller.update(snapshot(self.surface, stale=True))
        self.assertFalse(self.controller.select_target("near"))
        self.assertTrue(all(row.distance_m is None and not row.selectable for row in self.controller.candidates()))

    def test_selected_target_survives_restart(self):
        store = NavigationPreferencesStore(Path(self.temporary.name) / "preferences.json")
        self.controller.preferences_store = store
        self.controller.update(snapshot(self.surface))
        self.controller.select_target("far")
        restored = NavigationController(self.catalog, self.progress, target_kinds={"chest"}, preferences_store=store)
        self.assertEqual(restored.update(snapshot(self.surface)).target.id, "far")
        restored.mark_collected()
        again = NavigationController(self.catalog, self.progress, target_kinds={"chest"}, preferences_store=store)
        self.assertNotEqual(again.update(snapshot(self.surface)).target.id, "far")

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        points = [
            poi("near", 10),
            poi("middle", 20),
            poi("far", 30),
            poi("hydro", 5, kind="hydroculus"),
            poi("floor", 1, layer="floor78", space=CoordinateSpace.LAYER_LOCAL),
        ]
        metrics = [
            MapSpaceMetric("fontaine", "surface", CoordinateSpace.SURFACE_ATLAS, ((2, 0), (0, 2))),
            MapSpaceMetric("fontaine", "floor78", CoordinateSpace.LAYER_LOCAL, ((1, 0), (0, 1))),
        ]
        self.catalog = PoiCatalog(points, metrics)
        self.progress = PoiProgress.load(Path(self.temporary.name) / "progress.json")
        self.controller = NavigationController(
            self.catalog,
            self.progress,
            target_kinds={"chest"},
            available_target_kinds={"chest", "hydroculus"},
            calibration=DistanceCalibration("fontaine", 1.5),
        )
        self.surface = position("surface", CoordinateSpace.SURFACE_ATLAS, 0, 0)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_target_is_sticky_while_player_moves(self) -> None:
        first = self.controller.update(snapshot(self.surface))
        moved = position("surface", CoordinateSpace.SURFACE_ATLAS, 29, 0)
        second = self.controller.update(snapshot(moved))

        self.assertEqual(first.target.id, "near")  # type: ignore[union-attr]
        self.assertEqual(second.target.id, "near")  # type: ignore[union-attr]

    def test_next_previous_and_session_skip(self) -> None:
        self.controller.update(snapshot(self.surface))
        self.assertEqual(self.controller.next_target().id, "middle")  # type: ignore[union-attr]
        self.assertEqual(self.controller.previous_target().id, "near")  # type: ignore[union-attr]
        self.controller.skip()
        self.assertEqual(self.controller.current_target.id, "middle")  # type: ignore[union-attr]
        self.assertEqual(self.progress.collected_ids, set())

    def test_collected_persists_and_undo_restores_it(self) -> None:
        self.controller.update(snapshot(self.surface))
        self.controller.mark_collected()
        self.assertEqual(self.controller.current_target.id, "middle")  # type: ignore[union-attr]
        self.controller.undo()

        restored = PoiProgress.load(self.progress.path)
        self.assertEqual(self.controller.current_target.id, "near")  # type: ignore[union-attr]
        self.assertEqual(restored.collected_ids, set())

    def test_undo_restores_skipped_target(self) -> None:
        self.controller.update(snapshot(self.surface))
        self.controller.skip()
        self.controller.undo()

        self.assertEqual(self.controller.current_target.id, "near")  # type: ignore[union-attr]

    def test_keeps_separate_target_for_each_layer(self) -> None:
        self.controller.update(snapshot(self.surface))
        self.controller.next_target()
        floor_position = position("floor78", CoordinateSpace.LAYER_LOCAL, 0, 0)
        floor_nav = self.controller.update(snapshot(floor_position))
        returned = self.controller.update(snapshot(self.surface))

        self.assertEqual(floor_nav.target.id, "floor")  # type: ignore[union-attr]
        self.assertEqual(returned.target.id, "middle")  # type: ignore[union-attr]

    def test_never_selects_cross_layer_poi(self) -> None:
        nav = self.controller.update(snapshot(self.surface))
        self.assertNotEqual(nav.target.id, "floor")  # type: ignore[union-attr]

    def test_freezes_direction_and_distance_for_stale_position(self) -> None:
        fresh = self.controller.update(snapshot(self.surface))
        stale = self.controller.update(
            snapshot(self.surface, state=PositionState.RELOCATING, stale=True)
        )

        self.assertTrue(fresh.available)
        self.assertEqual(fresh.distance_m, 30.0)
        self.assertFalse(stale.available)
        self.assertIsNone(stale.distance_m)
        self.assertIsNone(stale.bearing_degrees)
        self.assertEqual(stale.target.id, fresh.target.id)  # type: ignore[union-attr]

    def test_without_calibration_never_displays_map_pixels_as_meters(self) -> None:
        controller = NavigationController(self.catalog, self.progress, target_kinds={"chest"})
        nav = controller.update(snapshot(self.surface))
        self.assertTrue(nav.available)
        self.assertIsNone(nav.distance_m)

    def test_cycles_target_filter_without_selecting_other_layer(self) -> None:
        self.controller.update(snapshot(self.surface))
        label = self.controller.cycle_target_filter()
        nav = self.controller.update(snapshot(self.surface))
        self.assertEqual(label, "гидрокулы")
        self.assertEqual(nav.target.id, "hydro")  # type: ignore[union-attr]
        self.assertEqual(nav.target.layer_id, "surface")  # type: ignore[union-attr]

    def test_persistent_blacklist_survives_restart_and_undo_removes_it(self) -> None:
        path = Path(self.temporary.name) / "navigation.json"
        store = NavigationPreferencesStore(path)
        controller = NavigationController(
            self.catalog,
            self.progress,
            target_kinds={"chest"},
            available_target_kinds={"chest", "hydroculus"},
            preferences_store=store,
        )
        controller.update(snapshot(self.surface))
        controller.blacklist_current()
        restarted = NavigationController(
            self.catalog,
            self.progress,
            target_kinds={"chest"},
            available_target_kinds={"chest", "hydroculus"},
            preferences_store=store,
        )
        self.assertEqual(restarted.update(snapshot(self.surface)).target.id, "middle")  # type: ignore[union-attr]
        controller.undo()
        restarted_again = NavigationController(
            self.catalog,
            self.progress,
            target_kinds={"chest"},
            available_target_kinds={"chest", "hydroculus"},
            preferences_store=store,
        )
        self.assertEqual(restarted_again.update(snapshot(self.surface)).target.id, "near")  # type: ignore[union-attr]

    def test_summary_reports_filter_progress_skip_and_blacklist(self) -> None:
        self.controller.update(snapshot(self.surface))
        self.controller.skip()
        self.controller.blacklist_current()
        summary = self.controller.summary
        self.assertEqual(summary.total, 4)
        self.assertEqual(summary.session_skipped, 1)
        self.assertEqual(summary.blacklisted, 1)
        self.assertEqual(summary.remaining, 3)


if __name__ == "__main__":
    unittest.main()
