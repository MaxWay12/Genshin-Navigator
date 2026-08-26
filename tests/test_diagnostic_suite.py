from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from genshin_navigator.diagnostic_suite import run_diagnostic_suite


class DiagnosticSuiteTests(unittest.TestCase):
    def test_aggregates_recovered_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "suite.json"
            manifest.write_text(
                json.dumps(
                    {
                        "format_version": 1,
                        "name": "live recovery",
                        "criteria": {
                            "require_recovery": True,
                            "max_recovery_seconds": 1.0,
                        },
                        "bundles": ["one", {"path": "two", "name": "second"}],
                    }
                ),
                encoding="utf-8",
            )
            reports = [
                {
                    "outcome": "transient_recovered",
                    "recovery_seconds": 0.5,
                    "recovery_method": "motion",
                    "recovery_reference_id": "sumeru",
                    "frame_count": 16,
                },
                {
                    "outcome": "transient_recovered",
                    "recovery_seconds": 0.8,
                    "recovery_method": "edge_correlation",
                    "recovery_reference_id": "sumeru",
                    "frame_count": 16,
                },
            ]
            with patch(
                "genshin_navigator.diagnostic_suite.replay_diagnostic",
                side_effect=reports,
            ):
                result = run_diagnostic_suite(manifest, object(), object())

            self.assertTrue(result["passed"])
            self.assertEqual(result["passed_count"], 2)
            self.assertEqual(result["gating_count"], 2)
            self.assertEqual(result["scope"], "recovery_only_no_positional_ground_truth")

    def test_fails_unresolved_or_slow_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "suite.json"
            manifest.write_text(
                json.dumps(
                    {
                        "format_version": 1,
                        "criteria": {"max_recovery_seconds": 1.0},
                        "bundles": ["unresolved", "slow"],
                    }
                ),
                encoding="utf-8",
            )
            reports = [
                {
                    "outcome": "unresolved",
                    "recovery_seconds": None,
                    "frame_count": 10,
                },
                {
                    "outcome": "transient_recovered",
                    "recovery_seconds": 1.2,
                    "frame_count": 10,
                },
            ]
            with patch(
                "genshin_navigator.diagnostic_suite.replay_diagnostic",
                side_effect=reports,
            ):
                result = run_diagnostic_suite(manifest, object(), object())

            self.assertFalse(result["passed"])
            self.assertEqual(result["failed_count"], 2)
            self.assertEqual(result["results"][0]["reasons"], ["did_not_recover"])
            self.assertEqual(result["results"][1]["reasons"], ["recovery_too_slow"])

    def test_rejects_empty_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "suite.json"
            manifest.write_text(
                json.dumps({"format_version": 1, "bundles": []}), encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                run_diagnostic_suite(manifest, object(), object())

    def test_informational_unresolved_bundle_does_not_fail_suite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "suite.json"
            manifest.write_text(
                json.dumps(
                    {
                        "format_version": 1,
                        "bundles": [{"path": "short", "gating": False}],
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "genshin_navigator.diagnostic_suite.replay_diagnostic",
                return_value={
                    "outcome": "unresolved",
                    "recovery_seconds": None,
                    "frame_count": 8,
                },
            ):
                result = run_diagnostic_suite(manifest, object(), object())
            self.assertTrue(result["passed"])
            self.assertEqual(result["gating_count"], 0)
            self.assertEqual(result["informational_count"], 1)


if __name__ == "__main__":
    unittest.main()
