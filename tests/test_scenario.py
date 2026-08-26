from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from genshin_navigator.config import (
    AppConfig,
    Roi,
    ScreenGateConfig,
    TrackerConfig,
)
from genshin_navigator.matcher import LocateResult
from genshin_navigator.position import CoordinateSpace
from genshin_navigator.scenario import evaluate_scenario, load_scenario, record_scenario
from genshin_navigator.screen_gate import ScreenGateResult


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class FakeGate:
    def check(self, minimap: np.ndarray) -> ScreenGateResult:
        if int(round(float(minimap.mean()))) == 2:
            return ScreenGateResult(False, 0.0, "loading_or_blank_screen")
        return ScreenGateResult(True, 1.0)


class FakeLocator:
    def locate(self, minimap: np.ndarray) -> LocateResult:
        index = int(round(float(minimap.mean())))
        if index <= 1:
            layer = "surface"
            x = 100.0 + index
        else:
            layer = "underground:floor_b"
            x = 500.0 + index
        return LocateResult(
            found=True,
            x_px=x,
            y_px=100.0,
            confidence=0.95,
            matches=40,
            inliers=35,
            reference_id=f"reference-{layer}",
            map_layer_id=layer,
            region_id="fontaine",
            coordinate_space=(
                CoordinateSpace.SURFACE_ATLAS
                if layer == "surface"
                else CoordinateSpace.LAYER_LOCAL
            ),
        )


def app_config() -> AppConfig:
    return AppConfig(
        map_path=Path("unused.png"),
        pyramid_path=None,
        debug_map_path=None,
        roi=Roi(left=2, top=3, width=4, height=4),
        interval_seconds=0.1,
        tracker=TrackerConfig(
            acquire_hits=2,
            relocate_hits=3,
            max_speed_px_per_second=20,
            jump_margin_px=5,
            smoothing_alpha=0.5,
            lost_timeout_seconds=1.0,
        ),
        screen_gate=ScreenGateConfig(enabled=False),
    )


