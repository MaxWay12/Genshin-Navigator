from __future__ import annotations

import unittest

from genshin_navigator.hotkeys import HotkeyAction
from genshin_navigator.tray import TrayController


class FakeBackend:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.emit = None
        self.closed = False

    def start(self, emit):
        if self.fail:
            raise RuntimeError("tray unavailable")
        self.emit = emit

    def close(self):
        self.closed = True


class TrayTests(unittest.TestCase):
    def test_tray_emits_actions_and_closes(self) -> None:
        actions = []
        backend = FakeBackend()
        tray = TrayController(actions.append, backend=backend)
        tray.start()
        backend.emit(HotkeyAction.TOGGLE_PAUSE)
        backend.emit(HotkeyAction.QUIT)
        tray.close()
        self.assertEqual(actions, [HotkeyAction.TOGGLE_PAUSE, HotkeyAction.QUIT])
        self.assertTrue(backend.closed)

    def test_tray_failure_is_non_fatal(self) -> None:
        tray = TrayController(lambda _action: None, backend=FakeBackend(fail=True))
        tray.start()
        self.assertFalse(tray.started)
        self.assertIn("unavailable", tray.error or "")


if __name__ == "__main__":
    unittest.main()
