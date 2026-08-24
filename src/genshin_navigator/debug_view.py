from __future__ import annotations

import ctypes
from math import cos, radians, sin
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .navigation import NavigationController, NavigationSnapshot
from .poi import PoiCatalog, PoiProgress, PointOfInterest
from .tracker import TrackerSnapshot, TrackerState


class DebugMapView:
    _NUMPAD_ACTIONS = {
        "previous": 0x64,  # VK_NUMPAD4
        "next": 0x66,  # VK_NUMPAD6
        "skip": 0x62,  # VK_NUMPAD2
        "collected": 0x65,  # VK_NUMPAD5
        "undo": 0x68,  # VK_NUMPAD8
    }

    def __init__(
        self,
        atlas: np.ndarray,
        layer_maps: dict[str, np.ndarray] | None = None,
        poi_catalog: PoiCatalog | None = None,
        poi_kinds: set[str] | None = None,
        poi_target_kinds: set[str] | None = None,
        poi_progress: PoiProgress | None = None,
        navigation: NavigationController | None = None,
        max_width: int = 1100,
        max_height: int = 720,
    ):
        if atlas is None or atlas.size == 0:
            raise ValueError("Debug atlas is empty")
        self.window_name = "Genshin Navigator - Debug Map"
        self.max_width = max_width
        self.max_height = max_height
        self._layers = {"surface": atlas}
        self._layers.update(layer_maps or {})
        self._poi_catalog = poi_catalog
        self._poi_kinds = poi_kinds
        self._poi_target_kinds = poi_target_kinds
        self._poi_progress = poi_progress
        self._navigation = navigation
        self._numpad_was_down = {
            action: False for action in self._NUMPAD_ACTIONS
        }
        self._active_layer_id = ""
        font_path = Path("C:/Windows/Fonts/arial.ttf")
        self._unicode_font = (
            ImageFont.truetype(str(font_path), 15)
            if font_path.exists()
            else ImageFont.load_default()
        )
        self.base = np.empty((0, 0, 3), dtype=np.uint8)
        self.scale = 1.0
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        try:
            cv2.setWindowProperty(
                self.window_name, cv2.WND_PROP_TOPMOST, 1
            )
            cv2.moveWindow(self.window_name, 800, 20)
        except cv2.error:
            pass
        self._select_layer("surface")

    def _select_layer(self, layer_id: str | None) -> None:
        selected = layer_id if layer_id in self._layers else "surface"
        if selected == self._active_layer_id:
            return
        image = self._layers[selected]
        source_height, source_width = image.shape[:2]
        self.scale = min(
            self.max_width / source_width,
            self.max_height / source_height,
            1.0,
        )
        size = (
            max(1, round(source_width * self.scale)),
            max(1, round(source_height * self.scale)),
        )
        self.base = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        self._active_layer_id = selected
        cv2.resizeWindow(self.window_name, size[0], size[1] + 112)

    @staticmethod
    def _poi_color(poi: PointOfInterest) -> tuple[int, int, int]:
        return {
            "chest": (40, 180, 255),
            "hydroculus": (255, 210, 60),
            "waypoint": (255, 120, 60),
        }.get(poi.kind, (210, 210, 210))

    def _display_position(
        self, x_px: float | None, y_px: float | None
    ) -> tuple[float, float] | None:
        if x_px is None or y_px is None:
            return None
        return float(x_px), float(y_px)

    def show(
        self,
        snapshot: TrackerSnapshot,
        fps: float,
        paused_reason: str | None = None,
    ) -> bool:
        layer_id = snapshot.position.layer_id if snapshot.position is not None else snapshot.map_layer_id
        self._select_layer(layer_id)
        canvas = cv2.copyMakeBorder(self.base, 0, 112, 0, 0, cv2.BORDER_CONSTANT, value=(18, 18, 18))
        colors = {
            TrackerState.TRACKING: (80, 220, 80),
            TrackerState.ACQUIRING: (40, 210, 240),
            TrackerState.RELOCATING: (30, 150, 255),
            TrackerState.LOST: (60, 60, 230),
        }
        color = (150, 150, 150) if paused_reason else colors[snapshot.state]
        display_position = self._display_position(snapshot.x_px, snapshot.y_px)
        navigation = self._navigation.update(snapshot) if self._navigation else None
        layer_poi_count = 0
        if self._poi_catalog is not None and snapshot.position is not None:
            layer_pois = [
                poi
                for poi in self._poi_catalog.on_layer(snapshot.position)
                if self._poi_kinds is None or poi.kind in self._poi_kinds
                if self._poi_progress is None or poi.id not in self._poi_progress.collected_ids
            ]
            layer_poi_count = len(layer_pois)
            for poi in layer_pois:
                poi_point = (round(poi.x * self.scale), round(poi.y * self.scale))
                cv2.circle(canvas, poi_point, 2, self._poi_color(poi), thickness=-1)
        target_point: tuple[int, int] | None = None
        if navigation is not None and navigation.target is not None:
            poi = navigation.target
            target_point = (round(poi.x * self.scale), round(poi.y * self.scale))
            target_color = (150, 150, 150) if not navigation.available else (255, 255, 255)
            cv2.circle(canvas, target_point, 9, target_color, thickness=2)
        if display_position is not None:
            point = (
                round(display_position[0] * self.scale),
                round(display_position[1] * self.scale),
            )
            cv2.circle(canvas, point, 9, (10, 10, 10), thickness=3)
            cv2.circle(canvas, point, 7, color, thickness=-1)
            if target_point is not None:
                line_color = (
                    (150, 150, 150)
                    if navigation is None or not navigation.available
                    else (255, 235, 180)
                )
                cv2.line(canvas, point, target_point, line_color, 2, cv2.LINE_AA)
        state_label = "PAUSED" if paused_reason else snapshot.state.value
        shown_position = (
            f"({display_position[0]:.2f}, {display_position[1]:.2f})"
            if display_position is not None
            else "(-, -)"
        )
        status = (
            f"{state_label}  pos={shown_position}  "
            f"confidence={snapshot.confidence:.2f}  fps={fps:.1f}"
        )
        detail_reason = paused_reason or snapshot.reason or ""
        detail = (
            f"layer={snapshot.map_layer_id or '-'}  "
            f"reference={snapshot.reference_id or '-'}  {detail_reason}"
        )
        target_text, controls = self._navigation_text(navigation, layer_poi_count)
        y = self.base.shape[0]
        cv2.putText(canvas, status, (12, y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
        cv2.putText(canvas, detail, (12, y + 44), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (210, 210, 210), 1, cv2.LINE_AA)
        nav_color = (210, 210, 210) if navigation is None or navigation.available else (135, 135, 135)
        self._put_unicode_text(canvas, target_text, (12, y + 52), nav_color)
        cv2.putText(canvas, controls, (12, y + 90), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (190, 190, 190), 1, cv2.LINE_AA)
        if navigation is not None and navigation.bearing_degrees is not None:
            self._draw_direction_arrow(canvas, navigation, y)
        cv2.imshow(self.window_name, canvas)
        key = cv2.waitKey(1) & 0xFF
        global_action = self._poll_global_numpad_action()
        if self._navigation is not None:
            if key in (ord("n"), ord("N")):
                self._navigation.next_target()
            elif key in (ord("p"), ord("P")):
                self._navigation.previous_target()
            elif key in (ord("s"), ord("S")):
                self._navigation.skip()
            elif key in (ord("m"), ord("M")):
                self._navigation.mark_collected()
            elif key in (ord("u"), ord("U")):
                self._navigation.undo()
            elif global_action == "next":
                self._navigation.next_target()
            elif global_action == "previous":
                self._navigation.previous_target()
            elif global_action == "skip":
                self._navigation.skip()
            elif global_action == "collected":
                self._navigation.mark_collected()
            elif global_action == "undo":
                self._navigation.undo()
        try:
            visible = cv2.getWindowProperty(self.window_name, cv2.WND_PROP_VISIBLE) >= 1
        except cv2.error:
            visible = False
        return visible and key not in (27, ord("q"), ord("Q"))

    @staticmethod
    def _navigation_text(
        navigation: NavigationSnapshot | None, layer_poi_count: int
    ) -> tuple[str, str]:
        controls = "Num4/6 prev/next  Num2 skip  Num5 collected  Num8 undo  Q quit"
        if navigation is None or navigation.target is None:
            return f"target=-  visible_pois={layer_poi_count}", controls
        target = navigation.target
        if not navigation.available:
            distance = "frozen"
        elif navigation.distance_m is None:
            distance = "uncalibrated"
        else:
            distance = f"≈{navigation.distance_m:.0f} м (straight)"
        return (
            f"target={target.name} [{target.kind}]  distance={distance}  "
            f"layer={target.layer_id}",
            controls,
        )

    def _poll_global_numpad_action(
        self, key_state: Callable[[int], int] | None = None
    ) -> str | None:
        read_key = key_state or ctypes.windll.user32.GetAsyncKeyState
        selected: str | None = None
        for action, virtual_key in self._NUMPAD_ACTIONS.items():
            is_down = bool(read_key(virtual_key) & 0x8000)
            if is_down and not self._numpad_was_down[action] and selected is None:
                selected = action
            self._numpad_was_down[action] = is_down
        return selected

    def _put_unicode_text(
        self,
        canvas: np.ndarray,
        text: str,
        point: tuple[int, int],
        color: tuple[int, int, int],
    ) -> None:
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        draw = ImageDraw.Draw(image)
        draw.text(point, text, font=self._unicode_font, fill=(color[2], color[1], color[0]))
        np.copyto(canvas, cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR))

    @staticmethod
    def _draw_direction_arrow(
        canvas: np.ndarray, navigation: NavigationSnapshot, panel_y: int
    ) -> None:
        assert navigation.bearing_degrees is not None
        center = (canvas.shape[1] - 34, panel_y + 56)
        angle = radians(navigation.bearing_degrees)
        tip = (
            round(center[0] + 25 * sin(angle)),
            round(center[1] - 25 * cos(angle)),
        )
        color = (255, 235, 180) if navigation.available else (135, 135, 135)
        cv2.arrowedLine(canvas, center, tip, color, 3, cv2.LINE_AA, tipLength=0.35)
        cv2.putText(
            canvas, "N", (center[0] - 5, panel_y + 17),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1, cv2.LINE_AA,
        )

    def close(self) -> None:
        try:
            cv2.destroyWindow(self.window_name)
        except cv2.error:
            pass
