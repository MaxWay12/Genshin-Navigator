from __future__ import annotations

import unittest

from genshin_navigator.debug_view import DebugMapView


class DebugMapHotkeyTests(unittest.TestCase):
    def test_global_numpad_action_fires_once_per_key_press(self) -> None:
        view = object.__new__(DebugMapView)
        view._numpad_was_down = {
            action: False for action in DebugMapView._NUMPAD_ACTIONS
        }
        numpad6 = DebugMapView._NUMPAD_ACTIONS["next"]
        held = lambda virtual_key: 0x8000 if virtual_key == numpad6 else 0
        released = lambda _virtual_key: 0

        self.assertEqual(view._poll_global_numpad_action(held), "next")
        self.assertIsNone(view._poll_global_numpad_action(held))
        self.assertIsNone(view._poll_global_numpad_action(released))
        self.assertEqual(view._poll_global_numpad_action(held), "next")


if __name__ == "__main__":
    unittest.main()
