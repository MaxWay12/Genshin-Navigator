from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import ImageFont

from genshin_navigator.debug_view import DebugMapView
from genshin_navigator.hotkeys import CollectedHoldController, HotkeyAction
from genshin_navigator.hud import (
    HudStateStore,
    WindowGeometry,
    build_hud_presentation,
    clamp_geometry,
)
from genshin_navigator.navigation import NavigationSnapshot
from genshin_navigator.poi import PointOfInterest
from genshin_navigator.poi_guidance import HintState, PoiHint, PoiHintSnapshot
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
    def test_expanded_hint_hud_renders_without_changing_navigation_state(self) -> None:
        class HintService:
            snapshot = PoiHintSnapshot(
                "hoyolab:1", HintState.CACHED,
                PoiHint("hoyolab:1", content="Сундук находится под мостом."),
                message="Кэш",
            )

        view = object.__new__(DebugMapView)
        view.hud_width = 360
        view.hud_height = 150
        view.details_width = 360
        view.details_height = 650
        view._layer_labels = {"floor78": "Великое озеро · B1"}
        view._hint_service = HintService()
        view._hint_image_path = None
        view._hint_image = None
        view._details_page = 0
        font_path = Path("C:/Windows/Fonts/arial.ttf")
        view._unicode_font = ImageFont.truetype(str(font_path), 15)
        view._unicode_font_large = ImageFont.truetype(str(font_path), 19)
        view._toast_until = 0.0
        view._hold_progress = 0.0

        panel = view._render_details_hud(tracker(), navigation(), None)

        self.assertEqual(panel.shape, (650, 360, 3))
        self.assertGreater(int(panel.sum()), 0)

    def test_details_toggle_and_page_keys_preserve_single_window_mode(self) -> None:
        view = object.__new__(DebugMapView)
        view._hint_service = object()
        view._mode = "hud"
        view._details_open = False
        view._details_page = 0
        view._hold = CollectedHoldController()
        view._apply_window_mode = lambda: None

        view._dispatch_action(HotkeyAction.TOGGLE_DETAILS, None)
        view._dispatch_action(HotkeyAction.NEXT_PAGE, None)
        view._dispatch_action(HotkeyAction.PREVIOUS_PAGE, None)

        self.assertTrue(view._details_open)
        self.assertEqual(view._details_page, 0)
        self.assertEqual(view._mode, "hud")

    def test_hint_text_is_paginated_from_safe_presentation(self) -> None:
        snapshot = PoiHintSnapshot(
            "hoyolab:1", HintState.CACHED,
            PoiHint("hoyolab:1", content="Первая строка\nВторая строка", links=("https://example.test/a",)),
            message="Кэш",
        )

        lines = DebugMapView._hint_lines(snapshot, 30)

        self.assertIn("Первая строка", lines)
        self.assertIn("example.test", lines)

    def test_global_quit_requests_clean_window_shutdown(self) -> None:
        view = object.__new__(DebugMapView)
        view._quit_requested = False
        view._hold = CollectedHoldController()

        view._dispatch_action(HotkeyAction.QUIT, None)

        self.assertTrue(view._quit_requested)

    def test_pause_action_toggles_without_navigation_or_focus(self) -> None:
        view = object.__new__(DebugMapView)
        view._paused = False
        view._hold = CollectedHoldController()
        view._show_toast = lambda *_args, **_kwargs: None

        view._dispatch_action(HotkeyAction.TOGGLE_PAUSE, None)
        self.assertTrue(view.paused)
        view._dispatch_action(HotkeyAction.TOGGLE_PAUSE, None)
        self.assertFalse(view.paused)

    def test_direction_arrow_renders_for_fresh_bearing(self) -> None:
        canvas = np.zeros((150, 360, 3), dtype=np.uint8)

        DebugMapView._draw_hud_arrow(canvas, 45.0, (85, 220, 110))

        self.assertGreater(int(canvas.sum()), 0)

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
