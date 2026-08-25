from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from genshin_navigator.config import FailureRecorderConfig
from genshin_navigator.failure_recorder import DiagnosticContext, FailureRecorder
from genshin_navigator.matcher import CandidateMatch, LocateResult
from genshin_navigator.tracker import TrackerSnapshot, TrackerState
from genshin_navigator.position import CoordinateSpace, MapPosition


def snapshot(
    state: TrackerState,
    *,
    x: float | None = None,
    y: float | None = None,
    layer: str = "surface",
) -> TrackerSnapshot:
    position = (
        MapPosition(
            region_id="fontaine",
            layer_id=layer,
            coordinate_space=(
                CoordinateSpace.SURFACE_ATLAS
                if layer == "surface"
                else CoordinateSpace.LAYER_LOCAL
            ),
            x=x,
            y=y,
            confidence=0.9,
            state=state,
            timestamp=0.0,
            reference_id=f"test:{layer}",
        )
        if x is not None and y is not None
        else None
    )
    return TrackerSnapshot(
        state=state,
        x_px=x,
        y_px=y,
        raw_x_px=x,
        raw_y_px=y,
        confidence=0.9 if x is not None else 0.0,
        reference_id=f"test:{layer}" if x is not None else None,
        map_layer_id=layer if x is not None else None,
        accepted=x is not None,
        stale=False,
        reason=None if x is not None else "no_pyramid_level_matched",
        position=position,
    )


