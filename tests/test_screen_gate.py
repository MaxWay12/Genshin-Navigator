from __future__ import annotations

import unittest
from pathlib import Path

import cv2
import numpy as np

from genshin_navigator.config import ScreenGateConfig
from genshin_navigator.screen_gate import MinimapScreenGate


class MinimapScreenGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = np.zeros((27, 26, 3), dtype=np.uint8)
        cv2.putText(
            self.template,
            "N",
            (2, 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (230, 230, 230),
            2,
            cv2.LINE_AA,
        )
        self.gate = MinimapScreenGate(
            self.template,
            ScreenGateConfig(
                enabled=True,
                template_path=Path("unused.png"),
                match_threshold=0.5,
                blank_std_threshold=8,
            ),
        )

    def test_accepts_compass_over_arbitrary_map_content(self) -> None:
        rng = np.random.default_rng(42)
        minimap = rng.integers(20, 180, (216, 216, 3), dtype=np.uint8)
        minimap[7:34, 99:125] = self.template

        result = self.gate.check(minimap)

        self.assertTrue(result.minimap_present)
        self.assertGreaterEqual(result.confidence, 0.5)

    def test_rejects_blank_loading_screen(self) -> None:
        result = self.gate.check(np.full((216, 216, 3), 12, dtype=np.uint8))

        self.assertFalse(result.minimap_present)
        self.assertEqual(result.reason, "loading_or_blank_screen")

    def test_rejects_compass_like_text_at_the_wrong_position(self) -> None:
        minimap = np.full((216, 216, 3), 30, dtype=np.uint8)
        minimap[17:44, 106:132] = self.template

        result = self.gate.check(minimap)

        self.assertFalse(result.minimap_present)
        self.assertEqual(result.reason, "minimap_compass_misaligned")


if __name__ == "__main__":
    unittest.main()
