from __future__ import annotations

import unittest

import cv2
import numpy as np

from genshin_navigator.config import EdgeCorrelationConfig
from genshin_navigator.edge_correlation import EdgeCorrelationLocalizer


class EdgeCorrelationLocalizerTests(unittest.TestCase):
    @staticmethod
    def _reference() -> np.ndarray:
        image = np.zeros((320, 420, 3), np.uint8)
        cv2.line(image, (120, 80), (230, 190), (220, 220, 220), 4)
        cv2.ellipse(image, (210, 150), (55, 25), 25, 0, 270, (180, 180, 180), 3)
        cv2.rectangle(image, (165, 105), (190, 135), (255, 255, 255), 2)
        cv2.line(image, (175, 200), (255, 110), (150, 150, 150), 3)
        return image

    @staticmethod
    def _config(**changes: object) -> EdgeCorrelationConfig:
        values: dict[str, object] = {
            "enabled": True,
            "scales": (1.0,),
            "rotations_degrees": (0.0,),
            "min_score": 0.25,
            "min_peak_margin": 0.05,
            "exclusion_radius_px": 80.0,
        }
        values.update(changes)
        return EdgeCorrelationConfig(**values)

    def test_finds_unique_absolute_surface_position(self) -> None:
        reference = self._reference()
        minimap = reference[70:230, 130:290].copy()
        localizer = EdgeCorrelationLocalizer(
            reference, self._config(), "sumeru_desert"
        )
        result = localizer.locate(minimap)
        self.assertTrue(result.found, result.reason)
        self.assertAlmostEqual(result.x_px or 0, 210, delta=2)
        self.assertAlmostEqual(result.y_px or 0, 150, delta=2)
        self.assertEqual(result.match_method, "edge_correlation")

    def test_rejects_two_equally_plausible_locations(self) -> None:
        reference = self._reference()
        reference[70:230, 250:410] = reference[70:230, 130:290]
        minimap = reference[70:230, 130:290].copy()
        localizer = EdgeCorrelationLocalizer(
            reference,
            self._config(min_peak_margin=0.10, exclusion_radius_px=70.0),
            "sumeru_desert",
        )
        result = localizer.locate(minimap)
        self.assertFalse(result.found)
        self.assertEqual(result.reason, "edge_correlation_ambiguous")

    def test_rejects_blank_minimap(self) -> None:
        localizer = EdgeCorrelationLocalizer(
            self._reference(), self._config(), "sumeru_desert"
        )
        result = localizer.locate(np.zeros((160, 160, 3), np.uint8))
        self.assertFalse(result.found)
        self.assertEqual(result.reason, "edge_correlation_too_sparse")


if __name__ == "__main__":
    unittest.main()
