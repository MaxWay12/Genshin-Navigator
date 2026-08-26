from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .capture import load_image
from .scenario import load_scenario


@dataclass(frozen=True)
class AtlasViewport:
    left: int
    top: int
    width: int
    height: int
    atlas_width: int
    atlas_height: int

    def to_atlas(self, x: int, y: int) -> tuple[float, float] | None:
        if not (
            self.left <= x < self.left + self.width
            and self.top <= y < self.top + self.height
        ):
            return None
        return (
            (x - self.left) * self.atlas_width / self.width,
            (y - self.top) * self.atlas_height / self.height,
        )

    def to_canvas(self, x: float, y: float) -> tuple[int, int]:
        return (
            round(self.left + x * self.width / self.atlas_width),
            round(self.top + y * self.height / self.atlas_height),
        )


class ScenarioAnnotation:
    def __init__(
        self,
        scenario: str | Path,
        atlas_path: str | Path,
        *,
        region_id: str,
        layer_id: str = "surface",
        tolerance_px: float = 35.0,
    ) -> None:
        if tolerance_px <= 0:
            raise ValueError("Checkpoint tolerance must be positive")
        self.root, self.manifest = load_scenario(scenario)
        self.manifest_path = self.root / "scenario.json"
        self.atlas_path = Path(atlas_path).resolve()
        self.atlas = load_image(self.atlas_path)
        self.region_id = region_id
        self.layer_id = layer_id
        self.tolerance_px = float(tolerance_px)
        self.frame_index = 0
        self.checkpoints = [dict(item) for item in self.manifest.get("checkpoints", [])]

    @property
    def frames(self) -> list[dict[str, object]]:
        frames = self.manifest["frames"]
        assert isinstance(frames, list)
        return frames  # type: ignore[return-value]

    @property
    def current_timestamp(self) -> float:
        return float(self.frames[self.frame_index]["timestamp_seconds"])

    def move(self, delta: int) -> None:
        self.frame_index = max(0, min(len(self.frames) - 1, self.frame_index + delta))

    def checkpoint_for_current_frame(self) -> dict[str, object] | None:
        timestamp = self.current_timestamp
        return next(
            (
                item
                for item in self.checkpoints
                if abs(float(item["timestamp_seconds"]) - timestamp) < 1e-6
            ),
            None,
        )

    def set_checkpoint(self, x: float, y: float) -> None:
        if not (0 <= x < self.atlas.shape[1] and 0 <= y < self.atlas.shape[0]):
            raise ValueError("Checkpoint lies outside the canonical atlas")
        self.remove_checkpoint()
        self.checkpoints.append(
            {
                "timestamp_seconds": self.current_timestamp,
                "region_id": self.region_id,
                "layer_id": self.layer_id,
                "position": {
                    "x": round(float(x), 2),
                    "y": round(float(y), 2),
                    "tolerance_px": round(self.tolerance_px, 2),
                },
            }
        )
        self.checkpoints.sort(key=lambda item: float(item["timestamp_seconds"]))

    def remove_checkpoint(self) -> bool:
        checkpoint = self.checkpoint_for_current_frame()
        if checkpoint is None:
            return False
        self.checkpoints.remove(checkpoint)
        return True

    def save(self) -> Path:
        payload = dict(self.manifest)
        payload["checkpoints"] = self.checkpoints
        temporary = self.manifest_path.with_name(
            f".{self.manifest_path.name}.{os.getpid()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, self.manifest_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        self.manifest = payload
        return self.manifest_path


class ScenarioAnnotationView:
    WINDOW = "Genshin Navigator - Scenario Annotation"

    def __init__(self, session: ScenarioAnnotation) -> None:
        self.session = session
        self.viewport: AtlasViewport | None = None
        self.saved = False

    def _render(self) -> np.ndarray:
        canvas = np.full((820, 1420, 3), (13, 22, 31), np.uint8)
        atlas = self.session.atlas
        available_w, available_h = 1125, 745
        scale = min(available_w / atlas.shape[1], available_h / atlas.shape[0])
        width, height = round(atlas.shape[1] * scale), round(atlas.shape[0] * scale)
        left, top = 280 + (available_w - width) // 2, 55 + (available_h - height) // 2
        resized = cv2.resize(atlas, (width, height), interpolation=cv2.INTER_AREA)
        canvas[top : top + height, left : left + width] = resized
        self.viewport = AtlasViewport(
            left, top, width, height, atlas.shape[1], atlas.shape[0]
        )

        frame = self.session.frames[self.session.frame_index]
        minimap = load_image(self.session.root / str(frame["image"]))
        mini_scale = min(250 / minimap.shape[1], 250 / minimap.shape[0])
        mini_w, mini_h = round(minimap.shape[1] * mini_scale), round(minimap.shape[0] * mini_scale)
        mini = cv2.resize(minimap, (mini_w, mini_h), interpolation=cv2.INTER_NEAREST)
        canvas[80 : 80 + mini_h, 15 : 15 + mini_w] = mini

        checkpoint = self.session.checkpoint_for_current_frame()
        if checkpoint is not None:
            position = checkpoint["position"]
            assert isinstance(position, dict)
            point = self.viewport.to_canvas(float(position["x"]), float(position["y"]))
            cv2.circle(canvas, point, 9, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.drawMarker(canvas, point, (0, 255, 255), cv2.MARKER_CROSS, 18, 2)

        index_text = f"frame {self.session.frame_index + 1}/{len(self.session.frames)}"
        time_text = f"t={self.session.current_timestamp:.3f}s"
        count_text = f"checkpoints={len(self.session.checkpoints)} tolerance={self.session.tolerance_px:.0f}px"
        cv2.putText(canvas, index_text, (15, 365), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (235, 235, 235), 1, cv2.LINE_AA)
        cv2.putText(canvas, time_text, (15, 395), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (235, 235, 235), 1, cv2.LINE_AA)
        cv2.putText(canvas, count_text, (15, 425), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (120, 220, 120), 1, cv2.LINE_AA)
        instructions = [
            "A/D or arrows: frame",
            "Left click atlas: set/replace",
            "Right click or Delete: remove",
            "+/-: tolerance",
            "Enter: save   Esc: cancel",
        ]
        for index, line in enumerate(instructions):
            cv2.putText(canvas, line, (15, 475 + 30 * index), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (180, 185, 190), 1, cv2.LINE_AA)
        return canvas

    def _mouse(self, event: int, x: int, y: int, _flags: int, _data: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN and self.viewport is not None:
            point = self.viewport.to_atlas(x, y)
            if point is not None:
                self.session.set_checkpoint(*point)
        elif event == cv2.EVENT_RBUTTONDOWN:
            self.session.remove_checkpoint()

    def run(self) -> bool:
        cv2.namedWindow(self.WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.WINDOW, 1420, 820)
        cv2.setMouseCallback(self.WINDOW, self._mouse)
        try:
            while True:
                cv2.imshow(self.WINDOW, self._render())
                key = cv2.waitKeyEx(30)
                if cv2.getWindowProperty(self.WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    return False
                if key in (27,):
                    return False
                if key in (13, 10):
                    self.session.save()
                    self.saved = True
                    return True
                if key in (ord("a"), ord("A"), 2424832):
                    self.session.move(-1)
                elif key in (ord("d"), ord("D"), 2555904):
                    self.session.move(1)
                elif key in (8, 46, 3014656):
                    self.session.remove_checkpoint()
                elif key in (ord("+"), ord("=")):
                    self.session.tolerance_px = min(500.0, self.session.tolerance_px + 5.0)
                elif key in (ord("-"), ord("_")):
                    self.session.tolerance_px = max(5.0, self.session.tolerance_px - 5.0)
        finally:
            cv2.destroyWindow(self.WINDOW)


def annotate_scenario(
    scenario: str | Path,
    atlas_path: str | Path,
    *,
    region_id: str,
    layer_id: str = "surface",
    tolerance_px: float = 35.0,
) -> bool:
    return ScenarioAnnotationView(
        ScenarioAnnotation(
            scenario,
            atlas_path,
            region_id=region_id,
            layer_id=layer_id,
            tolerance_px=tolerance_px,
        )
    ).run()
