from __future__ import annotations
from dataclasses import replace

import ctypes
import queue
import textwrap
import time
from math import cos, radians, sin
from pathlib import Path
from typing import Callable

import cv2
from .image_io import read_image
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .hotkeys import (
    CollectedHoldController,
    GlobalHotkeyManager,
    HotkeyAction,
    HotkeyBinding,
)
from .hud import HudStateStore, WindowGeometry, build_hud_presentation
from .navigation import NavigationController, NavigationSnapshot
from .map_viewport import MapViewport
from .poi import PointOfInterest, PoiRepository, ProgressRepository
from .poi_guidance import HintState, PoiHintService, PoiHintSnapshot
from .performance import PerformanceSnapshot
from .tracker import TrackerSnapshot, TrackerState
from .tray import TrayController
from .target_panel import render_panel, marker_hits


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


class DebugMapView:
    """A single Navigator window with compact HUD and full-map modes."""

    VK_NUMPAD5 = 0x65
    GWL_STYLE = -16
    GWL_EXSTYLE = -20
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOZORDER = 0x0004
    SWP_FRAMECHANGED = 0x0020

    def __init__(
        self,
        atlas: np.ndarray,
        layer_maps: dict[str, np.ndarray] | None = None,
        poi_catalog: PoiRepository | None = None,
        poi_kinds: set[str] | None = None,
        poi_target_kinds: set[str] | None = None,
        poi_progress: ProgressRepository | None = None,
        navigation: NavigationController | None = None,
        max_width: int = 1100,
        max_height: int = 720,
        *,
        layer_labels: dict[str, str] | None = None,
        default_view: str = "hud",
        hud_width: int = 360,
        hud_height: int = 150,
        hud_state_path: str | Path = "datasets/local/ui/hud_state.json",
        collected_hold_seconds: float = 1.0,
        global_hotkeys: bool = True,
        hotkey_virtual_keys: dict[
            HotkeyAction, int | HotkeyBinding | list[HotkeyBinding]
        ] | None = None,
        hotkey_manager: GlobalHotkeyManager | None = None,
        hint_service: PoiHintService | None = None,
        details_width: int | None = None,
        details_height: int = 650,
        report_callback: Callable[[], bool] | None = None,
        trace_callback=None,
        trace_status=None,
        progress_status: Callable[[], dict[str, object]] | None = None,
        tray_enabled: bool = True,
        tray_controller: TrayController | None = None,
    ):
        if atlas is None or atlas.size == 0:
            raise ValueError("Debug atlas is empty")
        self.window_name = "Genshin Navigator"
        self.max_width = max_width
        self.max_height = max_height
        self.hud_width = hud_width
        self.hud_height = hud_height
        self.details_width = details_width or hud_width
        self.details_height = details_height
        self._mode = default_view
        self._details_open = False
        self._details_page = 0
        self._hint_target_id: str | None = None
        self._hint_service = hint_service
        self._hint_image_path: Path | None = None
        self._hint_image: np.ndarray | None = None
        self._image_viewport = None
        self._image_drag = None
        self._detail_hits = []
        self._locked = True
        self._hud_hits = ()
        self._mouse_hover = None
        self._mouse_collect = False
        self._mouse_pressed_action = None
        self._last_navigation = None
        self._canvas_size = (hud_width, hud_height)
        self._target_page = 0
        self._target_section = "available"
        self._target_hits = []
        self._overlap_ids = ()
        self._browsed_layer = None
        self._map_query = ""
        self._map_kind = None
        self._search_edit = False
        self._clusters = ()
        self._layers = {"surface": atlas}
        self._layers.update(layer_maps or {})
        self._layer_labels = {"surface": "Фонтейн · Поверхность"}
        self._layer_labels.update(layer_labels or {})
        self._poi_catalog = poi_catalog
        self._poi_kinds = poi_kinds
        self._poi_target_kinds = poi_target_kinds
        self._poi_progress = poi_progress
        self._navigation = navigation
        self._hold = CollectedHoldController(collected_hold_seconds)
        self._hold_progress = 0.0
        self._toast = ""
        self._toast_until = 0.0
        self._quit_requested = False
        self._paused = False
        self._report_callback = report_callback
        self._trace_callback = trace_callback
        self._trace_status = trace_status
        self._progress_status_supplier = progress_status
        self._progress_status_cache: dict[str, object] = {}
        self._progress_status_at = 0.0
        self._external_actions: queue.SimpleQueue[HotkeyAction] = queue.SimpleQueue()
        self._tray = tray_controller or TrayController(
            self._external_actions.put, enabled=tray_enabled
        )
        self._active_layer_id = ""
        self._state_store = HudStateStore(hud_state_path)
        self._hotkeys = hotkey_manager
        if self._hotkeys is None and global_hotkeys:
            self._hotkeys = GlobalHotkeyManager(hotkey_virtual_keys)
        font_path = Path("C:/Windows/Fonts/arial.ttf")
        self._unicode_font = ImageFont.truetype(str(font_path), 15) if font_path.exists() else ImageFont.load_default()
        self._unicode_font_large = ImageFont.truetype(str(font_path), 19) if font_path.exists() else ImageFont.load_default()
        self.base = np.empty((0, 0, 3), dtype=np.uint8)
        self.scale = 1.0
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self.window_name, self._on_mouse)
        try:
            cv2.setWindowProperty(self.window_name, cv2.WND_PROP_TOPMOST, 1)
        except cv2.error:
            pass
        self._select_layer("surface")
        geometry = self._state_store.load(
            WindowGeometry(20, 70, hud_width, hud_height), self._work_area()
        )
        cv2.moveWindow(self.window_name, geometry.x, geometry.y)
        self._apply_window_mode()
        self._set_locked_style(True)
        if self._hotkeys is not None:
            self._hotkeys.start()
            if self._hotkeys.registration_errors:
                keys = ", ".join(action.value for action in self._hotkeys.registration_errors)
                self._show_toast(f"Конфликт клавиш: {keys}", 6.0)
        self._tray.start()
        if self._tray.error:
            self._show_toast("Tray недоступен — Num9 или Ctrl+Alt+Q закроет Navigator", 5.0)
        try:
            if not bool(ctypes.windll.shell32.IsUserAnAdmin()):
                self._show_toast("Если Genshin запущен администратором, запустите Navigator так же", 7.0)
        except (AttributeError, OSError):
            pass

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def locked(self) -> bool:
        return self._locked

    @property
    def paused(self) -> bool:
        return self._paused

    def _window_handle(self) -> int:
        return int(ctypes.windll.user32.FindWindowW(None, self.window_name) or 0)

    @staticmethod
    def _work_area() -> WindowGeometry:
        user32 = ctypes.windll.user32
        x, y = int(user32.GetSystemMetrics(76)), int(user32.GetSystemMetrics(77))
        width, height = int(user32.GetSystemMetrics(78)), int(user32.GetSystemMetrics(79))
        if width <= 0 or height <= 0:
            x = y = 0
            width, height = int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        return WindowGeometry(x, y, width, height)

    def _window_geometry(self) -> WindowGeometry | None:
        handle, rect = self._window_handle(), _Rect()
        if not handle or not ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(rect)):
            return None
        return WindowGeometry(
            int(rect.left), int(rect.top), max(1, int(rect.right - rect.left)),
            max(1, int(rect.bottom - rect.top)),
        )

    def _set_locked_style(self, locked: bool) -> None:
        self._locked = locked
        handle = self._window_handle()
        if not handle:
            return
        user32 = ctypes.windll.user32
        style = int(user32.GetWindowLongW(handle, self.GWL_STYLE))
        exstyle = int(user32.GetWindowLongW(handle, self.GWL_EXSTYLE))
        if locked:
            style &= ~(self.WS_CAPTION | self.WS_THICKFRAME)
            exstyle |= self.WS_EX_TRANSPARENT | self.WS_EX_NOACTIVATE | self.WS_EX_TOOLWINDOW
        else:
            style |= self.WS_CAPTION | self.WS_THICKFRAME
            exstyle &= ~(self.WS_EX_TRANSPARENT | self.WS_EX_NOACTIVATE)
        user32.SetWindowLongW(handle, self.GWL_STYLE, style)
        user32.SetWindowLongW(handle, self.GWL_EXSTYLE, exstyle)
        user32.SetWindowPos(
            handle, None, 0, 0, 0, 0,
            self.SWP_NOMOVE | self.SWP_NOSIZE | self.SWP_NOZORDER | self.SWP_FRAMECHANGED,
        )

    def _toggle_lock(self) -> None:
        if self._locked:
            self._set_locked_style(False)
            self._show_toast("HUD разблокирован — перетащите окно")
        else:
            geometry = self._window_geometry()
            self._set_locked_style(True)
            if geometry is not None and self._mode == "hud":
                self._state_store.save(WindowGeometry(geometry.x, geometry.y, self.hud_width, self.hud_height))
            self._show_toast("Положение HUD сохранено")

    def _toggle_view(self) -> None:
        self._mode = "map" if self._mode == "hud" else "hud"
        self._apply_window_mode()
        self._show_toast(
            "Полная карта"
            if self._mode == "map"
            else "Подсказка POI" if self._details_open else "Компактный HUD"
        )

    def _toggle_details(self) -> None:
        if self._hint_service is None:
            self._show_toast("Подсказки отключены")
            return
        if self._mode == "map":
            self._mode = "hud"
            self._details_open = True
        else:
            self._details_open = not self._details_open
        self._details_page = 0
        self._apply_window_mode()

    def _apply_window_mode(self) -> None:
        if self._mode == "hud":
            size = (
                (self.details_width, self.details_height)
                if self._details_open else (self.hud_width, self.hud_height)
            )
            cv2.resizeWindow(self.window_name, *size)
        elif self.base.size:
            cv2.resizeWindow(self.window_name, self.base.shape[1] + 360, max(750, self.base.shape[0] + 142))

    def _select_layer(self, layer_id: str | None) -> None:
        selected = layer_id if layer_id in self._layers else "surface"
        if selected == self._active_layer_id:
            return
        image = self._layers[selected]
        source_height, source_width = image.shape[:2]
        self.scale = min((self.max_width - 360) / source_width, self.max_height / source_height, 1.0)
        size = (max(1, round(source_width * self.scale)), max(1, round(source_height * self.scale)))
        self.base = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        self._viewport = MapViewport.fit(size[0], size[1], source_width, source_height)
        self._viewport_cache_key = None
        self._map_drag = None
        self._map_dragged = False
        self._active_layer_id = selected
        if self._mode == "map":
            self._apply_window_mode()

    @staticmethod
    def _poi_color(poi: PointOfInterest) -> tuple[int, int, int]:
        return {"chest": (40, 180, 255), "hydroculus": (255, 210, 60), "waypoint": (255, 120, 60)}.get(poi.kind, (210, 210, 210))

    def _show_toast(self, message: str, duration: float = 2.5) -> None:
        self._toast, self._toast_until = message, time.monotonic() + duration

    def notify(self, message: str, duration: float = 4.0) -> None:
        self._show_toast(message, duration)

    def _dispatch_action(self, action: HotkeyAction, navigation: NavigationSnapshot | None) -> None:
        if action is not HotkeyAction.COLLECTED_HOLD:
            self._mouse_collect = False
        if action is HotkeyAction.OPEN_SETTINGS:
            self.settings_requested = True
            self._hold.cancel()
            self._quit_requested = True
            return
        if action is HotkeyAction.QUIT:
            self._hold.cancel()
            self._quit_requested = True
            return
        if action is HotkeyAction.TOGGLE_PAUSE:
            self._hold.cancel()
            self._paused = not self._paused
            self._show_toast("Navigator приостановлен" if self._paused else "Navigator продолжает работу")
            return
        if action is HotkeyAction.SHOW_HUD:
            self._mode = "hud"
            self._apply_window_mode()
            handle = self._window_handle()
            if handle:
                ctypes.windll.user32.ShowWindow(handle, 5)
            return
        if action is HotkeyAction.TOGGLE_TRACE:
            self._show_toast(self._trace_callback() if self._trace_callback else "Запись недоступна")
            return
        if action is HotkeyAction.REPORT_ISSUE:
            accepted = self._report_callback() if self._report_callback else False
            self._show_toast(
                "Собираю diagnostic report…" if accepted else "Diagnostic recorder недоступен"
            )
            return
        if action is HotkeyAction.TOGGLE_VIEW:
            self._hold.cancel()
            self._toggle_view()
            return
        if action is HotkeyAction.TOGGLE_LOCK:
            self._hold.cancel()
            self._toggle_lock()
            return
        if action is HotkeyAction.TOGGLE_DETAILS:
            self._hold.cancel()
            self._toggle_details()
            return
        if action in {HotkeyAction.PREVIOUS_PAGE, HotkeyAction.NEXT_PAGE}:
            if self._mode == "hud" and self._details_open:
                delta = -1 if action is HotkeyAction.PREVIOUS_PAGE else 1
                self._details_page = max(0, self._details_page + delta)
            return
        if self._navigation is None:
            return
        if action is HotkeyAction.COLLECTED_HOLD:
            target_id = navigation.target.id if navigation and navigation.target else None
            if self._hold.begin(target_id, available=bool(navigation and navigation.available)):
                self._show_toast("Удерживайте Num5 или Ctrl+Alt+Space…", 1.2)
            else:
                self._show_toast("Нет подтверждённой цели")
            return
        self._hold.cancel()
        if action is HotkeyAction.NEXT:
            target = self._navigation.next_target()
            self._show_toast(f"Следующая: {target.name}" if target else "Целей нет")
        elif action is HotkeyAction.PREVIOUS:
            target = self._navigation.previous_target()
            self._show_toast(f"Предыдущая: {target.name}" if target else "Целей нет")
        elif action is HotkeyAction.SKIP:
            self._navigation.skip()
            self._show_toast("Цель временно пропущена")
        elif action is HotkeyAction.CYCLE_TARGET_FILTER:
            label = self._navigation.cycle_target_filter()
            self._show_toast(f"Фильтр целей: {label}")
        elif action is HotkeyAction.BLACKLIST_TARGET:
            target = self._navigation.current_target
            self._navigation.blacklist_current()
            self._show_toast(
                f"В blacklist: {target.name}" if target else "Нет цели для blacklist"
            )
        elif action is HotkeyAction.UNDO:
            self._navigation.undo()
            self._show_toast("Последнее действие отменено")

    def _update_hold(self, navigation: NavigationSnapshot | None) -> None:
        target_id = navigation.target.id if navigation and navigation.target else None
        key_down = (
            self._hotkeys.is_action_down(HotkeyAction.COLLECTED_HOLD)
            if self._hotkeys is not None
            else bool(ctypes.windll.user32.GetAsyncKeyState(self.VK_NUMPAD5) & 0x8000)
        )
        if getattr(self, "_mouse_collect", False):
            if self._mouse_still_on_collect():
                key_down = True
            else:
                self._mouse_collect = False
                self._hold.cancel()
        status = self._hold.update(
            key_down=key_down, target_id=target_id,
            available=bool(navigation and navigation.available),
        )
        self._hold_progress = status.progress if status.active else 0.0
        if status.cancelled:
            self._mouse_collect = False
            self._show_toast("Отметка отменена")
        elif status.confirmed and self._navigation is not None:
            self._mouse_collect = False
            from .categories import progress_kind, ProgressKind
            semantics = progress_kind(navigation.target.kind) if navigation and navigation.target else None
            self._navigation.mark_collected()
            self._show_toast("Ориентир посещён · без удалённой отметки" if semantics is ProgressKind.LANDMARK
                             else "Отмечено в текущей вылазке · без sync" if semantics is ProgressKind.OUTING
                             else "Сохранено локально · ожидает sync")

    def _mouse_still_on_collect(self) -> bool:
        if self._locked or self._mode != "hud":
            return False
        try:
            user32 = ctypes.windll.user32
            if not user32.GetAsyncKeyState(0x01) & 0x8000:
                return False
            if int(user32.GetForegroundWindow() or 0) != self._window_handle():
                return False
            cursor = (ctypes.c_long * 2)()
            if not user32.GetCursorPos(cursor):
                return False
            left, top, width, height = cv2.getWindowImageRect(self.window_name)
            if width <= 0 or height <= 0:
                return False
            x = (cursor[0] - left) * self._canvas_size[0] / width
            y = (cursor[1] - top) * self._canvas_size[1] / height
            return any(hit.action is HotkeyAction.COLLECTED_HOLD and hit.contains(x, y) for hit in self._hud_hits)
        except (AttributeError, OSError, cv2.error):
            return False

    def show(
        self,
        snapshot: TrackerSnapshot,
        fps: float,
        paused_reason: str | None = None,
        performance: PerformanceSnapshot | None = None,
    ) -> bool:
        layer_id = snapshot.position.layer_id if snapshot.position is not None else snapshot.map_layer_id
        self._select_layer(self._browsed_layer if self._mode=="map" and self._browsed_layer else layer_id)
        navigation = self._navigation.update(snapshot) if self._navigation else None
        self._last_navigation = navigation
        key = cv2.waitKey(1) & 0xFF
        if self._mode=="map" and self._search_edit:
            if key in (13,27): self._search_edit=False
            elif key==8: self._map_query=self._map_query[:-1]
            elif key==22:
                from .map_browser import clipboard_text
                self._map_query=clipboard_text()
            elif 32<=key<127: self._map_query=(self._map_query+chr(key))[:120]
            self._target_page=0
            key=255
        local_actions = {
            ord("n"): HotkeyAction.NEXT, ord("N"): HotkeyAction.NEXT,
            ord("p"): HotkeyAction.PREVIOUS, ord("P"): HotkeyAction.PREVIOUS,
            ord("s"): HotkeyAction.SKIP, ord("S"): HotkeyAction.SKIP,
            ord("u"): HotkeyAction.UNDO, ord("U"): HotkeyAction.UNDO,
            ord("0"): HotkeyAction.TOGGLE_VIEW, ord("."): HotkeyAction.TOGGLE_LOCK,
            ord("7"): HotkeyAction.TOGGLE_DETAILS,
            ord("1"): HotkeyAction.PREVIOUS_PAGE,
            ord("3"): HotkeyAction.NEXT_PAGE,
            ord("+"): HotkeyAction.REPORT_ISSUE,
            ord("*"): HotkeyAction.TOGGLE_PAUSE,
            ord("/"): HotkeyAction.CYCLE_TARGET_FILTER,
            ord("-"): HotkeyAction.BLACKLIST_TARGET,
        }
        if key in (ord("m"), ord("M")) and self._navigation is not None:
            self._show_toast("Для отметки удерживайте Num5 / Ctrl+Alt+Space или кнопку мыши")
        elif key in local_actions:
            self._dispatch_action(local_actions[key], navigation)
        for action in self._hotkeys.poll() if self._hotkeys is not None else []:
            self._dispatch_action(action, navigation)
        while True:
            try:
                self._dispatch_action(self._external_actions.get_nowait(), navigation)
            except queue.Empty:
                break
        navigation = self._navigation.update(snapshot) if self._navigation else None
        if self._details_open and self._hint_service is not None:
            target = navigation.target if navigation is not None else None
            target_id = target.id if target is not None else None
            if target_id != self._hint_target_id:
                self._hint_target_id = target_id
                self._details_page = 0
            self._hint_service.request(target)
        self._update_hold(navigation)
        navigation = self._navigation.update(snapshot) if self._navigation else None
        if self._mode == "hud":
            canvas = (
                self._render_details_hud(snapshot, navigation, paused_reason, performance)
                if self._details_open else self._render_hud(snapshot, navigation, paused_reason, performance)
            )
        else:
            canvas = self._render_map(snapshot, navigation, fps, paused_reason, performance)
        cv2.imshow(self.window_name, canvas)
        self._canvas_size = (canvas.shape[1], canvas.shape[0])
        try:
            visible = cv2.getWindowProperty(self.window_name, cv2.WND_PROP_VISIBLE) >= 1
        except cv2.error:
            visible = False
        return visible and not self._quit_requested and key not in (27, ord("q"), ord("Q"))

    def _render_hud(
        self,
        snapshot: TrackerSnapshot,
        navigation: NavigationSnapshot | None,
        paused_reason: str | None,
        performance: PerformanceSnapshot | None = None,
    ) -> np.ndarray:
        from .hud_renderer import render_compact
        presentation = build_hud_presentation(snapshot, navigation, self._layer_labels)
        panel, self._hud_hits = render_compact(
            presentation, self.hud_width, self.hud_height,
            paused=bool(paused_reason), locked=getattr(self, "_locked", True),
            hover=getattr(self, "_mouse_hover", None),
            hold=self._hold_progress,
            toast=self._toast if time.monotonic() < self._toast_until else "",
            performance=performance,
        )
        trace_status = self._trace_status() if getattr(self,"_trace_status",None) else ""
        if trace_status.startswith("●"):
            cv2.circle(panel,(8,8),4,(50,50,245),-1)
            cv2.putText(panel,"REC",(16,12),cv2.FONT_HERSHEY_SIMPLEX,.3,(70,70,255),1)
        return panel

    def _progress_status(self) -> dict[str, object]:
        supplier = getattr(self, "_progress_status_supplier", None)
        if supplier is None:
            return {}
        now = time.monotonic()
        if now - getattr(self, "_progress_status_at", 0.0) >= 2.0:
            try:
                self._progress_status_cache = supplier()
            except Exception:
                self._progress_status_cache = {}
            self._progress_status_at = now
        return self._progress_status_cache

    @staticmethod
    def _hint_lines(hint: PoiHintSnapshot, width: int) -> list[str]:
        if hint.hint is None:
            return textwrap.wrap(
                hint.message or "Подсказка отсутствует", width=max(20, width)
            )
        body = hint.hint.content or "У официальной карты нет текстового описания."
        lines: list[str] = []
        for paragraph in body.splitlines() or [body]:
            lines.extend(textwrap.wrap(paragraph, width=max(20, width)) or [""])
        if hint.hint.links:
            lines.append("")
            lines.append(f"Ссылки: {len(hint.hint.links)}")
            lines.extend(urllib_domain(url) for url in hint.hint.links)
        if hint.hint.video_url:
            lines.append("Видео: доступно на HoYoLAB")
        return lines

    def _render_details_hud(
        self,
        snapshot: TrackerSnapshot,
        navigation: NavigationSnapshot | None,
        paused_reason: str | None,
        performance: PerformanceSnapshot | None = None,
    ) -> np.ndarray:
        panel = np.full(
            (self.details_height, self.details_width, 3), (22, 24, 15), np.uint8
        )
        compact = self._render_hud(snapshot, navigation, paused_reason, performance)
        header_width = min(self.details_width, compact.shape[1])
        header_height = min(self.hud_height, compact.shape[0])
        panel[:header_height, :header_width] = compact[:header_height, :header_width]
        hint = self._hint_service.snapshot if self._hint_service is not None else PoiHintSnapshot(None, HintState.UNAVAILABLE)
        image_x = 8
        image_y = self.hud_height + 8
        image_w = self.details_width - 16
        image_h = min(245, max(150, round(image_w * 0.68)))
        image = None
        if hint.image_path is not None:
            if hint.image_path != self._hint_image_path:
                self._hint_image_path = hint.image_path
                self._hint_image = read_image(str(hint.image_path), cv2.IMREAD_COLOR)
                self._image_viewport = None
            image = self._hint_image
        if image is not None and image.size:
            if self._image_viewport is None:
                self._image_viewport = MapViewport.fit(image_w,image_h,image.shape[1],image.shape[0])
            view = self._image_viewport
            tx,ty = view.to_screen(0,0)
            panel[image_y:image_y+image_h,image_x:image_x+image_w] = cv2.warpAffine(
                image,np.float64([[view.scale,0,tx],[0,view.scale,ty]]),(image_w,image_h),borderValue=(22,24,15))
        else:
            cv2.rectangle(
                panel, (image_x, image_y),
                (image_x + image_w, image_y + image_h), (52, 56, 62), 1,
            )
            self._put_unicode_text(
                panel, "Изображения нет",
                (image_x + max(15, (image_w - 120) // 2), image_y + image_h // 2 - 8),
                (145, 150, 155),
            )

        text_x = 14
        self._image_rect = (image_x,image_y,image_x+image_w,image_y+image_h)
        gallery_y = image_y+image_h+2
        count = len(hint.hint.images) if hint.hint else 0
        self._detail_hits = [(8, gallery_y, 48, gallery_y+22, "image_previous"),
                             (48,gallery_y,92,gallery_y+22,"image_next"),
                             (92,gallery_y,175,gallery_y+22,"image_reset"),
                             (175,gallery_y,self.details_width-8,gallery_y+22,"official")]
        for x,label in ((17,"‹"),(57,"›"),(98,"Сброс"),(180,"HoYoLAB")):
            self._put_unicode_text(panel,label,(x,gallery_y),(170,220,180))
        text_y = image_y + image_h + 60
        line_width = max(28, (self.details_width - 28) // 8)
        self._put_unicode_text(
            panel, f"{hint.image_index+1 if count else 0}/{count} · " + (hint.message or hint.state.value),
            (text_x, image_y + image_h + 29), (175, 180, 185),
        )
        lines = self._hint_lines(hint, line_width)
        per_page = max(1, (self.details_height - text_y - 35) // 22)
        pages = max(1, (len(lines) + per_page - 1) // per_page)
        self._details_page = min(self._details_page, pages - 1)
        shown = lines[self._details_page * per_page:(self._details_page + 1) * per_page]
        for index, line in enumerate(shown):
            self._put_unicode_text(
                panel, line, (text_x, text_y + index * 22), (220, 222, 224)
            )
            if hint.hint:
                link=next((url for url in hint.hint.links if urllib_domain(url)==line),None)
                if line.startswith("Видео:"): link=hint.hint.video_url
                if link: self._detail_hits.append((text_x,text_y+index*22,self.details_width-10,text_y+(index+1)*22,"link:"+link))
        footer = f"‹ Назад       {self._details_page + 1} / {pages}       Вперёд ›"
        cv2.rectangle(panel, (8, self.details_height - 30), (self.details_width - 8, self.details_height - 5), (32, 40, 26), -1)
        self._put_unicode_text(panel, footer, (16, self.details_height - 27), (175, 200, 183))
        from .hud_renderer import HudButton
        self._hud_hits = tuple(self._hud_hits) + (
            HudButton(HotkeyAction.PREVIOUS_PAGE, (8, self.details_height - 30, self.details_width // 2, self.details_height - 5), "", "", self._details_page > 0),
            HudButton(HotkeyAction.NEXT_PAGE, (self.details_width // 2, self.details_height - 30, self.details_width - 8, self.details_height - 5), "", "", self._details_page < pages - 1),
        )
        if time.monotonic() < self._toast_until:
            cv2.rectangle(panel, (7, self.details_height - 62), (self.details_width - 7, self.details_height - 34), (42, 45, 50), -1)
            self._put_unicode_text(panel, self._toast[:65], (14, self.details_height - 58), (245, 220, 170))
        return panel

    def _render_map(
        self,
        snapshot: TrackerSnapshot,
        navigation: NavigationSnapshot | None,
        fps: float,
        paused_reason: str | None,
        performance: PerformanceSnapshot | None = None,
    ) -> np.ndarray:
        same_layer=bool(navigation and navigation.position and navigation.position.layer_id==self._active_layer_id)
        if self._viewport.following and same_layer and navigation.available:
            self._viewport = self._viewport.centered(navigation.position.x, navigation.position.y, follow=True)
        if self._viewport_cache_key != self._viewport:
            view = self._viewport
            tx, ty = view.to_screen(0, 0)
            self._viewport_background = cv2.warpAffine(
                self._layers[self._active_layer_id], np.float64([[view.scale,0,tx],[0,view.scale,ty]]),
                (view.width, view.height), flags=cv2.INTER_LINEAR, borderValue=(18,18,18))
            self._viewport_cache_key = view
        canvas = cv2.copyMakeBorder(self._viewport_background, 0, 142, 0, 0, cv2.BORDER_CONSTANT, value=(18, 18, 18))
        map_canvas=canvas[:self.base.shape[0],:self.base.shape[1]]
        colors = {TrackerState.TRACKING: (80, 220, 80), TrackerState.ACQUIRING: (40, 210, 240), TrackerState.RELOCATING: (30, 150, 255), TrackerState.LOST: (60, 60, 230)}
        color = (150, 150, 150) if paused_reason or snapshot.stale else colors[snapshot.state]
        layer_poi_count = 0
        if self._poi_catalog is not None:
            layer_pois = [row.poi for row in self._map_rows()]
            layer_poi_count = len(layer_pois)
            from .map_browser import cluster_markers
            self._clusters=cluster_markers(layer_pois,self._viewport,26 if self._viewport.scale<1 else 8)
            for cluster in self._clusters:
                point=(round(cluster.x),round(cluster.y))
                cv2.circle(map_canvas,point,8 if len(cluster.ids)>1 else 3,(75,210,155),-1)
                if len(cluster.ids)>1: cv2.putText(map_canvas,str(len(cluster.ids)),(point[0]+7,point[1]),cv2.FONT_HERSHEY_SIMPLEX,.35,(230,240,230),1)
            plan=self._navigation.active_plan if self._navigation else None
            if plan and plan.layer_id==self._active_layer_id:
                by_id={p.id:p for p in self._poi_catalog.pois}
                for a,b in zip(plan.poi_ids,plan.poi_ids[1:]):
                    if a not in by_id or b not in by_id: continue
                    p1=np.array(self._viewport.to_screen(by_id[a].x,by_id[a].y)); p2=np.array(self._viewport.to_screen(by_id[b].x,by_id[b].y))
                    visible,c1,c2=cv2.clipLine((0,0,self.base.shape[1],self.base.shape[0]),tuple(map(round,p1)),tuple(map(round,p2)))
                    if visible:
                        count=max(1,int(np.linalg.norm(np.array(c2)-c1)/8))
                        for point in np.linspace(c1,c2,count): cv2.circle(map_canvas,tuple(map(round,point)),1,(100,195,245),-1)
                self._put_unicode_text(canvas,"Порядок сбора · не пеший маршрут",(10,30),(100,195,245))
        target_point = None
        if navigation is not None and navigation.target is not None and navigation.target.layer_id==self._active_layer_id:
            poi = navigation.target
            target_point = tuple(round(v) for v in self._viewport.to_screen(poi.x, poi.y))
            cv2.circle(map_canvas, target_point, 9, (255, 255, 255) if navigation.available else (150, 150, 150), 2)
        if same_layer and snapshot.x_px is not None and snapshot.y_px is not None:
            point = tuple(round(v) for v in self._viewport.to_screen(snapshot.x_px, snapshot.y_px))
            cv2.circle(map_canvas, point, 9, (10, 10, 10), 3)
            cv2.circle(map_canvas, point, 7, color, -1)
            if target_point is not None:
                cv2.line(map_canvas, point, target_point, (255, 235, 180) if navigation and navigation.available else (150, 150, 150), 2, cv2.LINE_AA)
        y = self.base.shape[0]
        trace_status = self._trace_status() if getattr(self,"_trace_status",None) else ""
        if trace_status:
            self._put_unicode_text(canvas,trace_status,(10,8),(100,180,255))
        self._put_unicode_text(canvas, "Сброс       К игроку       К цели       Следить: " + ("да" if self._viewport.following else "нет") + "      Запись", (10,y+116), (180,220,185))
        state = "PAUSED" if paused_reason else snapshot.state.value
        perf = "" if performance is None else f"  CV={performance.processing_ms or 0:.0f}ms/{performance.search_mode}/{performance.mode}"
        cv2.putText(canvas, f"{state}  confidence={snapshot.confidence:.2f}  HUD={fps:.1f}Hz{perf}", (12, y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
        label = self._layer_labels.get(snapshot.map_layer_id or "surface", snapshot.map_layer_id or "surface")
        age=f"{max(0,time.perf_counter()-snapshot.position.timestamp):.1f} с" if snapshot.position else "нет позиции"
        self._put_unicode_text(canvas, f"Слой: {label[:40]} · возраст: {age}", (12, y + 31), (210, 210, 210))
        target_text, controls = self._navigation_text(navigation, layer_poi_count)
        self._put_unicode_text(canvas, target_text, (12, y + 55), (210, 210, 210))
        cv2.putText(canvas, controls, (12, y + 90), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (190, 190, 190), 1, cv2.LINE_AA)
        if navigation is not None and navigation.bearing_degrees is not None:
            self._draw_direction_arrow(canvas, navigation, y)
        if time.monotonic() < self._toast_until:
            self._put_unicode_text(canvas, self._toast, (12, y + 73), (245, 220, 170))
        if self._navigation is not None:
            rows = self._map_rows()
            if self._overlap_ids and self._target_section == "available":
                rows = tuple(row for row in rows if row.poi.id in self._overlap_ids)
            panel, self._target_hits, self._target_page = render_panel(
                rows, self._target_section, self._target_page, canvas.shape[0],
                self._unicode_font, locked=self._locked, overlap=bool(self._overlap_ids),
                browser={"query":self._map_query+(" |" if self._search_edit else ""),"layer":self._layer_labels.get(self._active_layer_id,self._active_layer_id),"kind":self._map_kind or "все"})
            canvas = cv2.copyMakeBorder(canvas, 0, panel.shape[0] - canvas.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(18, 18, 18))
            canvas = np.concatenate((canvas, panel), axis=1)
        return canvas

    def _map_rows(self):
        if self._navigation is None: return ()
        from .map_browser import filter_rows
        rows=self._navigation.browse_candidates(self._active_layer_id,self._target_section)
        # Reuse the fixed navigation order when viewing the player's candidates.
        if not self._browsed_layer and self._target_section=="available" and self._map_kind is None and not self._map_query:
            candidates=self._navigation.candidates()
            if candidates: rows=candidates
        return filter_rows(rows,self._map_query,self._map_kind)

    def _plan_from_map(self, ids, append=False):
        nav=self._navigation
        if nav is None or nav.plan_repository is None:
            self._show_toast("Планы требуют SQLite"); return
        from .collection_plans import CollectionPlan
        from uuid import uuid4
        try:
            points=[p for p in nav.catalog.pois if p.id in ids]
            if not points: return
            first=points[0]
            old=nav.active_plan if append else None
            ordered=list(old.poi_ids) if old else []
            for identifier in ids:
                if identifier not in ordered: ordered.append(identifier)
            plan=replace(old,poi_ids=tuple(ordered)) if old else CollectionPlan(uuid4().hex,"Сбор · "+self._active_layer_id,first.region_id,first.layer_id,first.coordinate_space,tuple(ordered))
            nav.plan_repository.save(plan)
            nav.activate_plan(plan.plan_id)
            self._show_toast("План сохранён · редактирование в launcher → Сбор")
        except (ValueError,KeyError,OSError): self._show_toast("План должен содержать точки одного слоя")

    def _on_mouse(self, event, x, y, flags, param=None):
        if self._mode == "hud":
            self._mouse_hover = (x, y)
            if self._locked:
                self._mouse_collect = False
                return
            if hasattr(self,"_image_rect") and self._details_open:
                left,top,right,bottom = self._image_rect
                inside_image = left <= x < right and top <= y < bottom
                if event == cv2.EVENT_MOUSEWHEEL:
                    delta = (flags >> 16) & 0xffff
                    if delta >= 32768: delta -= 65536
                    if inside_image and self._image_viewport:
                        self._image_viewport = self._image_viewport.zoom(1.2 if delta>0 else 1/1.2,x-left,y-top)
                    else:
                        self._details_page = max(0,self._details_page + (-1 if delta>0 else 1))
                    return
                if event == cv2.EVENT_LBUTTONDOWN and inside_image:
                    self._image_drag = (x,y)
                    return
                if event == cv2.EVENT_MOUSEMOVE and self._image_drag and self._image_viewport:
                    self._image_viewport = self._image_viewport.pan(x-self._image_drag[0],y-self._image_drag[1])
                    self._image_drag = (x,y)
                    return
                if event == cv2.EVENT_LBUTTONUP:
                    self._image_drag = None
                    for l,t,r,b,action in self._detail_hits:
                        if l<=x<r and t<=y<b:
                            hint = self._hint_service.snapshot if self._hint_service else None
                            if action == "image_reset": self._image_viewport = None
                            elif action.startswith("link:https://"):
                                import webbrowser
                                webbrowser.open(action[5:])
                            elif hint and action.startswith("image_"):
                                self._hint_service.select_image(hint.image_index+(-1 if action=="image_previous" else 1))
                            elif hint and hint.poi_id and action == "official":
                                from .poi_guidance import official_point_id
                                import webbrowser
                                identifier = official_point_id(hint.poi_id)
                                if identifier: webbrowser.open(f"https://act.hoyolab.com/ys/app/interactive-map/index.html?lang=ru-ru#/map/2?point_id={identifier}")
                            return
            hit = next((hit for hit in self._hud_hits if hit.enabled and hit.contains(x, y)), None)
            if event == cv2.EVENT_MOUSEMOVE and self._mouse_collect:
                if hit is None or hit.action is not HotkeyAction.COLLECTED_HOLD:
                    self._mouse_collect = False
                    self._hold.cancel()
            if event == cv2.EVENT_LBUTTONDOWN:
                self._mouse_pressed_action = hit.action if hit else None
                if hit and hit.action is HotkeyAction.COLLECTED_HOLD:
                    nav = self._last_navigation
                    self._mouse_collect = self._hold.begin(nav.target.id if nav and nav.target else None, available=bool(nav and nav.available))
            elif event == cv2.EVENT_LBUTTONUP:
                if self._mouse_collect:
                    self._mouse_collect = False
                    self._hold.cancel()
                if hit and hit.action == self._mouse_pressed_action and hit.action is not HotkeyAction.COLLECTED_HOLD:
                    self._dispatch_action(hit.action, self._last_navigation)
                self._mouse_pressed_action = None
            return
        if self._mode != "map" or self._locked or self._navigation is None:
            return
        inside = 0 <= x < self.base.shape[1] and 0 <= y < self.base.shape[0]
        if event == cv2.EVENT_MOUSEWHEEL and inside:
            delta = (flags >> 16) & 0xffff
            if delta >= 32768: delta -= 65536
            self._viewport = self._viewport.zoom(1.2 if delta > 0 else 1/1.2,x,y)
            return
        if event == cv2.EVENT_LBUTTONDOWN and inside:
            self._map_drag = (x,y); self._map_dragged = False
            return
        if event == cv2.EVENT_MOUSEMOVE and self._map_drag is not None:
            dx,dy = x-self._map_drag[0],y-self._map_drag[1]
            if self._map_dragged or abs(dx)+abs(dy) > 4:
                self._viewport = self._viewport.pan(dx,dy)
                self._map_drag = (x,y); self._map_dragged = True
            return
        if event != cv2.EVENT_LBUTTONUP:
            return
        self._map_drag = None
        if self._map_dragged:
            self._map_dragged = False
            return
        if x < self.base.shape[1] and y >= self.base.shape[0]+112:
            nav = self._last_navigation
            if x < 90:
                image = self._layers[self._active_layer_id]
                self._viewport = MapViewport.fit(self.base.shape[1],self.base.shape[0],image.shape[1],image.shape[0])
            elif x < 190 and nav and nav.available and nav.position:
                self._browsed_layer=None
                self._select_layer(nav.position.layer_id)
                self._viewport = self._viewport.centered(nav.position.x,nav.position.y)
            elif x < 280 and nav and nav.target:
                self._browsed_layer=nav.target.layer_id
                self._select_layer(nav.target.layer_id)
                self._viewport = self._viewport.centered(nav.target.x,nav.target.y)
            elif x >= 430:
                self._dispatch_action(HotkeyAction.TOGGLE_TRACE,nav)
            elif x >= 280:
                self._viewport = replace(self._viewport,following=not self._viewport.following)
            return
        if x < self.base.shape[1] and y < self.base.shape[0]:
            cluster=next((c for c in self._clusters if (c.x-x)**2+(c.y-y)**2<=144),None)
            matches=cluster.ids if cluster else ()
            if matches and flags & cv2.EVENT_FLAG_SHIFTKEY:
                self._plan_from_map(matches,append=True)
                return
            if len(matches) == 1:
                self._navigation.select_target(matches[0])
            elif matches:
                self._viewport=self._viewport.zoom(1.7,x,y)
                self._overlap_ids = matches
                self._target_section, self._target_page = "available", 0
            return
        for hit in self._target_hits:
            if not hit.contains(x - self.base.shape[1], y):
                continue
            if hit.action == "section":
                self._target_section, self._target_page, self._overlap_ids = hit.value, 0, ()
            elif hit.action == "page":
                self._target_page = max(0, self._target_page + int(hit.value))
            elif hit.action == "refresh":
                self._navigation.refresh_candidates()
                self._overlap_ids, self._target_page = (), 0
            elif hit.action == "select":
                if flags & cv2.EVENT_FLAG_SHIFTKEY: self._plan_from_map((hit.value,),append=True)
                else: self._navigation.select_target(hit.value)
            elif hit.action == "restore":
                if self._navigation.restore_target(hit.value, self._target_section):
                    self._show_toast("Точка восстановлена")
            elif hit.action == "search":
                self._search_edit=True
                self._show_toast("Поиск: текст/ID, Ctrl+V — вставить, Enter — готово")
            elif hit.action == "layer":
                layers=list(self._layers)
                self._browsed_layer=layers[(layers.index(self._active_layer_id)+1)%len(layers)]
                self._select_layer(self._browsed_layer)
                self._target_page=0; self._overlap_ids=()
                self._show_toast("Просмотр другого слоя · положение игрока не изменено")
            elif hit.action == "kind":
                kinds=[None]+sorted({p.kind for p in self._poi_catalog.pois})
                self._map_kind=kinds[(kinds.index(self._map_kind)+1)%len(kinds)]
                self._target_page=0; self._overlap_ids=()
            elif hit.action == "plan":
                self._plan_from_map(tuple(row.poi.id for row in self._map_rows()))
            elif hit.action == "free":
                if self._navigation.plan_repository: self._navigation.activate_plan(None)
                self._browsed_layer=None; self._map_kind=None; self._map_query=""; self._overlap_ids=()
            break

    @staticmethod
    def _navigation_text(navigation: NavigationSnapshot | None, layer_poi_count: int) -> tuple[str, str]:
        controls = "NumPad or Ctrl+Alt: Left/Right target  Down skip  hold Space collected  Up undo  M HUD  Q quit"
        if navigation is None or navigation.target is None:
            return f"target=-  visible_pois={layer_poi_count}", controls
        distance = "frozen" if not navigation.available else "uncalibrated" if navigation.distance_m is None else f"≈{navigation.distance_m:.0f} м"
        return f"target={navigation.target.name} [{navigation.target.kind}]  distance={distance}", controls

    def _put_unicode_text(self, canvas: np.ndarray, text: str, point: tuple[int, int], color: tuple[int, int, int], *, large: bool = False) -> None:
        image = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
        ImageDraw.Draw(image).text(point, text, font=self._unicode_font_large if large else self._unicode_font, fill=(color[2], color[1], color[0]))
        np.copyto(canvas, cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR))

    @staticmethod
    def _draw_hud_arrow(canvas: np.ndarray, bearing_degrees: float, color: tuple[int, int, int]) -> None:
        center, angle = (canvas.shape[1] - 45, 63), radians(bearing_degrees)
        tip = (round(center[0] + 28 * sin(angle)), round(center[1] - 28 * cos(angle)))
        cv2.circle(canvas, center, 33, (70, 74, 80), 1)
        cv2.arrowedLine(canvas, center, tip, color, 3, cv2.LINE_AA, tipLength=0.35)
        cv2.putText(canvas, "N", (center[0] - 5, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (170, 175, 180), 1, cv2.LINE_AA)

    @staticmethod
    def _draw_direction_arrow(canvas: np.ndarray, navigation: NavigationSnapshot, panel_y: int) -> None:
        assert navigation.bearing_degrees is not None
        center, angle = (canvas.shape[1] - 34, panel_y + 56), radians(navigation.bearing_degrees)
        tip = (round(center[0] + 25 * sin(angle)), round(center[1] - 25 * cos(angle)))
        color = (255, 235, 180) if navigation.available else (135, 135, 135)
        cv2.arrowedLine(canvas, center, tip, color, 3, cv2.LINE_AA, tipLength=0.35)
        cv2.putText(canvas, "N", (center[0] - 5, panel_y + 17), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1, cv2.LINE_AA)

    def close(self) -> None:
        self._hold.cancel()
        self._tray.close()
        if self._hotkeys is not None:
            self._hotkeys.close()
        if self._locked and self._mode == "hud":
            geometry = self._window_geometry()
            if geometry is not None:
                self._state_store.save(WindowGeometry(geometry.x, geometry.y, self.hud_width, self.hud_height))
        if self._hint_service is not None:
            self._hint_service.close()
        try:
            cv2.destroyWindow(self.window_name)
        except cv2.error:
            pass


def urllib_domain(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc or url
