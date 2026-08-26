from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np


SCRIPT = Path(__file__).parents[1] / "scripts" / "build_detail_pyramid.py"
SPEC = importlib.util.spec_from_file_location("build_detail_pyramid", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BuildDetailPyramidTests(unittest.TestCase):
    def _files(self, root: Path, *, inliers: int = 30, error: float = 1.0):
        base = root / "surface.json"
        base.write_text(
            json.dumps(
                {
                    "region_id": "sumeru_desert",
                    "canonical_size": [200, 100],
                    "levels": [
                        {
                            "id": "base",
                            "image": "atlas.png",
                            "local_to_canonical": np.eye(3).tolist(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        detail = root / "detail.png"
        cv2.imwrite(str(detail), np.full((40, 60, 3), 100, np.uint8))
        registration = root / "registration.json"
        registration.write_text(
            json.dumps(
                {
                    "inliers": inliers,
                    "median_error_px": error,
                    "local_to_canonical": [
                        [0.5, 0.0, 10.0],
                        [0.0, 0.5, 20.0],
                        [0.0, 0.0, 1.0],
                    ],
                }
            ),
            encoding="utf-8",
        )
        return base, detail, registration

    def test_builds_registered_detail_without_matcher_threshold_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, detail, registration = self._files(root)
            output = root / "candidate.json"
            argv = [
                str(SCRIPT), str(base), str(detail), str(registration), str(output),
                "--id", "ruins", "--source", "appsample", "--resolution-scale", "2",
            ]
            with patch.object(sys, "argv", argv):
                self.assertEqual(MODULE.main(), 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            level = payload["levels"][1]
            self.assertEqual(level["id"], "ruins")
            self.assertNotIn("matcher", level)
            self.assertEqual(level["local_to_canonical"][0], [0.5, 0.0, 10.0])
            self.assertEqual(level["metadata"]["source"], "appsample")

    def test_rejects_weak_registration_without_writing_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, detail, registration = self._files(root, inliers=19)
            output = root / "candidate.json"
            argv = [
                str(SCRIPT), str(base), str(detail), str(registration), str(output),
                "--id", "ruins", "--source", "hoyolab", "--resolution-scale", "2",
            ]
            with patch.object(sys, "argv", argv):
                with self.assertRaisesRegex(ValueError, "need 20"):
                    MODULE.main()
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
