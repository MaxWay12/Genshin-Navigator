from __future__ import annotations

import cv2
import numpy as np

from .poi import PoiCatalog, PoiProgress, PointOfInterest
from .tracker import TrackerSnapshot, TrackerState


class DebugMapView:
    def __init__(
        self,
        atlas: np.ndarray,
        layer_maps: dict[str, np.ndarray] | None = None,
        poi_catalog: PoiCatalog | None = None,
        poi_kinds: set[str] | None = None,
        poi_target_kinds: set[str] | None = None,
        poi_progress: PoiProgress | None = None,
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
        self._active_layer_id = ""
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
        cv2.resizeWindow(self.window_name, size[0], size[1] + 76)

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
        canvas = cv2.copyMakeBorder(self.base, 0, 76, 0, 0, cv2.BORDER_CONSTANT, value=(18, 18, 18))
        colors = {
            TrackerState.TRACKING: (80, 220, 80),
            TrackerState.ACQUIRING: (40, 210, 240),
            TrackerState.RELOCATING: (30, 150, 255),
            TrackerState.LOST: (60, 60, 230),
        }
        color = (150, 150, 150) if paused_reason else colors[snapshot.state]
        display_position = self._display_position(snapshot.x_px, snapshot.y_px)
        nearest: tuple[PointOfInterest, float] | None = None
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
            ranked = self._poi_catalog.nearest(
                snapshot.position,
                kinds=self._poi_target_kinds,
                exclude_ids=(
                    self._poi_progress.collected_ids
                    if self._poi_progress is not None
                    else None
                ),
                limit=1,
            )
            if ranked:
                nearest = ranked[0]
                poi, _ = nearest
                poi_point = (round(poi.x * self.scale), round(poi.y * self.scale))
                cv2.circle(canvas, poi_point, 7, (255, 255, 255), thickness=2)
        if display_position is not None:
            point = (
                round(display_position[0] * self.scale),
                round(display_position[1] * self.scale),
            )
            cv2.circle(canvas, point, 9, (10, 10, 10), thickness=3)
            cv2.circle(canvas, point, 7, color, thickness=-1)
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
        nearest_text = (
            f"target={nearest[0].kind}:{nearest[0].label_id}  distance={nearest[1]:.1f}px  visible_pois={layer_poi_count}  M=collected"
            if nearest is not None
            else f"nearest=-  layer_pois={layer_poi_count}"
        )
        y = self.base.shape[0]
        cv2.putText(canvas, status, (12, y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
        cv2.putText(canvas, detail, (12, y + 44), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (210, 210, 210), 1, cv2.LINE_AA)
        cv2.putText(canvas, nearest_text, (12, y + 66), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (210, 210, 210), 1, cv2.LINE_AA)
        cv2.imshow(self.window_name, canvas)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("m"), ord("M")) and nearest is not None and self._poi_progress is not None:
            self._poi_progress.mark_collected(nearest[0].id)
        try:
            visible = cv2.getWindowProperty(self.window_name, cv2.WND_PROP_VISIBLE) >= 1
        except cv2.error:
            visible = False
        return visible and key not in (27, ord("q"), ord("Q"))

    def close(self) -> None:
        try:
            cv2.destroyWindow(self.window_name)
        except cv2.error:
            pass