class FailureRecorderTests(unittest.TestCase):
    def test_records_pre_and_post_minimap_frames_on_real_track_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "failures"
            recorder = FailureRecorder(
                FailureRecorderConfig(
                    enabled=True,
                    output_dir=output,
                    pre_frames=3,
                    post_frames=2,
                    cooldown_seconds=0,
                )
            )
            minimap = np.full((12, 14, 3), 80, dtype=np.uint8)
            found = LocateResult(
                found=True,
                x_px=100,
                y_px=200,
                confidence=0.9,
                inliers=20,
                reference_id="test",
            )
            missing = LocateResult(found=False, reason="no_pyramid_level_matched")

            recorder.observe(minimap, missing, snapshot(TrackerState.LOST), 0.0)
            recorder.observe(minimap, found, snapshot(TrackerState.TRACKING, x=100, y=200), 1.0)
            recorder.observe(minimap, found, snapshot(TrackerState.TRACKING, x=101, y=201), 2.0)
            self.assertIsNone(
                recorder.observe(minimap, missing, snapshot(TrackerState.LOST), 3.0)
            )
            self.assertIsNone(
                recorder.observe(minimap, missing, snapshot(TrackerState.LOST), 4.0)
            )
            incident = recorder.observe(
                minimap, missing, snapshot(TrackerState.LOST), 5.0
            )

            self.assertIsNotNone(incident)
            assert incident is not None
            metadata = json.loads((incident / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["trigger_frame_index"], 2)
            self.assertEqual(metadata["trigger"], "established_track_became_lost")
            self.assertEqual(len(metadata["frames"]), 5)
            self.assertEqual(metadata["last_known_position"]["x"], 101)
            self.assertEqual(metadata["last_known_position"]["y"], 201)
            self.assertEqual(metadata["last_known_position"]["region_id"], "fontaine")
            stored = cv2.imread(str(incident / "minimap_000.png"))
            self.assertEqual(stored.shape, minimap.shape)
            self.assertEqual(metadata["format_version"], 4)

    def test_manual_report_is_anonymized_and_contains_ranked_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = FailureRecorder(
                FailureRecorderConfig(
                    enabled=False,
                    output_dir=Path(temporary),
                    pre_frames=2,
                    post_frames=1,
                ),
                DiagnosticContext(
                    app_version="0.1.0",
                    schema_version=3,
                    content_version="safe-content",
                    reference_versions=("surface", "floor78"),
                    windows_build="test-build",
                    screen_resolution=(1920, 1080),
                    dpi=96,
                ),
            )
            minimap = np.zeros((8, 8, 3), dtype=np.uint8)
            located = LocateResult(
                found=True,
                x_px=2,
                y_px=3,
                candidates=(CandidateMatch("surface", "surface", 0.9, 20, 16, None, True),),
            )
            tracked = snapshot(TrackerState.TRACKING, x=2, y=3)
            recorder.observe(minimap, located, tracked, 1.0)
            self.assertTrue(recorder.request_manual_report())
            recorder.observe(minimap, located, tracked, 2.0)
            incident = recorder.observe(minimap, located, tracked, 3.0)
            self.assertIsNotNone(incident)
            assert incident is not None
            serialized = (incident / "metadata.json").read_text(encoding="utf-8")
            metadata = json.loads(serialized)
            self.assertEqual(metadata["trigger"], "manual_report")
            self.assertEqual(metadata["environment"]["schema_version"], 3)
            self.assertEqual(
                metadata["frames"][0]["localization"]["candidates"][0]["reference_id"],
                "surface",
            )
            for secret in ("1816430870", "cookie", "authorization", "C:\\Users\\maks-"):
                self.assertNotIn(secret.lower(), serialized.lower())

    def test_close_flushes_partial_incident(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = FailureRecorder(
                FailureRecorderConfig(
                    enabled=True,
                    output_dir=Path(temporary),
                    pre_frames=2,
                    post_frames=3,
                    cooldown_seconds=0,
                )
            )
            minimap = np.zeros((8, 8, 3), dtype=np.uint8)
            found = LocateResult(found=True, x_px=1, y_px=2, confidence=1, inliers=20)
            missing = LocateResult(found=False, reason="missing")
            recorder.observe(minimap, found, snapshot(TrackerState.TRACKING, x=1, y=2), 0.0)
            recorder.observe(minimap, missing, snapshot(TrackerState.LOST), 1.0)

            incident = recorder.close()

            self.assertIsNotNone(incident)
            assert incident is not None
            metadata = json.loads((incident / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(len(metadata["frames"]), 2)

    def test_records_sustained_failure_to_acquire_initial_position(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = FailureRecorder(
                FailureRecorderConfig(
                    enabled=True,
                    output_dir=Path(temporary),
                    pre_frames=4,
                    post_frames=0,
                    cooldown_seconds=0,
                    acquisition_timeout_seconds=2.0,
                )
            )
            minimap = np.zeros((8, 8, 3), dtype=np.uint8)
            missing = LocateResult(found=False, reason="no_pyramid_level_matched")

            self.assertIsNone(
                recorder.observe(minimap, missing, snapshot(TrackerState.LOST), 10.0)
            )
            self.assertIsNone(
                recorder.observe(minimap, missing, snapshot(TrackerState.LOST), 11.0)
            )
            incident = recorder.observe(
                minimap, missing, snapshot(TrackerState.LOST), 12.1
            )

            self.assertIsNotNone(incident)
            assert incident is not None
            metadata = json.loads((incident / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["trigger"], "localization_acquisition_timed_out")
            self.assertEqual(metadata["trigger_frame_index"], 2)
            self.assertIsNone(metadata["last_known_position"])
            self.assertEqual(len(metadata["frames"]), 3)

    def test_short_initial_search_does_not_create_incident(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = FailureRecorder(
                FailureRecorderConfig(
                    enabled=True,
                    output_dir=Path(temporary),
                    pre_frames=4,
                    post_frames=0,
                    cooldown_seconds=0,
                    acquisition_timeout_seconds=2.0,
                )
            )
            minimap = np.zeros((8, 8, 3), dtype=np.uint8)
            missing = LocateResult(found=False, reason="missing")
            found = LocateResult(found=True, x_px=1, y_px=2, confidence=1, inliers=20)

            recorder.observe(minimap, missing, snapshot(TrackerState.LOST), 0.0)
            recorder.observe(minimap, missing, snapshot(TrackerState.LOST), 1.0)
            recorder.observe(
                minimap, found, snapshot(TrackerState.TRACKING, x=1, y=2), 1.5
            )

            self.assertIsNone(recorder.close())
            self.assertFalse(any(Path(temporary).iterdir()))

    def test_confirmed_map_layer_transition_is_not_saved_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            recorder = FailureRecorder(
                FailureRecorderConfig(
                    enabled=True,
                    output_dir=output,
                    pre_frames=3,
                    post_frames=2,
                    cooldown_seconds=0,
                )
            )
            minimap = np.zeros((8, 8, 3), dtype=np.uint8)
            found_a = LocateResult(
                found=True,
                x_px=1,
                y_px=2,
                confidence=1,
                inliers=20,
                map_layer_id="underground:floor_a",
            )
            found_b = LocateResult(
                found=True,
                x_px=3,
                y_px=4,
                confidence=1,
                inliers=20,
                map_layer_id="underground:floor_b",
            )
            missing = LocateResult(found=False, reason="missing")

            recorder.observe(
                minimap,
                found_a,
                snapshot(TrackerState.TRACKING, x=1, y=2, layer="underground:floor_a"),
                0.0,
            )
            recorder.observe(minimap, missing, snapshot(TrackerState.LOST), 1.0)
            recorder.observe(
                minimap,
                found_b,
                snapshot(TrackerState.ACQUIRING, layer="underground:floor_b"),
                2.0,
            )
            incident = recorder.observe(
                minimap,
                found_b,
                snapshot(TrackerState.TRACKING, x=3, y=4, layer="underground:floor_b"),
                3.0,
            )

            self.assertIsNone(incident)
            self.assertFalse(recorder.active)
            self.assertFalse(any(output.iterdir()))


if __name__ == "__main__":
    unittest.main()
