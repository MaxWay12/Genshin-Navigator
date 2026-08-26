from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from genshin_navigator.scenario import load_scenario
from genshin_navigator.scenario_annotation import AtlasViewport, ScenarioAnnotation


class ScenarioAnnotationTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path]:
        frames = root / "frames"
        frames.mkdir()
        cv2.imwrite(str(frames / "frame.png"), np.full((8, 8, 3), 80, np.uint8))
        atlas = root / "atlas.png"
        cv2.imwrite(str(atlas), np.full((100, 200, 3), 120, np.uint8))
        (root / "scenario.json").write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "expectations": [],
                    "frames": [
                        {"image": "frames/frame.png", "timestamp_seconds": 0.0}
                    ],
                }
            ),
            encoding="utf-8",
        )
        return root, atlas

    def test_sets_replaces_removes_and_atomically_saves_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, atlas = self._fixture(Path(temporary))
            annotation = ScenarioAnnotation(
                root, atlas, region_id="sumeru_desert", tolerance_px=35
            )
            annotation.set_checkpoint(50, 40)
            annotation.set_checkpoint(60, 45)
            self.assertEqual(len(annotation.checkpoints), 1)
            annotation.save()
            _, payload = load_scenario(root)
            checkpoint = payload["checkpoints"][0]
            self.assertEqual(checkpoint["region_id"], "sumeru_desert")
            self.assertEqual(checkpoint["position"]["x"], 60.0)
            self.assertNotIn(str(root), json.dumps(payload))
            self.assertTrue(annotation.remove_checkpoint())

    def test_failed_replace_preserves_original_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, atlas = self._fixture(Path(temporary))
            original = (root / "scenario.json").read_bytes()
            annotation = ScenarioAnnotation(root, atlas, region_id="sumeru_desert")
            annotation.set_checkpoint(10, 10)
            with patch(
                "genshin_navigator.scenario_annotation.os.replace",
                side_effect=OSError("interrupted"),
            ):
                with self.assertRaises(OSError):
                    annotation.save()
            self.assertEqual((root / "scenario.json").read_bytes(), original)

    def test_viewport_round_trip_and_outside_click(self) -> None:
        viewport = AtlasViewport(100, 50, 400, 200, 800, 400)
        self.assertEqual(viewport.to_atlas(300, 150), (400.0, 200.0))
        self.assertEqual(viewport.to_canvas(400, 200), (300, 150))
        self.assertIsNone(viewport.to_atlas(50, 50))

    def test_loader_rejects_checkpoint_outside_recording(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, _atlas = self._fixture(Path(temporary))
            payload = json.loads((root / "scenario.json").read_text(encoding="utf-8"))
            payload["checkpoints"] = [
                {
                    "timestamp_seconds": 1.0,
                    "region_id": "sumeru_desert",
                    "layer_id": "surface",
                    "position": {"x": 1, "y": 2, "tolerance_px": 35},
                }
            ]
            (root / "scenario.json").write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside the recording"):
                load_scenario(root)


if __name__ == "__main__":
    unittest.main()
