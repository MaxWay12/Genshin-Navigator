from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from genshin_navigator.capture import grab_roi
from genshin_navigator.config import Roi


class CaptureTests(unittest.TestCase):
    def test_grab_roi_requests_only_minimap_rectangle(self) -> None:
        roi = Roi(left=57, top=19, width=216, height=216)
        image = Image.fromarray(np.zeros((216, 216, 3), dtype=np.uint8))

        with patch(
            "genshin_navigator.capture.ImageGrab.grab", return_value=image
        ) as grab:
            frame = grab_roi(roi)

        grab.assert_called_once_with(bbox=(57, 19, 273, 235), all_screens=True)
        self.assertEqual(frame.shape, (216, 216, 3))

    def test_grab_roi_rejects_unexpected_capture_size(self) -> None:
        roi = Roi(left=10, top=20, width=100, height=80)
        image = Image.fromarray(np.zeros((79, 100, 3), dtype=np.uint8))

        with patch(
            "genshin_navigator.capture.ImageGrab.grab", return_value=image
        ):
            with self.assertRaisesRegex(ValueError, "unexpected size"):
                grab_roi(roi)


if __name__ == "__main__":
    unittest.main()
