import threading
import time
import unittest
from unittest.mock import Mock
from dataclasses import replace

import numpy as np

from genshin_navigator.config import AppConfig, Roi
from genshin_navigator.live_worker import LocalizationWorker, LiveResult, visible_snapshot
from genshin_navigator.matcher import LocateResult
from genshin_navigator.performance import PerformanceMonitor
from genshin_navigator.tracker import LiveTracker


class LiveWorkerTests(unittest.TestCase):
    def test_slow_result_is_discarded_after_pause_and_close_is_bounded(self):
        entered, release = threading.Event(), threading.Event()
        def locate(frame):
            entered.set()
            release.wait(3)
            return LocateResult(found=False)
        config = AppConfig(None, None, None, Roi(0, 0, 216, 216))
        capture = Mock(return_value=np.zeros((216, 216, 3), np.uint8))
        recorder = Mock()
        worker = LocalizationWorker(config, Mock(locate=locate), None, recorder, capture=capture)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            started = time.perf_counter()
            worker.set_paused(True)
            for _ in range(20):
                self.assertIsNone(worker.latest())
            self.assertLess(time.perf_counter() - started, 0.1)
            self.assertEqual(capture.call_count, 1)
            release.set()
            worker.close()
            self.assertIsNone(worker.latest())
            recorder.observe.assert_not_called()
        finally:
            release.set()
            worker.close()

    def test_expired_snapshot_cannot_be_navigated(self):
        snapshot = LiveTracker().pause(1, "starting")
        result = LiveResult(replace(snapshot, stale=False), 1, PerformanceMonitor("balanced").snapshot)
        self.assertFalse(visible_snapshot(result, 1.2, 1.5).stale)
        expired = visible_snapshot(result, 3, 1.5)
        self.assertTrue(expired.stale)
        self.assertIsNone(expired.position)
        self.assertEqual(expired.reason, "capture_expired")
