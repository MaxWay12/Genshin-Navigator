from __future__ import annotations

import unittest

import cv2
import numpy as np

from genshin_navigator.config import MatcherConfig
from genshin_navigator.matcher import MinimapMatcher


class MinimapMatcherTests(unittest.TestCase):
    def test_locates_rotated_crop_on_synthetic_map(self) -> None:
        rng = np.random.default_rng(42)
        reference = rng.integers(0, 256, (900, 1100, 3), dtype=np.uint8)
        reference = cv2.GaussianBlur(reference, (3, 3), 0)
        for index in range(80):
            point = (int(rng.integers(20, 1080)), int(rng.integers(20, 880)))
            cv2.circle(reference, point, int(rng.integers(3, 14)), (255, 255, 255), 2)

        expected_x, expected_y = 640, 470
        crop_size = 260
        crop = reference[
            expected_y - crop_size // 2 : expected_y + crop_size // 2,
            expected_x - crop_size // 2 : expected_x + crop_size // 2,
        ]
        matrix = cv2.getRotationMatrix2D((crop_size / 2, crop_size / 2), 17, 1.0)
        minimap = cv2.warpAffine(crop, matrix, (crop_size, crop_size))
        cv2.circle(minimap, (crop_size // 2, crop_size // 2), 12, (10, 10, 250), -1)

        matcher = MinimapMatcher(
            reference,
            MatcherConfig(min_matches=10, min_inliers=7, ratio_threshold=0.8),
        )
        result = matcher.locate(minimap)

        self.assertTrue(result.found, result.reason)
        self.assertAlmostEqual(result.x_px or 0, expected_x, delta=8)
        self.assertAlmostEqual(result.y_px or 0, expected_y, delta=8)
        self.assertGreater(result.confidence, 0.25)

    def test_rejects_blank_minimap(self) -> None:
        rng = np.random.default_rng(7)
        reference = rng.integers(0, 256, (500, 500, 3), dtype=np.uint8)
        matcher = MinimapMatcher(reference)
        result = matcher.locate(np.zeros((220, 220, 3), dtype=np.uint8))
        self.assertFalse(result.found)
        self.assertEqual(result.reason, "not_enough_minimap_features")

    def test_prepared_features_preserve_local_search_result(self) -> None:
        rng = np.random.default_rng(11)
        reference = rng.integers(0, 256, (500, 500, 3), dtype=np.uint8)
        matcher = MinimapMatcher(
            reference,
            MatcherConfig(min_matches=8, min_inliers=6, ratio_threshold=0.82),
        )
        minimap = reference[140:360, 140:360].copy()
        kwargs = {
            "ratio_threshold": 0.82,
            "min_matches": 8,
            "min_inliers": 6,
        }

        regular = matcher.locate_near(minimap, (250, 250), 150, **kwargs)
        prepared = matcher.prepare_minimap(minimap)
        reused = matcher.locate_near_prepared(
            minimap, prepared, (250, 250), 150, **kwargs
        )

        self.assertEqual(regular.to_dict(), reused.to_dict())


if __name__ == "__main__":
    unittest.main()
