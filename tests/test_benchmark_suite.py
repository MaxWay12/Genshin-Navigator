from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from genshin_navigator.benchmark_suite import run_benchmark_suite
from test_scenario import FakeLocator, app_config


def scenario_report(*, false_locks: int = 0, jitter: float | None = 1.0):
    return {
        "name": "synthetic",
        "metrics": {
            "false_locks": false_locks,
            "layer_samples": 4,
            "layer_accuracy": 1.0,
            "acquisition_delays_seconds": [0.2, 1.5],
            "reacquisition_p95_seconds": 1.5,
            "required_tracking_coverage": 1.0,
            "longest_untracked_streak_seconds": 0.0,
            "position_checkpoint_count": 2,
            "one_frame_layer_runs": 0,
            "stationary_jitter_p95_px": jitter,
        },
    }


class BenchmarkSuiteTests(unittest.TestCase):
    def _manifest(self, root: Path) -> Path:
        path = root / "suite.json"
        path.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "name": "test suite",
                    "scenarios": [
                        {"name": "gate", "path": "gate", "gating": True},
                        {"name": "info", "path": "info", "gating": False},
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_informational_failure_does_not_fail_suite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            reports = [scenario_report(), scenario_report(false_locks=1)]
            with patch(
                "genshin_navigator.benchmark_suite.evaluate_scenario",
                side_effect=reports,
            ):
                report = run_benchmark_suite(
                    self._manifest(Path(temporary)), app_config(), FakeLocator()
                )
            self.assertTrue(report["passed"])
            self.assertFalse(report["scenarios"][1]["passed"])
            self.assertEqual(report["scenarios"][1]["failures"], ["false_locks"])

    def test_gating_failure_fails_suite_and_records_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            reports = [scenario_report(jitter=5.1), scenario_report()]
            with patch(
                "genshin_navigator.benchmark_suite.evaluate_scenario",
                side_effect=reports,
            ):
                report = run_benchmark_suite(
                    self._manifest(Path(temporary)),
                    app_config(),
                    FakeLocator(),
                    ui_scale="1.0",
                    graphics_preset="high",
                )
            self.assertFalse(report["passed"])
            self.assertEqual(report["scenarios"][0]["failures"], ["stationary_jitter_p95_px"])
            self.assertEqual(report["compatibility"]["format_version"], 1)
            self.assertEqual(report["compatibility"]["genshin_ui_scale"], "1.0")
            self.assertEqual(report["compatibility"]["graphics_preset"], "high")

    def test_new_coverage_and_checkpoint_kpis_are_optional_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self._manifest(Path(temporary))
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["kpis"] = {
                "min_tracking_coverage": 0.95,
                "min_position_checkpoints": 3,
            }
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            weak = scenario_report()
            weak["metrics"]["required_tracking_coverage"] = 0.94
            with patch(
                "genshin_navigator.benchmark_suite.evaluate_scenario",
                side_effect=[weak, scenario_report()],
            ):
                report = run_benchmark_suite(manifest, app_config(), FakeLocator())
            self.assertFalse(report["passed"])
            self.assertEqual(
                report["scenarios"][0]["failures"],
                ["tracking_coverage", "position_checkpoints"],
            )


if __name__ == "__main__":
    unittest.main()
