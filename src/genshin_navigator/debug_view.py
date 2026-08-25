from __future__ import annotations

import ctypes
import time
from math import radians, sin
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .hotkeys import CollectedHoldController, GlobalHotkeyManager, HotkeyAction
from .hud import HudStateStore, WindowGeometry, build_hud_presentation
from .navigation import NavigationController, NavigationSnapshot
from .poi import PointOfInterest, PoiRepository, ProgressRepository
from .tracker import TrackerSnapshot, TrackerState


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
        hotkey_virtual_keys: dict[HotkeyAction, int] | None = None,
        hotkey_manager: GlobalHotkeyManager | None = None,
    ):
        if atlas is None or atlas.size == 0:
            raise ValueError("Debug atlas is empty")
        self.window_name = "Genshin Navigator"
        self.max_width = max_width
        self.max_height = max_height
        self.hud_width = hud_width
        self.hud_height = hud_height
        self._mode = default_view
        self._locked = True
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

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def locked(self) -> bool:
        return self._locked

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
        if self._mode != "hud":
            self._show_toast("Переключитесь в HUD через Num0")
            return
        if self._locked:
            self._set_locked_style(False)
            self._show_toast("HUD разблокирован — перетащите окно")
        else:
            geometry = self._window_geometry()
            self._set_locked_style(True)
            if geometry is not None:
                self._state_store.save(geometry)
            self._show_toast("Положение HUD сохранено")

    def _toggle_view(self) -> None:
        self._mode = "map" if self._mode == "hud" else "hud"
        self._apply_window_mode()
        self._show_toast("Полная карта" if self._mode == "map" else "Компактный HUD")

    def _apply_window_mode(self) -> None:
        if self._mode == "hud":
            cv2.resizeWindow(self.window_name, self.hud_width, self.hud_height)
        elif self.base.size:
            cv2.resizeWindow(self.window_name, self.base.shape[1], self.base.shape[0] + 112)

    def _select_layer(self, layer_id: str | None) -> None:
        selected = layer_id if layer_id in self._layers else "surface"
        if selected == self._active_layer_id:
            return
        image = self._layers[selected]
        source_height, source_width = image.shape[:2]
        self.scale = min(self.max_width / source_width, self.max_height / source_height, 1.0)
        size = (max(1, round(source_width * self.scale)), max(1, round(source_height * self.scale)))
        self.base = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        self._active_layer_id = selected
        if self._mode == "map":
            self._apply_window_mode()

    @staticmethod
    def _poi_color(poi: PointOfInterest) -> tuple[int, int, int]:
        return {"chest": (40, 180, 255), "hydroculus": (255, 210, 60), "waypoint": (255, 120, 60)}.get(poi.kind, (210, 210, 210))

    def _show_toast(self, message: str, duration: float = 2.5) -> None:
        self._toast, self._toast_until = message, time.monotonic() + duration

    def _dispatch_action(self, action: HotkeyAction, navigation: NavigationSnapshot | None) -> None:
        if action is HotkeyAction.TOGGLE_VIEW:
            self._hold.cancel()
            self._toggle_view()
            return
        if action is HotkeyAction.TOGGLE_LOCK:
            self._hold.cancel()
            self._toggle_lock()
            return
        if self._navigation is None:
            return
        if action is HotkeyAction.COLLECTED_HOLD:
            target_id = navigation.target.id if navigation and navigation.target else None
            if self._hold.begin(target_id, available=bool(navigation and navigation.available)):
                self._show_toast("Удерживайте Num5…", 1.2)
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
        elif action is HotkeyAction.UNDO:
            self._navigation.undo()
            self._show_toast("Последнее действие отменено")

    def _update_hold(self, navigation: NavigationSnapshot | None) -> None:
        target_id = navigation.target.id if navigation and navigation.target else None
        key_down = bool(ctypes.windll.user32.GetAsyncKeyState(self.VK_NUMPAD5) & 0x8000)
        status = self._hold.update(
            key_down=key_down, target_id=target_id,
            available=bool(navigation and navigation.available),
        )
        self._hold_progress = status.progress if status.active else 0.0
        if status.cancelled:
            self._show_toast("Отметка отменена")
        elif status.confirmed and self._navigation is not None:
            self._navigation.mark_collected()
            self._show_toast("Сундук отмечен собранным")

    def show(self, snapshot: TrackerSnapshot, fps: float, paused_reason: str | None = None) -> bool:
        layer_id = snapshot.position.layer_id if snapshot.position is not None else snapshot.map_layer_id
        self._select_layer(layer_id)
        navigation = self._navigation.update(snapshot) if self._navigation else None
        key = cv2.waitKey(1) & 0xFF
        local_actions = {
            ord("n"): HotkeyAction.NEXT, ord("N"): HotkeyAction.NEXT,
            ord("p"): HotkeyAction.PREVIOUS, ord("P"): HotkeyAction.PREVIOUS,
            ord("s"): HotkeyAction.SKIP, ord("S"): HotkeyAction.SKIP,
            ord("u"): HotkeyAction.UNDO, ord("U"): HotkeyAction.UNDO,
            ord("0"): HotkeyAction.TOGGLE_VIEW, ord("."): HotkeyAction.TOGGLE_LOCK,
        }
        if key in (ord("m"), ord("M")) and self._navigation is not None:
            self._navigation.mark_collected()
            self._show_toast("Сундук отмечен собранным")
        elif key in local_actions:
            self._dispatch_action(local_actions[key], navigation)
        for action in self._hotkeys.poll() if self._hotkeys is not None else []:
            self._dispatch_action(action, navigation)
        navigation = self._navigation.update(snapshot) if self._navigation else None
        self._update_hold(navigation)
        navigation = self._navigation.update(snapshot) if self._navigation else None
        canvas = self._render_hud(snapshot, navigation, paused_reason) if self._mode == "hud" else self._render_map(snapshot, navigation, fps, paused_reason)
        cv2.imshow(self.window_name, canvas)
        try:
            visible = cv2.getWindowProperty(self.window_name, cv2.WND_PROP_VISIBLE) >= 1
        except cv2.error:
            visible = False
        return visible and key not in (27, ord("q"), ord("Q"))

    def _render_hud(self, snapshot: TrackerSnapshot, navigation: NavigationSnapshot | None, paused_reason: str | None) -> np.ndarray:
        panel = np.full((self.hud_height, self.hud_width, 3), (24, 27, 31), np.uint8)
        presentation = build_hud_presentation(snapshot, navigation, self._layer_labels)
        accent = (85, 220, 110) if presentation.available and not paused_reason else (145, 145, 145)
        cv2.rectangle(panel, (0, 0), (5, self.hud_height), accent, -1)
        self._put_unicode_text(panel, presentation.target[:38], (16, 12), (238, 238, 238), large=True)
        self._put_unicode_text(panel, presentation.distance, (16, 44), accent)
        self._put_unicode_text(panel, presentation.layer[:43], (16, 70), (190, 195, 200))
        self._put_unicode_text(panel, "ПАУЗА" if paused_reason else presentation.state, (16, 96), accent)
        self._put_unicode_text(panel, "4/6 цель  2 skip  5 держать  8 undo  0 карта  . move", (16, self.hud_height - 23), (145, 150, 155))
        if presentation.bearing_degrees is not None:
            self._draw_hud_arrow(panel, presentation.bearing_degrees, accent)
        if self._hold_progress > 0:
            width = round((self.hud_width - 12) * self._hold_progress)
            cv2.rectangle(panel, (6, self.hud_height - 5), (6 + width, self.hud_height - 2), (50, 190, 255), -1)
        if time.monotonic() < self._toast_until:
            cv2.rectangle(panel, (7, 91), (self.hud_width - 7, 119), (42, 45, 50), -1)
            self._put_unicode_text(panel, self._toast[:45], (14, 95), (245, 220, 170))
        return panel

    def _render_map(self, snapshot: TrackerSnapshot, navigation: NavigationSnapshot | None, fps: float, paused_reason: str | None) -> np.ndarray:
        canvas = cv2.copyMakeBorder(self.base, 0, 112, 0, 0, cv2.BORDER_CONSTANT, value=(18, 18, 18))
        colors = {TrackerState.TRACKING: (80, 220, 80), TrackerState.ACQUIRING: (40, 210, 240), TrackerState.RELOCATING: (30, 150, 255), TrackerState.LOST: (60, 60, 230)}
        color = (150, 150, 150) if paused_reason else colors[snapshot.state]
        layer_poi_count = 0
        if self._poi_catalog is not None and snapshot.position is not None:
            layer_pois = [poi for poi in self._poi_catalog.on_layer(snapshot.position) if self._poi_kinds is None or poi.kind in self._poi_kinds if self._poi_progress is None or poi.id not in self._poi_progress.collected_ids]
            layer_poi_count = len(layer_pois)
            for poi in layer_pois:
                cv2.circle(canvas, (round(poi.x * self.scale), round(poi.y * self.scale)), 2, self._poi_color(poi), -1)
        target_point = None
        if navigation is not None and navigation.target is not None:
            poi = navigation.target
            target_point = (round(poi.x * self.scale), round(poi.y * self.scale))
            cv2.circle(canvas, target_point, 9, (255, 255, 255) if navigation.available else (150, 150, 150), 2)
        if snapshot.x_px is not None and snapshot.y_px is not None:
            point = (round(snapshot.x_px * self.scale), round(snapshot.y_px * self.scale))
            cv2.circle(canvas, point, 9, (10, 10, 10), 3)
            cv2.circle(canvas, point, 7, color, -1)
            if target_point is not None:
                cv2.line(canvas, point, target_point, (255, 235, 180) if navigation and navigation.available else (150, 150, 150), 2, cv2.LINE_AA)
        y = self.base.shape[0]
        state = "PAUSED" if paused_reason else snapshot.state.value
        cv2.putText(canvas, f"{state}  confidence={snapshot.confidence:.2f}  fps={fps:.1f}", (12, y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
        label = self._layer_labels.get(snapshot.map_layer_id or "surface", snapshot.map_layer_id or "surface")
        self._put_unicode_text(canvas, f"Слой: {label}", (12, y + 31), (210, 210, 210))
        target_text, controls = self._navigation_text(navigation, layer_poi_count)
        self._put_unicode_text(canvas, target_text, (12, y + 55), (210, 210, 210))
        cv2.putText(canvas, controls, (12, y + 90), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (190, 190, 190), 1, cv2.LINE_AA)
        if navigation is not None and navigation.bearing_degrees is not None:
            self._draw_direction_arrow(canvas, navigation, y)
        if time.monotonic() < self._toast_until:
            self._put_unicode_text(canvas, self._toast, (12, y + 73), (245, 220, 170))
        return canvas

    @staticmethod
    def _navigation_text(navigation: NavigationSnapshot | None, layer_poi_count: int) -> tuple[str, str]:
        controls = "Num4/6 prev/next  Num2 skip  hold Num5 collected  Num8 undo  Num0 HUD/map"
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
        if self._hotkeys is not None:
            self._hotkeys.close()
        if self._locked and self._mode == "hud":
            geometry = self._window_geometry()
            if geometry is not None:
                self._state_store.save(geometry)
        try:
            cv2.destroyWindow(self.window_name)
        except cv2.error:
            pass
