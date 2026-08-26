from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from genshin_navigator.anchor_localization import (
    AnchorLocalizer,
    MapAnchor,
    ObservedAnchor,
    TemplateAnchorDetector,
)
from genshin_navigator.config import AnchorLocalizationConfig
from genshin_navigator.position import CoordinateSpace, MapPosition, PositionState


class _Detector:
    def __init__(self, observations: list[ObservedAnchor]):
        self.observations = observations

    def detect(self, _minimap: np.ndarray) -> list[ObservedAnchor]:
        return self.observations


def _position(x: float, y: float) -> MapPosition:
    return MapPosition(
        region_id="sumeru_desert",
        layer_id="surface",
        coordinate_space=CoordinateSpace.SURFACE_ATLAS,
        x=x,
        y=y,
        confidence=0.9,
        state=PositionState.TRACKING,
        timestamp=1.0,
        reference_id="base",
    )


class AnchorLocalizationTests(unittest.TestCase):
    def _config(self, **changes) -> AnchorLocalizationConfig:
        values = {
            "enabled": False,
            "default_canonical_scale": 0.8,
            "local_match_radius_px": 45,
            "max_residual_px": 4,
            "min_global_anchors": 3,
        }
        values.update(changes)
        return AnchorLocalizationConfig(**values)

    def test_single_unique_anchor_continues_confirmed_local_track(self) -> None:
        detector = _Detector([ObservedAnchor("waypoint", 75, 50, 0.9)])
        localizer = AnchorLocalizer(
            "sumeru_desert", (500, 500),
            [MapAnchor("a", "waypoint", 120, 100)], detector, self._config(),
        )
        result = localizer.locate_near(np.zeros((100, 100, 3), np.uint8), _position(101, 100))
        self.assertTrue(result.found)
        self.assertEqual((result.x_px, result.y_px), (100.0, 100.0))
        self.assertEqual(result.match_method, "anchors")

    def test_ambiguous_single_anchor_is_rejected(self) -> None:
        detector = _Detector([ObservedAnchor("waypoint", 75, 50, 0.9)])
        localizer = AnchorLocalizer(
            "sumeru_desert", (500, 500),
            [
                MapAnchor("a", "waypoint", 120, 100),
                MapAnchor("b", "waypoint", 122, 100),
            ], detector, self._config(),
        )
        result = localizer.locate_near(np.zeros((100, 100, 3), np.uint8), _position(101, 100))
        self.assertFalse(result.found)
        self.assertEqual(result.reason, "no_local_anchor_assignment")

    def test_three_consistent_anchors_allow_global_acquisition(self) -> None:
        detector = _Detector(
            [
                ObservedAnchor("waypoint", 75, 50, 0.9),
                ObservedAnchor("domain", 50, 75, 0.91),
                ObservedAnchor("statue", 25, 50, 0.92),
            ]
        )
        anchors = [
            MapAnchor("a", "waypoint", 120, 100),
            MapAnchor("b", "domain", 100, 120),
            MapAnchor("c", "statue", 80, 100),
        ]
        localizer = AnchorLocalizer(
            "sumeru_desert", (500, 500), anchors, detector, self._config()
        )
        result = localizer.locate(np.zeros((100, 100, 3), np.uint8))
        self.assertTrue(result.found)
        self.assertEqual((result.x_px, result.y_px), (100.0, 100.0))
        self.assertEqual(result.matches, 3)

    def test_template_detector_deduplicates_scale_hits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "waypoint.png"
            template = np.zeros((20, 20, 4), np.uint8)
            template[3:17, 8:12, :3] = (255, 255, 255)
            template[8:12, 3:17, :3] = (255, 255, 255)
            template[:, :, 3] = 255
            cv2.imwrite(str(path), template)
            minimap = np.zeros((100, 100, 3), np.uint8)
            scaled = cv2.resize(template[:, :, :3], (10, 10), interpolation=cv2.INTER_AREA)
            minimap[45:55, 70:80] = scaled
            detector = TemplateAnchorDetector(
                {"waypoint": path}, min_score=0.98, scales=(0.5,)
            )
            detections = detector.detect(minimap)
            self.assertEqual(len(detections), 1)
            self.assertAlmostEqual(detections[0].x, 75, delta=1)


if __name__ == "__main__":
    unittest.main()
