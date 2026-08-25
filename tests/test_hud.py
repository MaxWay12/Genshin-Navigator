from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from genshin_navigator.hud import (
    HudStateStore,
    WindowGeometry,
    build_hud_presentation,
    clamp_geometry,
)
from genshin_navigator.navigation import NavigationSnapshot
from genshin_navigator.poi import PointOfInterest
from genshin_navigator.position import CoordinateSpace, MapPosition, PositionState
from genshin_navigator.tracker import TrackerSnapshot


def position(state: PositionState = PositionState.TRACKING) -> MapPosition:
    return MapPosition(
        "fontaine", "floor78", CoordinateSpace.LAYER_LOCAL,
        10, 20, 0.9, state, 1.0, "ref",
    )


def tracker(*, state=PositionState.TRACKING, stale=False) -> TrackerSnapshot:
    pos = position(state) if state is not PositionState.LOST else None
    return TrackerSnapshot(
        state, pos.x if pos else None, pos.y if pos else None, None, None,
        0.9, "ref" if pos else None, "floor78" if pos else None,
        not stale, stale, "test" if stale else None, pos,
    )


def navigation(*, available=True) -> NavigationSnapshot:
    target = PointOfInterest(
        id="chest:1", kind="chest", name="Драгоценный сундук",
        region_id="fontaine", layer_id="floor78",
        coordinate_space=CoordinateSpace.LAYER_LOCAL, x=30, y=40,
    )
    return NavigationSnapshot(
        target, position(), available, not available,
        123.4 if available else None, 45.0 if available else None,
        None if available else "stale",
    )


class HudPresentationTests(unittest.TestCase):
    def test_fresh_navigation_shows_distance_arrow_and_readable_layer(self) -> None:
        result = build_hud_presentation(
            tracker(), navigation(), {"floor78": "Великое озеро · B1"}
        )
        self.assertEqual(result.distance, "≈123 м")
        self.assertEqual(result.bearing_degrees, 45.0)
        self.assertEqual(result.layer, "Великое озеро · B1")
        self.assertTrue(result.available)

    def test_stale_navigation_keeps_target_but_hides_direction(self) -> None:
        result = build_hud_presentation(
            tracker(state=PositionState.RELOCATING, stale=True),
            navigation(available=False), {},
        )
        self.assertIn("Драгоценный", result.target)
        self.assertEqual(result.distance, "позиция уточняется")
        self.assertIsNone(result.bearing_degrees)
        self.assertFalse(result.available)

    def test_lost_state_never_claims_tracking(self) -> None:
        result = build_hud_presentation(tracker(state=PositionState.LOST), None, {})
        self.assertEqual(result.state, "LOST")
        self.assertIsNone(result.bearing_degrees)


class HudStateTests(unittest.TestCase):
    def test_geometry_is_clamped_to_available_desktop(self) -> None:
        result = clamp_geometry(
            WindowGeometry(3000, -200, 600, 500),
            WindowGeometry(0, 0, 1920, 1080),
        )
        self.assertEqual(result, WindowGeometry(1320, 0, 600, 500))

    def test_state_round_trip_and_invalid_file_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hud.json"
            store = HudStateStore(path)
            expected = WindowGeometry(40, 60, 360, 150)
            store.save(expected)
            self.assertEqual(
                store.load(expected, WindowGeometry(0, 0, 1920, 1080)), expected
            )
            path.write_text("broken", encoding="utf-8")
            self.assertEqual(
                store.load(expected, WindowGeometry(0, 0, 1920, 1080)), expected
            )


if __name__ == "__main__":
    unittest.main()
