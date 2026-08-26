from __future__ import annotations

import unittest

from genshin_navigator.config import TrackerConfig
from genshin_navigator.matcher import LocateResult
from genshin_navigator.tracker import LiveTracker, TrackerState
from genshin_navigator.position import CoordinateSpace


def located(
    x: float,
    y: float,
    confidence: float = 0.9,
    inliers: int = 20,
    layer: str = "surface",
    method: str | None = None,
) -> LocateResult:
    return LocateResult(
        found=True,
        x_px=x,
        y_px=y,
        confidence=confidence,
        inliers=inliers,
        reference_id="test",
        map_layer_id=layer,
        region_id="fontaine",
        coordinate_space=(
            CoordinateSpace.SURFACE_ATLAS
            if layer == "surface"
            else CoordinateSpace.LAYER_LOCAL
        ),
        match_method=method,
    )


class LiveTrackerTests(unittest.TestCase):
    def test_plain_matcher_defaults_to_surface_position_contract(self) -> None:
        tracker = LiveTracker(TrackerConfig(acquire_hits=1))
        snapshot = tracker.update(
            LocateResult(found=True, x_px=12, y_px=34, confidence=0.9, inliers=20),
            1.0,
        )

        self.assertIsNotNone(snapshot.position)
        assert snapshot.position is not None
        self.assertEqual(snapshot.position.region_id, "unknown")
        self.assertEqual(snapshot.position.layer_id, "surface")
        self.assertIs(snapshot.position.coordinate_space, CoordinateSpace.SURFACE_ATLAS)

    def setUp(self) -> None:
        self.tracker = LiveTracker(
            TrackerConfig(
                acquire_hits=2,
                relocate_hits=3,
                max_speed_px_per_second=20,
                jump_margin_px=5,
                smoothing_alpha=0.5,
                lost_timeout_seconds=1.0,
            )
        )

    def test_acquires_after_two_consistent_observations(self) -> None:
        first = self.tracker.update(located(100, 100), 0.0)
        second = self.tracker.update(located(102, 100), 0.1)

        self.assertEqual(first.state, TrackerState.ACQUIRING)
        self.assertIsNone(first.map_layer_id)
        self.assertEqual(second.state, TrackerState.TRACKING)
        self.assertTrue(second.accepted)
        self.assertAlmostEqual(second.x_px or 0, 101)
        self.assertEqual(second.map_layer_id, "surface")

    def test_smooths_normal_movement(self) -> None:
        self.tracker.update(located(100, 100), 0.0)
        self.tracker.update(located(100, 100), 0.1)
        moved = self.tracker.update(located(106, 100), 0.2)

        self.assertEqual(moved.state, TrackerState.TRACKING)
        self.assertEqual(moved.x_px, 103.0)

    def test_absolute_fix_age_advances_only_during_relative_motion(self) -> None:
        self.tracker.update(located(100, 100), 0.0)
        acquired = self.tracker.update(located(100, 100), 0.1)
        relative = self.tracker.update(located(101, 100, method="motion"), 0.5)
        refreshed = self.tracker.update(
            located(102, 100, method="edge_correlation"), 0.7
        )

        self.assertEqual(acquired.absolute_fix_age_seconds, 0.0)
        self.assertEqual(relative.absolute_fix_age_seconds, 0.4)
        self.assertEqual(refreshed.absolute_fix_age_seconds, 0.0)

    def test_exposes_hint_only_after_position_is_confirmed(self) -> None:
        self.assertIsNone(self.tracker.position_hint)
        self.tracker.update(located(100, 100), 0.0)
        self.assertIsNone(self.tracker.position_hint)
        self.tracker.update(located(102, 100), 0.1)

        hint = self.tracker.position_hint
        self.assertIsNotNone(hint)
        assert hint is not None
        self.assertEqual(hint.region_id, "fontaine")
        self.assertEqual(hint.layer_id, "surface")
        self.assertEqual(hint.coordinate_space, CoordinateSpace.SURFACE_ATLAS)
        self.assertEqual((hint.x, hint.y), (101.0, 100.0))

    def test_requires_consistent_frames_before_relocation(self) -> None:
        self.tracker.update(located(100, 100), 0.0)
        self.tracker.update(located(100, 100), 0.1)
        first = self.tracker.update(located(500, 500), 0.2)
        second = self.tracker.update(located(502, 500), 0.3)
        third = self.tracker.update(located(501, 501), 0.4)

        self.assertEqual(first.state, TrackerState.RELOCATING)
        self.assertEqual(second.state, TrackerState.RELOCATING)
        self.assertEqual(third.state, TrackerState.TRACKING)
        self.assertTrue(third.accepted)
        self.assertAlmostEqual(third.x_px or 0, 501, delta=1)

    def test_becomes_lost_after_timeout(self) -> None:
        self.tracker.update(located(100, 100), 0.0)
        self.tracker.update(located(100, 100), 0.1)
        missing = LocateResult(found=False, reason="no_minimap")

        stale = self.tracker.update(missing, 0.5)
        lost = self.tracker.update(missing, 1.2)

        self.assertTrue(stale.stale)
        self.assertEqual(stale.state, TrackerState.TRACKING)
        self.assertEqual(lost.state, TrackerState.LOST)
        self.assertIsNone(lost.x_px)

    def test_rejects_low_confidence_result(self) -> None:
        result = self.tracker.update(located(100, 100, confidence=0.1), 0.0)
        self.assertEqual(result.state, TrackerState.LOST)

    def test_accepts_strong_template_evidence_with_fewer_sift_inliers(self) -> None:
        template = LocateResult(
            found=True,
            x_px=100,
            y_px=100,
            confidence=0.8,
            inliers=4,
            match_method="template",
            map_layer_id="underground:test",
        )
        first = self.tracker.update(template, 0.0)
        second = self.tracker.update(template, 0.1)
        self.assertEqual(first.state, TrackerState.ACQUIRING)
        self.assertEqual(second.state, TrackerState.TRACKING)

    def test_accepts_repeated_semantic_anchor_evidence(self) -> None:
        anchor = LocateResult(
            found=True,
            x_px=100,
            y_px=100,
            confidence=0.62,
            matches=1,
            inliers=1,
            match_method="anchors",
            map_layer_id="surface",
        )
        first = self.tracker.update(anchor, 0.0)
        second = self.tracker.update(anchor, 0.1)
        self.assertEqual(first.state, TrackerState.ACQUIRING)
        self.assertEqual(second.state, TrackerState.TRACKING)

    def test_pause_freezes_loss_timeout(self) -> None:
        self.tracker.update(located(100, 100), 0.0)
        self.tracker.update(located(100, 100), 0.1)

        paused = self.tracker.pause(10.0, "minimap_ui_not_detected")
        missing = self.tracker.update(LocateResult(found=False, reason="missing"), 10.2)

        self.assertEqual(paused.state, TrackerState.TRACKING)
        self.assertTrue(paused.stale)
        self.assertEqual(paused.map_layer_id, "surface")
        self.assertEqual(paused.reference_id, "test")
        self.assertEqual(paused.confidence, 0.9)
        self.assertEqual(missing.state, TrackerState.TRACKING)
        self.assertTrue(missing.stale)

    def test_single_nearby_result_on_another_floor_does_not_change_active_floor(self) -> None:
        floor_a = "underground:floor_a"
        floor_b = "underground:floor_b"
        self.tracker.update(located(100, 100, layer=floor_a), 0.0)
        self.tracker.update(located(100, 100, layer=floor_a), 0.1)

        first = self.tracker.update(located(101, 100, layer=floor_b), 0.2)
        second = self.tracker.update(located(101, 100, layer=floor_a), 0.3)

        self.assertEqual(first.state, TrackerState.RELOCATING)
        self.assertFalse(first.accepted)
        self.assertEqual(first.map_layer_id, floor_a)
        self.assertEqual(second.state, TrackerState.TRACKING)
        self.assertTrue(second.accepted)
        self.assertEqual(second.map_layer_id, floor_a)

    def test_initial_acquisition_cannot_mix_two_floors(self) -> None:
        first = self.tracker.update(
            located(100, 100, layer="underground:floor_a"), 0.0
        )
        second = self.tracker.update(
            located(100, 100, layer="underground:floor_b"), 0.1
        )

        self.assertEqual(first.state, TrackerState.ACQUIRING)
        self.assertEqual(second.state, TrackerState.ACQUIRING)
        self.assertFalse(second.accepted)


if __name__ == "__main__":
    unittest.main()
