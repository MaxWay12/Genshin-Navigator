from __future__ import annotations

import queue
import time
import unittest

from genshin_navigator.hotkeys import (
    CollectedHoldController,
    GlobalHotkeyManager,
    HotkeyAction,
    HotkeyBinding,
    MOD_ALT,
    MOD_CONTROL,
)


class FakeHotkeyApi:
    def __init__(self, conflicts: set[int] | None = None):
        self.conflicts = conflicts or set()
        self.messages: queue.Queue[int | None] = queue.Queue()
        self.registered: list[tuple[int, int, int]] = []
        self.unregistered: list[int] = []

    def current_thread_id(self) -> int:
        return 123

    def register(self, hotkey_id: int, virtual_key: int, modifiers: int = 0) -> bool:
        self.registered.append((hotkey_id, virtual_key, modifiers))
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
            {HotkeyAction.NEXT: 0x66, HotkeyAction.UNDO: 0x68}, api=api,
            physical_key_state=lambda _key: 0,
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
            {HotkeyAction.NEXT: 0x66, HotkeyAction.UNDO: 0x68}, api=api,
            physical_key_state=lambda _key: 0,
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

    def test_physical_fallback_works_without_window_focus_and_fires_once(self) -> None:
        api = FakeHotkeyApi({0x66})
        down = False

        def key_state(virtual_key: int) -> int:
            return 0x8000 if down and virtual_key == 0x66 else 0

        manager = GlobalHotkeyManager(
            {HotkeyAction.NEXT: 0x66}, api=api, physical_key_state=key_state
        )
        manager.start()
        down = True
        for _ in range(100):
            actions = manager.poll()
            if actions:
                break
            time.sleep(0.002)
        self.assertEqual(actions, [HotkeyAction.NEXT])
        self.assertEqual(manager.poll(), [])
        down = False
        time.sleep(0.02)
        self.assertEqual(manager.poll(), [])
        # The real UI loop runs at 10 Hz. Move outside the duplicate-suppression
        # window before simulating a second deliberate press.
        time.sleep(0.21)
        down = True
        for _ in range(100):
            actions = manager.poll()
            if actions:
                break
            time.sleep(0.002)
        self.assertEqual(actions, [HotkeyAction.NEXT])
        manager.close()

    def test_short_press_is_captured_between_slow_ui_polls(self) -> None:
        api = FakeHotkeyApi({0x66})
        down = False

        def key_state(_virtual_key: int) -> int:
            return 0x8000 if down else 0

        manager = GlobalHotkeyManager(
            {HotkeyAction.NEXT: 0x66}, api=api, physical_key_state=key_state
        )
        manager.start()
        down = True
        time.sleep(0.03)
        down = False
        time.sleep(0.03)

        self.assertEqual(manager.poll(), [HotkeyAction.NEXT])
        manager.close()

    def test_action_can_have_numpad_and_ctrl_alt_alternative(self) -> None:
        api = FakeHotkeyApi()
        manager = GlobalHotkeyManager(
            {
                HotkeyAction.NEXT: [
                    HotkeyBinding(0x66),
                    HotkeyBinding(0x27, MOD_CONTROL | MOD_ALT),
                ]
            },
            api=api,
            physical_key_state=lambda _key: 0,
        )
        manager.start()
        self.assertEqual(
            api.registered,
            [(1, 0x66, 0), (2, 0x27, MOD_CONTROL | MOD_ALT)],
        )
        api.messages.put(2)
        for _ in range(100):
            actions = manager.poll()
            if actions:
                break
            time.sleep(0.001)
        self.assertEqual(actions, [HotkeyAction.NEXT])
        manager.close()

    def test_modifier_binding_requires_all_keys_and_supports_hold_state(self) -> None:
        down: set[int] = set()
        manager = GlobalHotkeyManager(
            {
                HotkeyAction.COLLECTED_HOLD: HotkeyBinding(
                    0x20, MOD_CONTROL | MOD_ALT
                )
            },
            api=FakeHotkeyApi(),
            physical_key_state=lambda key: 0x8000 if key in down else 0,
        )
        down.add(0x20)
        self.assertFalse(manager.is_action_down(HotkeyAction.COLLECTED_HOLD))
        down.update({0x11, 0x12})
        self.assertTrue(manager.is_action_down(HotkeyAction.COLLECTED_HOLD))


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
