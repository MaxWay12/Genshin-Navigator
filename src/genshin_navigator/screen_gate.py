from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .capture import load_image
from .config import ScreenGateConfig


@dataclass(frozen=True)
class ScreenGateResult:
    minimap_present: bool
    confidence: float
    reason: str | None = None


class MinimapScreenGate:
    """Recognize the fixed compass UI without inspecting map contents."""

    REFERENCE_SIZE = (216, 216)
    COMPASS_SEARCH = (86, 0, 138, 44)
    COMPASS_EXPECTED_TOP_LEFT = (99, 7)
    COMPASS_POSITION_TOLERANCE = 3

    def __init__(self, compass_template: np.ndarray, config: ScreenGateConfig):
        if compass_template is None or compass_template.size == 0:
            raise ValueError("Compass template is empty")
        self.config = config
        template_gray = self._gray(compass_template)
        self._template_edges = cv2.Canny(template_gray, 40, 120)
        if cv2.countNonZero(self._template_edges) < 8:
            raise ValueError("Compass template has too few edges")
        left, top, right, bottom = self.COMPASS_SEARCH
        if template_gray.shape[1] > right - left or template_gray.shape[0] > bottom - top:
            raise ValueError("Compass template exceeds its search area")

    @classmethod
    def from_config(cls, config: ScreenGateConfig) -> MinimapScreenGate:
        if config.template_path is None:
            raise ValueError("Screen gate has no compass template path")
        return cls(load_image(Path(config.template_path)), config)

    @staticmethod
    def _gray(image: np.ndarray) -> np.ndarray:
        if image.ndim == 2:
            return image
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    def check(self, minimap: np.ndarray) -> ScreenGateResult:
        if not self.config.enabled:
            return ScreenGateResult(minimap_present=True, confidence=1.0)
        if minimap is None or minimap.size == 0:
            return ScreenGateResult(False, 0.0, "minimap_crop_is_empty")

        normalized = cv2.resize(minimap, self.REFERENCE_SIZE, interpolation=cv2.INTER_AREA)
        gray = self._gray(normalized)
        deviation = float(gray.std())
        if deviation < self.config.blank_std_threshold:
            confidence = deviation / max(self.config.blank_std_threshold, 1e-6)
            return ScreenGateResult(False, round(confidence, 4), "loading_or_blank_screen")

        left, top, right, bottom = self.COMPASS_SEARCH
        search_edges = cv2.Canny(gray[top:bottom, left:right], 40, 120)
        scores = cv2.matchTemplate(
            search_edges, self._template_edges, cv2.TM_CCOEFF_NORMED
        )
        _, maximum, _, maximum_location = cv2.minMaxLoc(scores)
        score = max(0.0, float(maximum))
        if score < self.config.match_threshold:
            return ScreenGateResult(False, round(score, 4), "minimap_ui_not_detected")
        expected_x = self.COMPASS_EXPECTED_TOP_LEFT[0] - left
        expected_y = self.COMPASS_EXPECTED_TOP_LEFT[1] - top
        if (
            abs(maximum_location[0] - expected_x) > self.COMPASS_POSITION_TOLERANCE
            or abs(maximum_location[1] - expected_y) > self.COMPASS_POSITION_TOLERANCE
        ):
            return ScreenGateResult(
                False, round(score, 4), "minimap_compass_misaligned"
            )
        return ScreenGateResult(True, round(score, 4))
