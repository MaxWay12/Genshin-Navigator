from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np


SCRIPT = Path(__file__).parents[1] / "scripts" / "crop_registered_reference.py"


class CropRegisteredReferenceTests(unittest.TestCase):
    def test_preserves_registration_quality_and_composes_crop_transform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            registration = root / "registration.json"
            output = root / "crop.png"
            cv2.imwrite(str(source), np.full((80, 100, 3), 120, np.uint8))
            registration.write_text(
                json.dumps(
                    {
                        "inliers": 42,
                        "median_error_px": 0.25,
                        "local_to_canonical": [
                            [0.5, 0.0, 10.0],
                            [0.0, 0.5, 20.0],
                            [0.0, 0.0, 1.0],
                        ],
                    }
                ),
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(source),
                    str(registration),
                    str(output),
                    "--x",
                    "20",
                    "--y",
                    "10",
                    "--width",
                    "40",
                    "--height",
                    "30",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            metadata = json.loads(output.with_suffix(".json").read_text())
            self.assertEqual(metadata["inliers"], 42)
            self.assertEqual(metadata["median_error_px"], 0.25)
            self.assertEqual(metadata["local_to_canonical"][0][2], 20.0)
            self.assertEqual(metadata["local_to_canonical"][1][2], 25.0)


if __name__ == "__main__":
    unittest.main()