class ScenarioTests(unittest.TestCase):
    def test_recorder_stores_only_the_minimap_crop_and_monotonic_time(self) -> None:
        clock = FakeClock()
        desktop = np.zeros((12, 12, 3), dtype=np.uint8)
        desktop[3:7, 2:6] = 123
        phases: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = record_scenario(
                app_config(),
                Path(temporary) / "surface_walk",
                0.2,
                name="surface walk",
                expected_start_layer="surface",
                expected_end_layer="surface",
                stationary_last_seconds=0.1,
                capture_screen=lambda: desktop.copy(),
                clock=clock,
                sleeper=clock.sleep,
                phase_notifier=phases.append,
            )
            root, manifest = load_scenario(manifest_path.parent)

            self.assertEqual(manifest["format_version"], 1)
            self.assertIn("full game frames are never written", manifest["privacy"])
            self.assertEqual(manifest["compatibility"]["genshin_ui_scale"], "unknown")
            timestamps = [item["timestamp_seconds"] for item in manifest["frames"]]
            self.assertEqual(timestamps, [0.0, 0.1, 0.2])
            saved = cv2.imread(str(root / manifest["frames"][0]["image"]))
            self.assertEqual(saved.shape[:2], (4, 4))
            self.assertEqual(len(list(root.rglob("*.png"))), 3)
            self.assertEqual(phases, ["started", "stationary", "finished"])

    def test_loader_rejects_non_monotonic_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames = root / "frames"
            frames.mkdir()
            cv2.imwrite(str(frames / "a.png"), np.zeros((4, 4, 3), dtype=np.uint8))
            cv2.imwrite(str(frames / "b.png"), np.zeros((4, 4, 3), dtype=np.uint8))
            (root / "scenario.json").write_text(
                json.dumps(
                    {
                        "format_version": 1,
                        "expectations": [],
                        "frames": [
                            {"image": "frames/a.png", "timestamp_seconds": 0.1},
                            {"image": "frames/b.png", "timestamp_seconds": 0.1},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "strictly increasing"):
                load_scenario(root)

    def test_replay_pauses_reacquires_and_confirms_layer_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames_dir = root / "frames"
            frames_dir.mkdir()
            frames = []
            for index, timestamp in enumerate((0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)):
                image = np.full((4, 4, 3), index, dtype=np.uint8)
                name = f"frame_{index}.png"
                cv2.imwrite(str(frames_dir / name), image)
                frames.append({"image": f"frames/{name}", "timestamp_seconds": timestamp})
            (root / "scenario.json").write_text(
                json.dumps(
                    {
                        "format_version": 1,
                        "name": "surface to underground",
                        "expectations": [
                            {
                                "name": "start",
                                "start_seconds": 0.1,
                                "end_seconds": 0.1,
                                "tracking": "required",
                                "region_id": "fontaine",
                                "layer_id": "surface",
                            },
                            {
                                "name": "end",
                                "start_seconds": 0.5,
                                "end_seconds": 0.6,
                                "tracking": "required",
                                "region_id": "fontaine",
                                "layer_id": "underground:floor_b",
                                "stationary_from_seconds": 0.5,
                            },
                        ],
                        "checkpoints": [
                            {
                                "timestamp_seconds": 0.1,
                                "region_id": "fontaine",
                                "layer_id": "surface",
                                "position": {"x": 100.0, "y": 100.0, "tolerance_px": 10.0},
                            },
                            {
                                "timestamp_seconds": 0.5,
                                "region_id": "fontaine",
                                "layer_id": "underground:floor_b",
                                "position": {"x": 505.0, "y": 100.0, "tolerance_px": 10.0},
                            },
                        ],
                        "frames": frames,
                    }
                ),
                encoding="utf-8",
            )

            report = evaluate_scenario(
                root,
                app_config(),
                FakeLocator(),
                screen_gate=FakeGate(),  # type: ignore[arg-type]
            )

            self.assertTrue(report["passed"])
            metrics = report["metrics"]
            self.assertEqual(metrics["false_locks"], 0)
            self.assertEqual(metrics["one_frame_layer_runs"], 0)
            self.assertEqual(metrics["layer_accuracy"], 1.0)
            self.assertEqual(metrics["position_checkpoint_count"], 2)
            self.assertEqual(metrics["position_checkpoint_tracking_samples"], 2)
            self.assertEqual(metrics["acquisition_delays_seconds"], [0.1, 0.2])
            rows = report["frames"]
            self.assertEqual(rows[2]["tracker"]["state"], "TRACKING")
            self.assertTrue(rows[2]["tracker"]["stale"])
            self.assertEqual(rows[3]["tracker"]["state"], "RELOCATING")
            self.assertEqual(rows[3]["tracker"]["map_layer_id"], "surface")
            self.assertEqual(rows[5]["tracker"]["position"]["layer_id"], "underground:floor_b")
            self.assertEqual(rows[5]["tracker"]["position"]["schema_version"], 1)

    def test_hidden_minimap_time_is_not_counted_as_lost_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames_dir = root / "frames"
            frames_dir.mkdir()
            frames = []
            for index, value in enumerate((2, 0, 0)):
                name = f"frame_{index}.png"
                cv2.imwrite(
                    str(frames_dir / name),
                    np.full((4, 4, 3), value, dtype=np.uint8),
                )
                frames.append(
                    {"image": f"frames/{name}", "timestamp_seconds": index * 0.1}
                )
            (root / "scenario.json").write_text(
                json.dumps(
                    {
                        "format_version": 1,
                        "expectations": [],
                        "frames": frames,
                    }
                ),
                encoding="utf-8",
            )

            report = evaluate_scenario(
                root,
                app_config(),
                FakeLocator(),
                screen_gate=FakeGate(),  # type: ignore[arg-type]
            )

            self.assertEqual(report["metrics"]["lost_duration_seconds"], 0.0)


if __name__ == "__main__":
    unittest.main()
