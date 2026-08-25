from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from genshin_navigator.diagnostics import load_diagnostic_bundle


class DiagnosticCompatibilityTests(unittest.TestCase):
    def test_loads_legacy_v3_and_current_v4_bundles(self) -> None:
        for version in (3, 4):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                cv2.imwrite(str(root / "minimap_000.png"), np.zeros((8, 8, 3), dtype=np.uint8))
                (root / "metadata.json").write_text(
                    json.dumps(
                        {
                            "format_version": version,
                            "trigger": "test",
                            "frames": [{"image": "minimap_000.png", "timestamp": 1.0}],
                        }
                    ),
                    encoding="utf-8",
                )
                _, payload = load_diagnostic_bundle(root)
                self.assertEqual(payload["format_version"], version)

    def test_rejects_frame_path_outside_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "metadata.json").write_text(
                json.dumps(
                    {
                        "format_version": 4,
                        "frames": [{"image": "../full-screen.png", "timestamp": 1.0}],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_diagnostic_bundle(root)


if __name__ == "__main__":
    unittest.main()
