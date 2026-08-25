from __future__ import annotations

import ctypes
from ctypes import wintypes
import queue
import threading
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import Callable, Protocol


class HotkeyAction(str, Enum):
    PREVIOUS = "previous"
    NEXT = "next"
    SKIP = "skip"
    COLLECTED_HOLD = "collected_hold"
    UNDO = "undo"
    TOGGLE_VIEW = "toggle_view"
    TOGGLE_LOCK = "toggle_lock"
    QUIT = "quit"


DEFAULT_HOTKEYS: dict[HotkeyAction, int] = {
    HotkeyAction.PREVIOUS: 0x64,  # VK_NUMPAD4
    HotkeyAction.NEXT: 0x66,  # VK_NUMPAD6
    HotkeyAction.SKIP: 0x62,  # VK_NUMPAD2
    HotkeyAction.COLLECTED_HOLD: 0x65,  # VK_NUMPAD5
    HotkeyAction.UNDO: 0x68,  # VK_NUMPAD8
    HotkeyAction.TOGGLE_VIEW: 0x60,  # VK_NUMPAD0
    HotkeyAction.TOGGLE_LOCK: 0x6E,  # VK_DECIMAL
    HotkeyAction.QUIT: 0x69,  # VK_NUMPAD9
}


class HotkeyApi(Protocol):
    def current_thread_id(self) -> int: ...
    def register(self, hotkey_id: int, virtual_key: int) -> bool: ...
    def unregister(self, hotkey_id: int) -> None: ...
    def get_message(self) -> int | None: ...
    def post_quit(self, thread_id: int) -> None: ...


class WindowsHotkeyApi:
    MOD_NOREPEAT = 0x4000
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012

    def current_thread_id(self) -> int:
        return int(ctypes.windll.kernel32.GetCurrentThreadId())

    def register(self, hotkey_id: int, virtual_key: int) -> bool:
        return bool(
            ctypes.windll.user32.RegisterHotKey(
                None, hotkey_id, self.MOD_NOREPEAT, virtual_key
            )
        )

    def unregister(self, hotkey_id: int) -> None:
        ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)

    def get_message(self) -> int | None:
        message = wintypes.MSG()
        result = ctypes.windll.user32.GetMessageW(
            ctypes.byref(message), None, 0, 0
        )
        if result <= 0 or message.message == self.WM_QUIT:
            return None
        if message.message == self.WM_HOTKEY:
            return int(message.wParam)
        return -1

    def post_quit(self, thread_id: int) -> None:
        ctypes.windll.user32.PostThreadMessageW(
            thread_id, self.WM_QUIT, 0, 0
        )


class GlobalHotkeyManager:
    """Register non-repeating Windows hotkeys and expose them as a safe queue."""

    def __init__(
        self,
        hotkeys: dict[HotkeyAction, int] | None = None,
        *,
        api: HotkeyApi | None = None,
        physical_key_state: Callable[[int], int] | None = None,
    ):
        self.hotkeys = dict(hotkeys or DEFAULT_HOTKEYS)
        self._api = api or WindowsHotkeyApi()
        self._actions: queue.SimpleQueue[tuple[HotkeyAction, float]] = queue.SimpleQueue()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ids = {
            index + 1: action for index, action in enumerate(self.hotkeys)
        }
        self.registration_errors: dict[HotkeyAction, int] = {}
        self._physical_key_state = physical_key_state or (
            lambda virtual_key: ctypes.windll.user32.GetAsyncKeyState(virtual_key)
        )
        self._physical_was_down = {action: False for action in self.hotkeys}
        self._last_delivered: dict[HotkeyAction, float] = {}

    def start(self, timeout: float = 2.0) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="navigator-hotkeys", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("Global hotkey thread did not start")

    def _run(self) -> None:
        self._thread_id = self._api.current_thread_id()
        registered: list[int] = []
        try:
            for hotkey_id, action in self._ids.items():
                virtual_key = self.hotkeys[action]
                if self._api.register(hotkey_id, virtual_key):
                    registered.append(hotkey_id)
                else:
                    self.registration_errors[action] = virtual_key
            self._ready.set()
            while True:
                hotkey_id = self._api.get_message()
                if hotkey_id is None:
                    break
                action = self._ids.get(hotkey_id)
                if action is not None:
                    self._actions.put((action, monotonic()))
        finally:
            for hotkey_id in registered:
                self._api.unregister(hotkey_id)
            self._ready.set()

    def poll(self) -> list[HotkeyAction]:
        candidates: list[tuple[HotkeyAction, float]] = []
        while True:
            try:
                candidates.append(self._actions.get_nowait())
            except queue.Empty:
                break
        now = monotonic()
        for action, virtual_key in self.hotkeys.items():
            is_down = bool(self._physical_key_state(virtual_key) & 0x8000)
            if is_down and not self._physical_was_down[action]:
                candidates.append((action, now))
            self._physical_was_down[action] = is_down
        actions: list[HotkeyAction] = []
        for action, timestamp in candidates:
            last = self._last_delivered.get(action, float("-inf"))
            if timestamp - last < 0.2:
                continue
            self._last_delivered[action] = timestamp
            actions.append(action)
        return actions

    def close(self) -> None:
        thread = self._thread
        if thread is None:
            return
        if self._thread_id is not None and thread.is_alive():
            self._api.post_quit(self._thread_id)
        thread.join(timeout=2.0)
        self._thread = None


@dataclass(frozen=True)
class HoldStatus:
    active: bool
    progress: float
    confirmed: bool = False
    cancelled: bool = False


class CollectedHoldController:
    def __init__(self, duration_seconds: float = 1.0):
        if duration_seconds <= 0:
            raise ValueError("Hold duration must be positive")
        self.duration_seconds = duration_seconds
        self._started_at: float | None = None
        self._target_id: str | None = None

    def begin(
        self, target_id: str | None, *, available: bool, now: float | None = None
    ) -> bool:
        if target_id is None or not available:
            return False
        self._target_id = target_id
        self._started_at = monotonic() if now is None else now
        return True

    def cancel(self) -> None:
        self._started_at = None
        self._target_id = None

    def update(
        self,
        *,
        key_down: bool,
        target_id: str | None,
        available: bool,
        now: float | None = None,
    ) -> HoldStatus:
        if self._started_at is None:
            return HoldStatus(False, 0.0)
        if not key_down or not available or target_id != self._target_id:
            self.cancel()
            return HoldStatus(False, 0.0, cancelled=True)
        elapsed = (monotonic() if now is None else now) - self._started_at
        progress = min(1.0, max(0.0, elapsed / self.duration_seconds))
        if progress >= 1.0:
            self.cancel()
            return HoldStatus(False, 1.0, confirmed=True)
        return HoldStatus(True, progress)
