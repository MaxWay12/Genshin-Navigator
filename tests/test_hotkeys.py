from __future__ import annotations

import queue
import time
import unittest

from genshin_navigator.hotkeys import (
    CollectedHoldController,
    GlobalHotkeyManager,
    HotkeyAction,
)


class FakeHotkeyApi:
    def __init__(self, conflicts: set[int] | None = None):
        self.conflicts = conflicts or set()
        self.messages: queue.Queue[int | None] = queue.Queue()
        self.registered: list[tuple[int, int]] = []
        self.unregistered: list[int] = []

    def current_thread_id(self) -> int:
        return 123

    def register(self, hotkey_id: int, virtual_key: int) -> bool:
        self.registered.append((hotkey_id, virtual_key))
        return virtual_key not in self.conflicts

    def unregister(self, hotkey_id: int) -> None:
        self.unregistered.append(hotkey_id)

    def get_message(self) -> int | None:
        return self.messages.get(timeout=2)

    def post_quit(self, thread_id: int) -> None:
        self.messages.put(None)


class GlobalHotkeyTests(unittest.TestCase):
    def test_registered_action_is_queued_and_all_successes_are_released(self) -> None:
        api = FakeHotkeyApi()
        manager = GlobalHotkeyManager(
            {HotkeyAction.NEXT: 0x66, HotkeyAction.UNDO: 0x68}, api=api
        )
        manager.start()
        api.messages.put(1)
        for _ in range(100):
            actions = manager.poll()
            if actions:
                break
            time.sleep(0.001)
        self.assertEqual(actions, [HotkeyAction.NEXT])
        manager.close()
        self.assertEqual(api.unregistered, [1, 2])

    def test_registration_conflict_does_not_disable_other_keys(self) -> None:
        api = FakeHotkeyApi({0x66})
        manager = GlobalHotkeyManager(
            {HotkeyAction.NEXT: 0x66, HotkeyAction.UNDO: 0x68}, api=api
        )
        manager.start()
        self.assertEqual(manager.registration_errors, {HotkeyAction.NEXT: 0x66})
        api.messages.put(2)
        for _ in range(100):
            actions = manager.poll()
            if actions:
                break
            time.sleep(0.001)
        self.assertEqual(actions, [HotkeyAction.UNDO])
        manager.close()
        self.assertEqual(api.unregistered, [2])


class CollectedHoldTests(unittest.TestCase):
    def test_confirms_only_after_full_hold(self) -> None:
        hold = CollectedHoldController(1.0)
        self.assertTrue(hold.begin("chest:1", available=True, now=10.0))
        pending = hold.update(
            key_down=True, target_id="chest:1", available=True, now=10.7
        )
        self.assertTrue(pending.active)
        self.assertAlmostEqual(pending.progress, 0.7)
        confirmed = hold.update(
            key_down=True, target_id="chest:1", available=True, now=11.0
        )
        self.assertTrue(confirmed.confirmed)

    def test_release_target_change_and_stale_position_cancel(self) -> None:
        for kwargs in (
            {"key_down": False, "target_id": "chest:1", "available": True},
            {"key_down": True, "target_id": "chest:2", "available": True},
            {"key_down": True, "target_id": "chest:1", "available": False},
        ):
            hold = CollectedHoldController(1.0)
            hold.begin("chest:1", available=True, now=2.0)
            status = hold.update(now=2.2, **kwargs)
            self.assertTrue(status.cancelled)

    def test_cannot_start_without_fresh_target(self) -> None:
        hold = CollectedHoldController()
        self.assertFalse(hold.begin(None, available=True, now=1.0))
        self.assertFalse(hold.begin("chest:1", available=False, now=1.0))


if __name__ == "__main__":
    unittest.main()
