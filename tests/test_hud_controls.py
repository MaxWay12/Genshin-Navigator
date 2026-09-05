import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2

from genshin_navigator.debug_view import DebugMapView
from genshin_navigator.hotkeys import CollectedHoldController, HotkeyAction
from genshin_navigator.hud import HudPresentation
from genshin_navigator.hud_renderer import buttons, render_compact


class HudControlTests(unittest.TestCase):
    def view(self, available=True):
        view = object.__new__(DebugMapView)
        view._mode, view._locked = "hud", False
        view._hud_hits = buttons(360, 150, available=available)
        view._mouse_collect = False
        view._mouse_pressed_action = None
        view._last_navigation = SimpleNamespace(target=SimpleNamespace(id="chest"), available=available)
        view._hold = CollectedHoldController(1.0)
        view._navigation = Mock()
        view._hotkeys = Mock(is_action_down=Mock(return_value=False))
        view._show_toast = Mock()
        return view

    def click_position(self, view, action):
        rect = next(hit.rect for hit in view._hud_hits if hit.action is action)
        return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)

    def test_normal_click_dispatches_once_and_locked_ignores_click(self):
        view = self.view()
        view._dispatch_action = Mock()
        x, y = self.click_position(view, HotkeyAction.NEXT)
        view._on_mouse(cv2.EVENT_LBUTTONDOWN, x, y, 0)
        view._on_mouse(cv2.EVENT_LBUTTONUP, x, y, 0)
        view._dispatch_action.assert_called_once_with(HotkeyAction.NEXT, view._last_navigation)
        view._locked = True
        view._on_mouse(cv2.EVENT_LBUTTONDOWN, x, y, 0)
        view._on_mouse(cv2.EVENT_LBUTTONUP, x, y, 0)
        self.assertEqual(view._dispatch_action.call_count, 1)

    def test_short_collect_and_leaving_button_do_not_change_progress(self):
        view = self.view()
        x, y = self.click_position(view, HotkeyAction.COLLECTED_HOLD)
        view._on_mouse(cv2.EVENT_LBUTTONDOWN, x, y, 0)
        view._on_mouse(cv2.EVENT_LBUTTONUP, x, y, 0)
        self.assertFalse(view._mouse_collect)
        view._navigation.mark_collected.assert_not_called()
        view._on_mouse(cv2.EVENT_LBUTTONDOWN, x, y, 0)
        view._on_mouse(cv2.EVENT_MOUSEMOVE, 0, 0, 0)
        self.assertFalse(view._mouse_collect)

    def test_mouse_hold_collects_once_and_focus_loss_cancels(self):
        view = self.view()
        x, y = self.click_position(view, HotkeyAction.COLLECTED_HOLD)
        with patch("genshin_navigator.hotkeys.monotonic", return_value=0):
            view._on_mouse(cv2.EVENT_LBUTTONDOWN, x, y, 0)
        view._mouse_still_on_collect = Mock(return_value=True)
        with patch("genshin_navigator.hotkeys.monotonic", return_value=1.1):
            view._update_hold(view._last_navigation)
            view._update_hold(view._last_navigation)
        view._navigation.mark_collected.assert_called_once()
        view._on_mouse(cv2.EVENT_LBUTTONDOWN, x, y, 0)
        view._mouse_still_on_collect.return_value = False
        view._update_hold(view._last_navigation)
        self.assertFalse(view._mouse_collect)
        view._navigation.mark_collected.assert_called_once()

    def test_stale_controls_disabled_and_sizes_render(self):
        view = self.view(False)
        x, y = self.click_position(view, HotkeyAction.COLLECTED_HOLD)
        view._on_mouse(cv2.EVENT_LBUTTONDOWN, x, y, 0)
        self.assertFalse(view._mouse_collect)
        presentation = HudPresentation("Очень длинное название сундука", "≈123 м", "Сумеру · Поверхность", "TRACKING", 45, True)
        for width, height in ((240, 120), (360, 150), (420, 200)):
            panel, hits = render_compact(presentation, width, height)
            self.assertEqual(panel.shape, (height, width, 3))
            self.assertEqual(len(hits), 10)
            for hit in hits:
                self.assertLessEqual(hit.rect[2], width)
                self.assertLessEqual(hit.rect[3], height)

    def test_target_change_or_lost_cancels_mouse_confirmation(self):
        for target, available in (("other", True), ("chest", False)):
            view = self.view()
            x, y = self.click_position(view, HotkeyAction.COLLECTED_HOLD)
            with patch("genshin_navigator.hotkeys.monotonic", return_value=0):
                view._on_mouse(cv2.EVENT_LBUTTONDOWN, x, y, 0)
            view._mouse_still_on_collect = Mock(return_value=True)
            nav = SimpleNamespace(target=SimpleNamespace(id=target), available=available)
            with patch("genshin_navigator.hotkeys.monotonic", return_value=1.1):
                view._update_hold(nav)
            view._navigation.mark_collected.assert_not_called()
