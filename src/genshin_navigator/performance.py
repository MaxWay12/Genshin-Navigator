from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .config import PerformanceConfig
from .position import PositionState


@dataclass(frozen=True)
class PerformanceSnapshot:
    mode: str
    search_mode: str
    processing_ms: float | None
    processing_p95_ms: float | None
    cv_fps: float


class LocalizationScheduler:
    """Throttle expensive localization while leaving UI/hotkeys responsive."""

    def __init__(self, config: PerformanceConfig):
        self.config = config
        self._last_localization_at: float | None = None
        self._consecutive_global_misses = 0

    def interval_for(self, state: PositionState) -> float:
        if state is PositionState.TRACKING:
            return self.config.tracking_interval
        backoff = self.config.global_search_interval * (
            1.45 ** min(self._consecutive_global_misses, 8)
        )
        return min(backoff, self.config.global_search_max_interval_seconds)

    def due(self, now: float, state: PositionState) -> bool:
        return bool(
            self._last_localization_at is None
            or now - self._last_localization_at + 1e-9 >= self.interval_for(state)
        )

    def mark_run(self, now: float) -> None:
        self._last_localization_at = now

    def observe_result(self, *, found: bool, state: PositionState) -> None:
        if found or state is PositionState.TRACKING:
            self._consecutive_global_misses = 0
        else:
            self._consecutive_global_misses += 1

    def force_next(self) -> None:
        self._last_localization_at = None
        self._consecutive_global_misses = 0


class PerformanceMonitor:
    def __init__(self, mode: str, window: int = 120):
        self.mode = mode
        self._samples: deque[float] = deque(maxlen=max(5, window))
        self._timestamps: deque[float] = deque(maxlen=max(5, window))
        self._search_mode = "starting"

    def record(self, now: float, processing_ms: float, search_mode: str) -> None:
        self._samples.append(max(0.0, processing_ms))
        self._timestamps.append(now)
        self._search_mode = search_mode

    def idle(self, search_mode: str) -> None:
        self._search_mode = search_mode

    @property
    def snapshot(self) -> PerformanceSnapshot:
        latest = self._samples[-1] if self._samples else None
        p95 = float(np.percentile(self._samples, 95)) if self._samples else None
        cv_fps = 0.0
        if len(self._timestamps) >= 2:
            duration = self._timestamps[-1] - self._timestamps[0]
            if duration > 0:
                cv_fps = (len(self._timestamps) - 1) / duration
        return PerformanceSnapshot(
            self.mode,
            self._search_mode,
            round(latest, 2) if latest is not None else None,
            round(p95, 2) if p95 is not None else None,
            round(cv_fps, 1),
        )
