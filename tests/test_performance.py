from __future__ import annotations

import unittest

from genshin_navigator.config import PerformanceConfig
from genshin_navigator.performance import LocalizationScheduler, PerformanceMonitor
from genshin_navigator.position import PositionState


class PerformanceTests(unittest.TestCase):
    def test_balanced_scheduler_runs_tracking_less_often_than_ui(self) -> None:
        scheduler = LocalizationScheduler(PerformanceConfig(mode="balanced"))
        self.assertTrue(scheduler.due(1.0, PositionState.TRACKING))
        scheduler.mark_run(1.0)
        self.assertFalse(scheduler.due(1.1, PositionState.TRACKING))
        self.assertTrue(scheduler.due(1.16, PositionState.TRACKING))

    def test_lost_search_uses_slower_global_interval_and_can_be_forced(self) -> None:
        scheduler = LocalizationScheduler(PerformanceConfig(mode="balanced"))
        scheduler.mark_run(2.0)
        self.assertFalse(scheduler.due(2.3, PositionState.LOST))
        self.assertTrue(scheduler.due(2.35, PositionState.LOST))
        scheduler.mark_run(2.35)
        scheduler.force_next()
        self.assertTrue(scheduler.due(2.36, PositionState.LOST))

    def test_repeated_global_misses_back_off_without_exceeding_cap(self) -> None:
        scheduler = LocalizationScheduler(
            PerformanceConfig(
                global_search_interval_seconds=0.2,
                global_search_max_interval_seconds=0.5,
            )
        )
        for _ in range(20):
            scheduler.observe_result(found=False, state=PositionState.LOST)
        self.assertEqual(scheduler.interval_for(PositionState.LOST), 0.5)
        scheduler.observe_result(found=True, state=PositionState.ACQUIRING)
        self.assertEqual(scheduler.interval_for(PositionState.LOST), 0.2)

    def test_monitor_reports_actual_cv_rate_and_p95(self) -> None:
        monitor = PerformanceMonitor("low_cpu")
        monitor.record(1.0, 10.0, "local")
        monitor.record(1.5, 20.0, "global")
        snapshot = monitor.snapshot
        self.assertEqual(snapshot.mode, "low_cpu")
        self.assertEqual(snapshot.search_mode, "global")
        self.assertEqual(snapshot.cv_fps, 2.0)
        self.assertGreaterEqual(snapshot.processing_p95_ms or 0, 19.0)


if __name__ == "__main__":
    unittest.main()
