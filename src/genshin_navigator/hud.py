from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .navigation import NavigationSnapshot
from .tracker import TrackerSnapshot


@dataclass(frozen=True)
class HudPresentation:
    target: str
    distance: str
    layer: str
    state: str
    bearing_degrees: float | None
    available: bool


def build_hud_presentation(
    tracker: TrackerSnapshot,
    navigation: NavigationSnapshot | None,
    layer_labels: dict[str, str],
) -> HudPresentation:
    layer_id = (
        tracker.position.layer_id
        if tracker.position is not None
        else tracker.map_layer_id or "surface"
    )
    layer = layer_labels.get(layer_id, layer_id)
    target = "Цель не выбрана"
    distance = "—"
    available = bool(navigation is not None and navigation.available)
    bearing = None
    if navigation is not None and navigation.target is not None:
        target = f"{navigation.target.name} · {navigation.target.kind}"
        if not available:
            distance = "позиция уточняется"
        elif navigation.distance_m is None:
            distance = "расстояние не откалибровано"
        else:
            distance = f"≈{navigation.distance_m:.0f} м"
        bearing = navigation.bearing_degrees if available else None
    state = "TRACKING" if available else tracker.state.value
    return HudPresentation(target, distance, layer, state, bearing, available)


@dataclass(frozen=True)
class WindowGeometry:
    x: int
    y: int
    width: int
    height: int


def clamp_geometry(
    geometry: WindowGeometry, work_area: WindowGeometry
) -> WindowGeometry:
    width = min(max(1, geometry.width), work_area.width)
    height = min(max(1, geometry.height), work_area.height)
    max_x = work_area.x + work_area.width - width
    max_y = work_area.y + work_area.height - height
    return WindowGeometry(
        min(max(geometry.x, work_area.x), max_x),
        min(max(geometry.y, work_area.y), max_y),
        width,
        height,
    )


class HudStateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(
        self, default: WindowGeometry, work_area: WindowGeometry
    ) -> WindowGeometry:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            geometry = WindowGeometry(
                int(raw["x"]), int(raw["y"]),
                int(raw.get("width", default.width)),
                int(raw.get("height", default.height)),
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            geometry = default
        return clamp_geometry(geometry, work_area)

    def save(self, geometry: WindowGeometry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"format_version": 1, **geometry.__dict__}, indent=2
        )
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
