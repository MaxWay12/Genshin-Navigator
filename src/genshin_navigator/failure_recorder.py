from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from .config import FailureRecorderConfig
from .matcher import LocateResult
from .tracker import TrackerSnapshot, TrackerState


@dataclass
class _RecordedFrame:
    image: np.ndarray
    timestamp: float
    localization: dict[str, object]
    tracker: dict[str, object]


@dataclass(frozen=True)
class DiagnosticContext:
    app_version: str = "unknown"
    schema_version: int | None = None
    content_version: str | None = None
    reference_versions: tuple[str, ...] = ()
    windows_build: str = "unknown"
    screen_resolution: tuple[int, int] | None = None
    dpi: int | None = None


class FailureRecorder:
    """Save minimap-only incidents for track loss and failed acquisition."""

    def __init__(
        self,
        config: FailureRecorderConfig,
        context: DiagnosticContext | None = None,
        *,
        automatic: bool = True,
    ):
        self.config = config
        self.context = context or DiagnosticContext()
        self.automatic = automatic
        self._buffer: deque[_RecordedFrame] = deque(maxlen=config.pre_frames)
        self._incident: list[_RecordedFrame] | None = None
        self._trigger_index: int | None = None
        self._trigger_reason: str | None = None
        self._trigger_origin_layer_id: str | None = None
        self._post_remaining = 0
        self._previous_state = TrackerState.LOST
        self._ever_tracked = False
        self._last_tracked_layer_id: str | None = None
        self._lost_since_timestamp: float | None = None
        self._last_trigger_timestamp: float | None = None
        self._initial_acquisition_reported = False
        self._incident_counter = 0
        self._manual_pending = False

    @property
    def active(self) -> bool:
        return self._incident is not None

    def request_manual_report(self) -> bool:
        if self._incident is not None or self._manual_pending:
            return False
        self._manual_pending = True
        return True

    @staticmethod
    def _frame(
        minimap: np.ndarray,
        localization: LocateResult,
        snapshot: TrackerSnapshot,
        timestamp: float,
    ) -> _RecordedFrame:
        return _RecordedFrame(
            image=minimap.copy(),
            timestamp=timestamp,
            localization=localization.to_dict(),
            tracker=snapshot.to_dict(),
        )

    def _cooldown_ready(self, timestamp: float) -> bool:
        return bool(
            self._incident is None
            and (
                self._last_trigger_timestamp is None
                or timestamp - self._last_trigger_timestamp >= self.config.cooldown_seconds
            )
        )

    def _start_incident(self, timestamp: float, reason: str) -> Path | None:
        self._incident = list(self._buffer)
        self._trigger_index = len(self._incident) - 1
        self._trigger_reason = reason
        self._trigger_origin_layer_id = self._last_tracked_layer_id
        self._post_remaining = self.config.post_frames
        self._last_trigger_timestamp = timestamp
        self._lost_since_timestamp = timestamp
        if self._post_remaining == 0:
            return self._flush()
        return None

    def observe(
        self,
        minimap: np.ndarray,
        localization: LocateResult,
        snapshot: TrackerSnapshot,
        timestamp: float,
    ) -> Path | None:
        frame = self._frame(minimap, localization, snapshot, timestamp)
        if (
            not self.config.enabled
            and not self._manual_pending
            and self._incident is None
        ):
            self._buffer.append(frame)
            return None
        saved: Path | None = None
        if self._incident is not None:
            self._incident.append(frame)
            self._post_remaining -= 1
            if self._post_remaining <= 0:
                saved = self._flush()
        else:
            self._buffer.append(frame)
            if self._manual_pending:
                self._manual_pending = False
                saved = self._start_incident(timestamp, "manual_report")
            elif self.automatic and snapshot.state is TrackerState.LOST:
                if self._lost_since_timestamp is None:
                    self._lost_since_timestamp = timestamp

                established_loss = (
                    self._ever_tracked and self._previous_state is not TrackerState.LOST
                )
                acquisition_failure = (
                    self.config.record_acquisition_failures
                    and not self._ever_tracked
                    and not self._initial_acquisition_reported
                    and timestamp - self._lost_since_timestamp
                    >= self.config.acquisition_timeout_seconds
                )
                if self._cooldown_ready(timestamp) and established_loss:
                    saved = self._start_incident(
                        timestamp, "established_track_became_lost"
                    )
                elif self._cooldown_ready(timestamp) and acquisition_failure:
                    self._initial_acquisition_reported = True
                    saved = self._start_incident(
                        timestamp, "localization_acquisition_timed_out"
                    )
            else:
                self._lost_since_timestamp = None

        if snapshot.state is TrackerState.TRACKING:
            self._ever_tracked = True
            if snapshot.map_layer_id is not None:
                self._last_tracked_layer_id = snapshot.map_layer_id
        self._previous_state = snapshot.state
        return saved

    @staticmethod
    def _last_known(frames: list[_RecordedFrame], trigger_index: int) -> dict[str, object] | None:
        for frame in reversed(frames[: trigger_index + 1]):
            tracker = frame.tracker
            position = tracker.get("position")
            if isinstance(position, dict):
                return position
            if tracker.get("x_px") is not None and tracker.get("y_px") is not None:
                return {
                    "region_id": "unknown",
                    "layer_id": tracker.get("map_layer_id") or "unknown",
                    "coordinate_space": "surface_atlas"
                    if tracker.get("map_layer_id") == "surface"
                    else "layer_local",
                    "x": tracker["x_px"],
                    "y": tracker["y_px"],
                    "reference_id": tracker.get("reference_id"),
                    "confidence": tracker.get("confidence"),
                    "state": tracker.get("state"),
                    "timestamp": frame.timestamp,
                }
        return None

    def _confirmed_layer_transition(self) -> bool:
        assert self._incident is not None and self._trigger_index is not None
        origin = self._trigger_origin_layer_id
        if origin is None:
            return False
        for frame in self._incident[self._trigger_index + 1 :]:
            tracker = frame.tracker
            if (
                tracker.get("accepted") is True
                and tracker.get("state") == TrackerState.TRACKING.value
                and tracker.get("map_layer_id") not in (None, origin)
            ):
                return True
        return False

    def _reset_incident(self) -> None:
        self._incident = None
        self._trigger_index = None
        self._trigger_reason = None
        self._trigger_origin_layer_id = None
        self._post_remaining = 0
        self._buffer.clear()

    def _incident_outcome(self) -> dict[str, object]:
        assert self._incident is not None and self._trigger_index is not None
        if self._trigger_reason == "manual_report":
            return {
                "outcome": "manual_report",
                "recovery_seconds": None,
                "recovery_method": None,
                "recovery_reference_id": None,
            }
        trigger_timestamp = self._incident[self._trigger_index].timestamp
        for frame in self._incident[self._trigger_index + 1 :]:
            tracker = frame.tracker
            if (
                tracker.get("accepted") is True
                and tracker.get("state") == TrackerState.TRACKING.value
                and tracker.get("stale") is not True
            ):
                method = frame.localization.get("match_method")
                return {
                    "outcome": "transient_recovered",
                    "recovery_seconds": round(frame.timestamp - trigger_timestamp, 4),
                    "recovery_method": method if isinstance(method, str) else "sift",
                    "recovery_reference_id": tracker.get("reference_id"),
                }
        return {
            "outcome": "unresolved",
            "recovery_seconds": None,
            "recovery_method": None,
            "recovery_reference_id": None,
        }

    def _flush(self) -> Path | None:
        assert (
            self._incident is not None
            and self._trigger_index is not None
            and self._trigger_reason is not None
        )
        if (
            self._trigger_reason != "manual_report"
            and self._confirmed_layer_transition()
        ):
            self._reset_incident()
            return None
        self._incident_counter += 1
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        incident_dir = self.config.output_dir / f"failure_{stamp}_{self._incident_counter:03d}"
        incident_dir.mkdir(parents=True, exist_ok=False)

        frames_metadata: list[dict[str, object]] = []
        for index, frame in enumerate(self._incident):
            filename = f"minimap_{index:03d}.png"
            success, encoded = cv2.imencode(".png", frame.image)
            if not success:
                raise OSError(f"Could not encode failure frame {index}")
            (incident_dir / filename).write_bytes(encoded.tobytes())
            frames_metadata.append(
                {
                    "image": filename,
                    "timestamp": frame.timestamp,
                    "offset_from_trigger_seconds": round(
                        frame.timestamp - self._incident[self._trigger_index].timestamp, 4
                    ),
                    "localization": frame.localization,
                    "tracker": frame.tracker,
                }
            )

        outcome = self._incident_outcome()
        metadata = {
            "format_version": 4,
            "privacy": "Only the configured minimap crop is stored; full game frames are not recorded.",
            "trigger": self._trigger_reason,
            "trigger_frame_index": self._trigger_index,
            **outcome,
            "last_known_position": self._last_known(self._incident, self._trigger_index),
            "environment": {
                "app_version": self.context.app_version,
                "schema_version": self.context.schema_version,
                "content_version": self.context.content_version,
                "reference_versions": list(self.context.reference_versions),
                "windows_build": self.context.windows_build,
                "screen_resolution": list(self.context.screen_resolution)
                if self.context.screen_resolution else None,
                "dpi": self.context.dpi,
            },
            "frames": frames_metadata,
        }
        (incident_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        self._reset_incident()
        return incident_dir

    def close(self) -> Path | None:
        if self._incident is None:
            return None
        return self._flush()

    def save_buffered_manual_report(self, timestamp: float) -> Path | None:
        if self._incident is not None or not self._buffer:
            return None
        self._incident = list(self._buffer)
        self._trigger_index = len(self._incident) - 1
        self._trigger_reason = "manual_report"
        self._trigger_origin_layer_id = self._last_tracked_layer_id
        self._post_remaining = 0
        self._last_trigger_timestamp = timestamp
        return self._flush()
