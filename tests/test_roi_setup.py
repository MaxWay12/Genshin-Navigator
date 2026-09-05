from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

from genshin_navigator.config import Roi
from genshin_navigator.roi_setup import ScreenBounds, validate_roi, write_roi_atomic, configure_roi


class RoiSetupTests(unittest.TestCase):
    def test_tall_rectangle_rejected(self):
        self.assertFalse(validate_roi(Roi(14, 330, 332, 651), ScreenBounds(0, 0, 1920, 1080)).valid)

    def test_preview_escape_preserves_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"unknown": true}', encoding="utf-8")
            with patch("genshin_navigator.roi_setup.grab_screen", return_value=np.zeros((1080, 1920, 3), dtype=np.uint8)), \
                 patch("genshin_navigator.roi_setup.virtual_screen_bounds", return_value=ScreenBounds(0, 0, 1920, 1080)), \
                 patch("genshin_navigator.roi_setup.cv2.selectROI", return_value=(20, 20, 216, 216)), \
                 patch("genshin_navigator.roi_setup.cv2.destroyWindow"), \
                 patch("genshin_navigator.roi_setup.cv2.imshow"), \
                 patch("genshin_navigator.roi_setup.cv2.waitKey", return_value=27):
                self.assertIsNone(configure_roi(path))
            self.assertEqual(path.read_text(), '{"unknown": true}')

    def test_roi_inside_virtual_desktop_is_valid(self) -> None:
        result = validate_roi(Roi(-1800, 20, 216, 216), ScreenBounds(-1920, 0, 3840, 1080))
        self.assertTrue(result.valid)

    def test_roi_outside_or_too_small_is_rejected(self) -> None:
        screen = ScreenBounds(0, 0, 1920, 1080)
        self.assertFalse(validate_roi(Roi(1850, 20, 216, 216), screen).valid)
        self.assertFalse(validate_roi(Roi(20, 20, 80, 80), screen).valid)

    def test_atomic_update_preserves_other_config_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({"map_path": "atlas.png", "roi": {"left": 0, "top": 0, "width": 10, "height": 10}}),
                encoding="utf-8",
            )
            write_roi_atomic(path, Roi(50, 60, 216, 216))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["map_path"], "atlas.png")
            self.assertEqual(payload["roi"], {"left": 50, "top": 60, "width": 216, "height": 216})


if __name__ == "__main__":
    unittest.main()
