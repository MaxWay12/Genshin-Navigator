from __future__ import annotations

import unittest
from unittest.mock import patch

from genshin_navigator.benchmark_compare import compare_benchmark_suites
from test_scenario import FakeLocator, app_config


def suite(passed: bool, coverage: float) -> dict[str, object]:
    return {
        "passed": passed,
        "scenarios": [
            {
                "name": "ruins",
                "passed": passed,
                "metrics": {
                    "required_tracking_coverage": coverage,
                    "reacquisition_p95_seconds": 1.0,
                    "longest_untracked_streak_seconds": 0.5,
                    "stationary_jitter_p95_px": 1.0,
                    "mean_processing_ms": 10.0,
                    "p95_processing_ms": 12.0,
                },
            }
        ],
    }


class BenchmarkCompareTests(unittest.TestCase):
    def test_accepts_only_candidate_with_passing_regression(self) -> None:
        with patch(
            "genshin_navigator.benchmark_compare.run_benchmark_suite",
            side_effect=[suite(False, 0.7), suite(True, 0.98), suite(True, 1.0)],
        ):
            report = compare_benchmark_suites(
                "suite.json",
                app_config(),
                FakeLocator(),
                app_config(),
                FakeLocator(),
                regression=("fontaine.json", app_config(), FakeLocator()),
            )
        self.assertTrue(report["accepted"])
        self.assertEqual(report["verdict"], "detail_reference_accepted")
        self.assertEqual(
            report["comparisons"][0]["deltas"]["required_tracking_coverage"],
            0.28,
        )

    def test_passing_candidate_without_regression_is_pending(self) -> None:
        with patch(
            "genshin_navigator.benchmark_compare.run_benchmark_suite",
            side_effect=[suite(True, 0.96), suite(True, 0.98)],
        ):
            report = compare_benchmark_suites(
                "suite.json", app_config(), FakeLocator(), app_config(), FakeLocator()
            )
        self.assertFalse(report["accepted"])
        self.assertEqual(report["verdict"], "candidate_passed_regression_not_run")


if __name__ == "__main__":
    unittest.main()
