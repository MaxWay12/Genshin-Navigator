from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from genshin_navigator.calibration import DistanceCalibration
from genshin_navigator.navigation import NavigationController
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


def poi(identifier: str, x: float, *, layer: str = "surface", space: CoordinateSpace = CoordinateSpace.SURFACE_ATLAS) -> PointOfInterest:
    return PointOfInterest(identifier, "chest", identifier, "fontaine", layer, space, x, 0)


class NavigationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        points = [
            poi("near", 10),
            poi("middle", 20),
            poi("far", 30),
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


if __name__ == "__main__":
    unittest.main()
