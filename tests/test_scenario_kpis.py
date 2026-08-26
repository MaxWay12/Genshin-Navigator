from __future__ import annotations

import unittest

from genshin_navigator.scenario_kpis import DEFAULT_KPIS, evaluate_kpis


def metrics(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "false_locks": 0,
        "layer_samples": 10,
        "layer_accuracy": 1.0,
        "required_tracking_coverage": 0.95,
        "longest_untracked_streak_seconds": 2.0,
        "acquisition_delays_seconds": [0.5, 3.0],
        "reacquisition_p95_seconds": 3.0,
        "one_frame_layer_runs": 0,
        "stationary_jitter_p95_px": 5.0,
        "position_checkpoint_count": 2,
    }
    payload.update(updates)
    return payload


class ScenarioKpiTests(unittest.TestCase):
    def test_boundary_values_pass(self) -> None:
        kpis = {**DEFAULT_KPIS, "min_position_checkpoints": 2}
        self.assertEqual(evaluate_kpis(metrics(), kpis), [])

    def test_coverage_streak_reacquire_and_missing_transition_fail_separately(self) -> None:
        kpis = {**DEFAULT_KPIS, "min_position_checkpoints": 2}
        self.assertEqual(
            evaluate_kpis(
                metrics(
                    required_tracking_coverage=0.949,
                    longest_untracked_streak_seconds=2.01,
                    acquisition_delays_seconds=[0.5, None],
                    reacquisition_p95_seconds=0.5,
                ),
                kpis,
            ),
            [
                "tracking_coverage",
                "longest_untracked_streak_seconds",
                "reacquire_p95_seconds",
            ],
        )


if __name__ == "__main__":
    unittest.main()
