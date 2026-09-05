import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from genshin_navigator.image_io import read_image, write_image


class ImageIoTests(unittest.TestCase):
    def test_unicode_roundtrip_all_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Карты Сумеру"
            root.mkdir()
            for channels in (1, 3, 4):
                shape = (15, 12) if channels == 1 else (15, 12, channels)
                expected = np.arange(np.prod(shape), dtype=np.uint8).reshape(shape)
                path = root / f"этаж {channels}.png"
                self.assertTrue(write_image(path, expected))
                np.testing.assert_array_equal(read_image(path, cv2.IMREAD_UNCHANGED), expected)
                self.assertEqual(read_image(path, cv2.IMREAD_GRAYSCALE).shape, (15, 12))

    def test_invalid_inputs_preserve_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "файл.png"
            self.assertIsNone(read_image(path))
            path.write_bytes(b"broken")
            self.assertIsNone(read_image(path))
            self.assertFalse(write_image(path, np.empty((0, 0), dtype=np.uint8)))
            self.assertEqual(path.read_bytes(), b"broken")
            path.write_bytes(b"")
            self.assertIsNone(read_image(path))
