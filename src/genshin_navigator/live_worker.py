"""Single-owner CV pipeline with a bounded latest-result mailbox."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace

from .capture import grab_roi
from .matcher import LocateResult
from .performance import LocalizationScheduler, PerformanceMonitor, PerformanceSnapshot
from .position import PositionState
from .pyramid import PyramidMatcher
from .tracker import LiveTracker, TrackerSnapshot


@dataclass(frozen=True)
class LiveResult:
    snapshot: TrackerSnapshot
    captured_at: float
    performance: PerformanceSnapshot
    incident: object = None


def visible_snapshot(result: LiveResult, now: float, max_age: float) -> TrackerSnapshot:
    if now - result.captured_at > max_age:
        return replace(result.snapshot, state=PositionState.LOST, position=None,
                       accepted=False, stale=True, reason="capture_expired")
    return result.snapshot


class LocalizationWorker:
    def __init__(self, config, locator, gate, recorder, *, capture=grab_roi):
        self.config, self.locator, self.gate, self.recorder = config, locator, gate, recorder
        self.capture = capture
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._manual = threading.Event()
        self._generation = 0
        self._paused = False
        self._latest = None
        self._error = None
        self._thread = threading.Thread(target=self._run, name="navigator-localization", daemon=True)

    def start(self):
        self._thread.start()

    def set_paused(self, paused):
        with self._lock:
            if paused != self._paused:
                self._paused = paused
                self._generation += 1
                self._latest = None

    def request_report(self):
        self._manual.set()
        return True

    def latest(self):
        with self._lock:
            if self._error is not None:
                raise RuntimeError("Localization worker failed") from self._error
            return self._latest

    def close(self):
        self._stop.set()
        with self._lock:
            self._generation += 1
            self._latest = None
        # OpenCV cannot be interrupted safely. An in-flight result is discarded;
        # this daemon never owns UI or SQLite, and cannot delay process exit.
        self._thread.join(timeout=0.2)

    def _run(self):
        generation = -1
        performance = PerformanceMonitor(self.config.performance.mode)
        try:
            while not self._stop.is_set():
                with self._lock:
                    current, paused = self._generation, self._paused
                if paused:
                    self._stop.wait(0.05)
                    continue
                if current != generation:
                    tracker = LiveTracker(self.config.tracker)
                    scheduler = LocalizationScheduler(self.config.performance)
                    if isinstance(self.locator, PyramidMatcher):
                        self.locator.reset_continuity()
                    generation = current
                if not scheduler.due(time.perf_counter(), tracker.state):
                    self._stop.wait(0.01)
                    continue
                started = time.perf_counter()
                minimap = self.capture(self.config.roi)
                captured = time.perf_counter()
                scheduler.mark_run(captured)
                gate_result = self.gate.check(minimap) if self.gate else None
                if gate_result is not None and not gate_result.minimap_present:
                    if isinstance(self.locator, PyramidMatcher):
                        self.locator.reset_continuity()
                    reason = gate_result.reason or "minimap_not_visible"
                    localization = LocateResult(found=False, reason=reason)
                    snapshot = tracker.pause(captured, reason)
                    mode = "gate"
                else:
                    hint = tracker.position_hint
                    near = self.config.local_search.enabled and hint is not None and isinstance(self.locator, PyramidMatcher)
                    localization = (self.locator.locate_near(minimap, hint, self.config.local_search)
                                    if near else self.locator.locate(minimap))
                    snapshot = tracker.update(localization, captured)
                    scheduler.observe_result(found=localization.found, state=snapshot.state)
                    mode = "local" if near else "global"
                with self._lock:
                    if self._stop.is_set() or current != self._generation:
                        continue
                performance.record(time.perf_counter(), (time.perf_counter() - started) * 1000, mode)
                if self._manual.is_set():
                    self._manual.clear()
                    self.recorder.request_manual_report()
                incident = self.recorder.observe(minimap, localization, snapshot, captured)
                result = LiveResult(snapshot, captured, performance.snapshot, incident)
                with self._lock:
                    if not self._stop.is_set() and current == self._generation:
                        self._latest = result
        except Exception as error:
            with self._lock:
                self._error = error
        finally:
            self.recorder.close()
