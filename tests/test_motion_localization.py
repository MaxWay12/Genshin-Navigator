from __future__ import annotations

import unittest

import cv2
import numpy as np

from genshin_navigator.config import MotionFallbackConfig
from genshin_navigator.motion_localization import RelativeMotionLocalizer
from genshin_navigator.position import CoordinateSpace, MapPosition, PositionState


def _position() -> MapPosition:
    return MapPosition(
        region_id="sumeru_desert",
        layer_id="surface",
        coordinate_space=CoordinateSpace.SURFACE_ATLAS,
        x=100,
        y=100,
        confidence=0.9,
        state=PositionState.TRACKING,
        timestamp=1,
        reference_id="base",
    )


class RelativeMotionLocalizerTests(unittest.TestCase):
    @staticmethod
    def _textured_frame() -> np.ndarray:
        frame = np.zeros((160, 160, 3), np.uint8)
        for y in range(25, 140, 15):
            for x in range(25, 140, 15):
                cv2.circle(frame, (x, y), 2, (255, 255, 255), -1)
                cv2.line(frame, (x - 3, y + 4), (x + 4, y + 6), (120, 180, 220), 1)
        return frame

    def test_converts_map_translation_to_opposite_player_motion(self) -> None:
        localizer = RelativeMotionLocalizer(
            MotionFallbackConfig(min_inliers=12), "sumeru_desert"
        )
        before = self._textured_frame()
        after = cv2.warpAffine(
            before,
            np.float32([[1, 0, 3], [0, 1, -2]]),
            (160, 160),
        )
        localizer.observe(before)
        result = localizer.locate_near(after, _position(), 0.8)
        self.assertTrue(result.found)
        self.assertAlmostEqual(result.x_px or 0, 97.6, delta=0.3)
        self.assertAlmostEqual(result.y_px or 0, 101.6, delta=0.3)
        self.assertEqual(result.match_method, "motion")

    def test_blank_frames_are_rejected(self) -> None:
        localizer = RelativeMotionLocalizer(MotionFallbackConfig(), "sumeru_desert")
        blank = np.zeros((160, 160, 3), np.uint8)
        localizer.observe(blank)
        result = localizer.locate_near(blank, _position(), 0.8)
        self.assertFalse(result.found)
        self.assertEqual(result.reason, "motion_not_enough_features")

    def test_cannot_acquire_without_absolute_fix(self) -> None:
        localizer = RelativeMotionLocalizer(MotionFallbackConfig(), "sumeru_desert")
        result = localizer.locate_near(self._textured_frame(), _position(), 0.8)
        self.assertFalse(result.found)
        self.assertEqual(result.reason, "motion_requires_absolute_fix")

    def test_reset_disarms_motion_until_next_absolute_fix(self) -> None:
        localizer = RelativeMotionLocalizer(
            MotionFallbackConfig(min_inliers=12), "sumeru_desert"
        )
        frame = self._textured_frame()
        localizer.observe(frame)
        localizer.reset()
        result = localizer.locate_near(frame, _position(), 0.8)
        self.assertFalse(result.found)
        self.assertEqual(result.reason, "motion_requires_absolute_fix")

    def test_consecutive_motion_has_a_strict_budget(self) -> None:
        localizer = RelativeMotionLocalizer(
            MotionFallbackConfig(min_inliers=12, max_consecutive_frames=1),
            "sumeru_desert",
        )
        before = self._textured_frame()
        after = cv2.warpAffine(
            before,
            np.float32([[1, 0, 2], [0, 1, 0]]),
            (160, 160),
        )
        localizer.observe(before)
        self.assertTrue(localizer.locate_near(after, _position(), 0.8).found)
        exhausted = localizer.locate_near(after, _position(), 0.8)
        self.assertFalse(exhausted.found)
        self.assertEqual(exhausted.reason, "motion_budget_exhausted")


if __name__ == "__main__":
    unittest.main()
