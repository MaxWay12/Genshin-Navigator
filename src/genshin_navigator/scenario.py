from __future__ import annotations

import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import cv2
import numpy as np

from .capture import crop_roi, grab_screen, load_image
from .config import AppConfig
from .pyramid import Locator, PyramidMatcher
from .position import PositionState
from .screen_gate import MinimapScreenGate, ScreenGateResult
from .scenario_kpis import DEFAULT_KPIS, evaluate_kpis
from .tracker import LiveTracker


SCENARIO_FORMAT_VERSION = 1


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return ordered[index]


def _safe_frame_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"Scenario frame escapes its directory: {relative}") from error
    return candidate


def load_scenario(path: str | Path) -> tuple[Path, dict[str, object]]:
    root = Path(path).resolve()
    manifest_path = root / "scenario.json"
    with manifest_path.open("r", encoding="utf-8") as stream:
        manifest = json.load(stream)
    if int(manifest.get("format_version", 0)) != SCENARIO_FORMAT_VERSION:
        raise ValueError("Unsupported scenario format_version")
    frames = manifest.get("frames")
    if not isinstance(frames, list) or not frames:
        raise ValueError("Scenario must contain at least one frame")
    previous = -math.inf
    for item in frames:
        if not isinstance(item, dict):
            raise ValueError("Scenario frames must be objects")
        timestamp = float(item["timestamp_seconds"])
        if timestamp < 0 or timestamp <= previous:
            raise ValueError("Scenario timestamps must be non-negative and strictly increasing")
        previous = timestamp
        frame_path = _safe_frame_path(root, str(item["image"]))
        if not frame_path.is_file():
            raise ValueError(f"Scenario frame does not exist: {item['image']}")
    expectations = manifest.get("expectations", [])
    if not isinstance(expectations, list):
        raise ValueError("Scenario expectations must be a list")
    for phase in expectations:
        if not isinstance(phase, dict):
            raise ValueError("Scenario expectations must be objects")
        start = float(phase["start_seconds"])
        end = float(phase["end_seconds"])
        if start < 0 or end < start:
            raise ValueError("Scenario expectation has an invalid time range")
        tracking = str(phase.get("tracking", "optional"))
        if tracking not in {"required", "optional"}:
            raise ValueError("Scenario expectation tracking must be required or optional")
    checkpoints = manifest.get("checkpoints", [])
    if not isinstance(checkpoints, list):
        raise ValueError("Scenario checkpoints must be a list")
    first_timestamp = float(frames[0]["timestamp_seconds"])
    last_timestamp = float(frames[-1]["timestamp_seconds"])
    checkpoint_timestamps: set[float] = set()
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, dict):
            raise ValueError("Scenario checkpoints must be objects")
        timestamp = float(checkpoint["timestamp_seconds"])
        if not first_timestamp <= timestamp <= last_timestamp:
            raise ValueError("Scenario checkpoint timestamp lies outside the recording")
        if timestamp in checkpoint_timestamps:
            raise ValueError("Scenario checkpoint timestamps must be unique")
        checkpoint_timestamps.add(timestamp)
        if not str(checkpoint.get("region_id") or "").strip():
            raise ValueError("Scenario checkpoint region_id must not be empty")
        if not str(checkpoint.get("layer_id") or "").strip():
            raise ValueError("Scenario checkpoint layer_id must not be empty")
        position = checkpoint.get("position")
        if not isinstance(position, dict):
            raise ValueError("Scenario checkpoint position must be an object")
        float(position["x"])
        float(position["y"])
        if float(position.get("tolerance_px", 20.0)) <= 0:
            raise ValueError("Scenario checkpoint tolerance must be positive")
    return root, manifest


def _build_expectations(
    duration_seconds: float,
    *,
    expected_region_id: str | None,
    expected_start_layer: str | None,
    expected_end_layer: str | None,
    stationary_last_seconds: float,
    required_throughout: bool = False,
) -> list[dict[str, object]]:
    expectations: list[dict[str, object]] = []
    if required_throughout:
        if not expected_start_layer or expected_start_layer != expected_end_layer:
            raise ValueError(
                "required_throughout requires identical start and end layers"
            )
        phase: dict[str, object] = {
            "name": "required throughout",
            "start_seconds": 0.0,
            "end_seconds": round(duration_seconds, 3),
            "tracking": "required",
            "region_id": expected_region_id,
            "layer_id": expected_start_layer,
        }
        if stationary_last_seconds > 0:
            phase["stationary_from_seconds"] = round(
                duration_seconds - stationary_last_seconds, 3
            )
        return [phase]
    edge_window = min(3.0, duration_seconds / 3.0)
    if expected_start_layer:
        expectations.append(
            {
                "name": "start",
                "start_seconds": 0.0,
                "end_seconds": round(edge_window, 3),
                "tracking": "required",
                "region_id": expected_region_id,
                "layer_id": expected_start_layer,
            }
        )
    if expected_end_layer:
        end_start = max(0.0, duration_seconds - max(5.0, stationary_last_seconds))
        phase: dict[str, object] = {
            "name": "end",
            "start_seconds": round(end_start, 3),
            "end_seconds": round(duration_seconds, 3),
            "tracking": "required",
            "region_id": expected_region_id,
            "layer_id": expected_end_layer,
        }
        if stationary_last_seconds > 0:
            phase["stationary_from_seconds"] = round(
                max(end_start, duration_seconds - stationary_last_seconds), 3
            )
        expectations.append(phase)
    return expectations


def record_scenario(
    config: AppConfig,
    output: str | Path,
    duration_seconds: float,
    *,
    name: str = "scenario",
    expected_region_id: str | None = "fontaine",
    expected_start_layer: str | None = None,
    expected_end_layer: str | None = None,
    stationary_last_seconds: float = 0.0,
    required_throughout: bool = False,
    genshin_ui_scale: str = "unknown",
    graphics_preset: str = "unknown",
    capture_screen: Callable[[], np.ndarray] = grab_screen,
    clock: Callable[[], float] = time.perf_counter,
    sleeper: Callable[[float], None] = time.sleep,
    phase_notifier: Callable[[str], None] | None = None,
) -> Path:
    if duration_seconds <= 0:
        raise ValueError("Scenario duration must be positive")
    if stationary_last_seconds < 0 or stationary_last_seconds > duration_seconds:
        raise ValueError("stationary_last_seconds must be within the recording duration")
    root = Path(output).resolve()
    manifest_path = root / "scenario.json"
    if manifest_path.exists():
        raise FileExistsError(f"Scenario already exists: {manifest_path}")
    frames_dir = root / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    screen_gate = (
        MinimapScreenGate.from_config(config.screen_gate)
        if config.screen_gate.enabled
        else None
    )
    started = clock()
    if phase_notifier is not None:
        phase_notifier("started")
    next_capture = started
    frames: list[dict[str, object]] = []
    stationary_notified = stationary_last_seconds <= 0
    while True:
        now = clock()
        elapsed = now - started
        if frames and elapsed > duration_seconds:
            break
        if (
            not stationary_notified
            and elapsed >= duration_seconds - stationary_last_seconds
        ):
            stationary_notified = True
            if phase_notifier is not None:
                phase_notifier("stationary")
        minimap = crop_roi(capture_screen(), config.roi)
        gate = (
            screen_gate.check(minimap)
            if screen_gate is not None
            else ScreenGateResult(True, 1.0)
        )
        filename = f"minimap_{len(frames):05d}.png"
        relative = f"frames/{filename}"
        if not cv2.imwrite(str(frames_dir / filename), minimap):
            raise OSError(f"Could not save scenario frame: {filename}")
        frames.append(
            {
                "image": relative,
                "timestamp_seconds": round(elapsed, 6),
                "recorded_minimap_present": gate.minimap_present,
                "recorded_gate_confidence": gate.confidence,
                "recorded_gate_reason": gate.reason,
            }
        )
        next_capture += config.interval_seconds
        remaining = next_capture - clock()
        if remaining > 0:
            sleeper(remaining)
    if phase_notifier is not None:
        phase_notifier("finished")
    manifest = {
        "format_version": SCENARIO_FORMAT_VERSION,
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "privacy": "Only the configured minimap crop is stored; full game frames are never written.",
        "interval_seconds": config.interval_seconds,
        "compatibility": {
            "genshin_ui_scale": genshin_ui_scale or "unknown",
            "graphics_preset": graphics_preset or "unknown",
        },
        "roi": {
            "left": config.roi.left,
            "top": config.roi.top,
            "width": config.roi.width,
            "height": config.roi.height,
        },
        "expectations": _build_expectations(
            duration_seconds,
            expected_region_id=expected_region_id,
            expected_start_layer=expected_start_layer,
            expected_end_layer=expected_end_layer,
            stationary_last_seconds=stationary_last_seconds,
            required_throughout=required_throughout,
        ),
        "checkpoints": [],
        "frames": frames,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest_path


def _phase_at(expectations: list[dict[str, object]], timestamp: float) -> dict[str, object] | None:
    for phase in reversed(expectations):
        if float(phase["start_seconds"]) <= timestamp <= float(phase["end_seconds"]):
            return phase
    return None


def _confirmed_layer_runs(rows: list[dict[str, object]]) -> list[tuple[str, int]]:
    layers: list[str] = []
    for row in rows:
        tracker = row["tracker"]
        assert isinstance(tracker, dict)
        position = tracker.get("position")
        if (
            tracker.get("state") == PositionState.TRACKING.value
            and not tracker.get("stale")
            and isinstance(position, dict)
        ):
            layers.append(str(position["layer_id"]))
    runs: list[tuple[str, int]] = []
    for layer in layers:
        if runs and runs[-1][0] == layer:
            runs[-1] = (layer, runs[-1][1] + 1)
        else:
            runs.append((layer, 1))
    return runs


def _is_confirmed(tracker: dict[str, object]) -> bool:
    return bool(
        tracker.get("state") == PositionState.TRACKING.value
        and not tracker.get("stale")
        and isinstance(tracker.get("position"), dict)
    )


def _nearest_row_index(rows: list[dict[str, object]], timestamp: float) -> int:
    return min(
        range(len(rows)),
        key=lambda index: abs(float(rows[index]["timestamp_seconds"]) - timestamp),
    )


def _longest_streak(
    rows: list[dict[str, object]], predicate: Callable[[dict[str, object]], bool]
) -> float:
    longest = 0.0
    current = 0.0
    for row, following in zip(rows, rows[1:]):
        duration = max(
            0.0,
            float(following["timestamp_seconds"]) - float(row["timestamp_seconds"]),
        )
        if predicate(row):
            current += duration
            longest = max(longest, current)
        else:
            current = 0.0
    return longest


def evaluate_scenario(
    path: str | Path,
    config: AppConfig,
    matcher: Locator,
    *,
    screen_gate: MinimapScreenGate | None = None,
) -> dict[str, object]:
    root, manifest = load_scenario(path)
    expectations = [dict(item) for item in manifest.get("expectations", [])]
    checkpoints = [dict(item) for item in manifest.get("checkpoints", [])]
    gate = screen_gate
    if gate is None and config.screen_gate.enabled:
        gate = MinimapScreenGate.from_config(config.screen_gate)
    tracker = LiveTracker(config.tracker)
    rows: list[dict[str, object]] = []
    processing_ms: list[float] = []
    visible_transitions: list[float] = []
    previous_visible = False

    for item in manifest["frames"]:
        timestamp = float(item["timestamp_seconds"])
        minimap = load_image(_safe_frame_path(root, str(item["image"])))
        started = time.perf_counter()
        gate_result = gate.check(minimap) if gate is not None else ScreenGateResult(True, 1.0)
        if gate_result.minimap_present and not previous_visible:
            visible_transitions.append(timestamp)
        previous_visible = gate_result.minimap_present
        localization = None
        if not gate_result.minimap_present:
            if isinstance(matcher, PyramidMatcher):
                matcher.reset_continuity()
            snapshot = tracker.pause(timestamp, gate_result.reason or "minimap_not_visible")
        else:
            hint = tracker.position_hint
            localization = (
                matcher.locate_near(minimap, hint, config.local_search)
                if (
                    config.local_search.enabled
                    and hint is not None
                    and isinstance(matcher, PyramidMatcher)
                )
                else matcher.locate(minimap)
            )
            snapshot = tracker.update(localization, timestamp)
        elapsed_ms = (time.perf_counter() - started) * 1000
        processing_ms.append(elapsed_ms)
        phase = _phase_at(expectations, timestamp)
        rows.append(
            {
                "image": item["image"],
                "timestamp_seconds": timestamp,
                "gate": {
                    "minimap_present": gate_result.minimap_present,
                    "confidence": gate_result.confidence,
                    "reason": gate_result.reason,
                },
                "localization": localization.to_dict() if localization is not None else None,
                "tracker": snapshot.to_dict(),
                "expectation": phase.get("name") if phase else None,
                "processing_ms": round(elapsed_ms, 2),
            }
        )

    false_lock_rows: set[int] = set()
    layer_samples = 0
    correct_layers = 0
    required_frames = 0
    correct_required_frames = 0
    position_errors: list[float] = []
    for row_index, row in enumerate(rows):
        phase = _phase_at(expectations, float(row["timestamp_seconds"]))
        tracker_raw = row["tracker"]
        assert isinstance(tracker_raw, dict)
        position = tracker_raw.get("position")
        confirmed = _is_confirmed(tracker_raw)
        gate_raw = row["gate"]
        assert isinstance(gate_raw, dict)
        if (
            phase is not None
            and phase.get("tracking") == "required"
            and gate_raw.get("minimap_present")
        ):
            required_frames += 1
            if confirmed and _position_matches_dict(tracker_raw, phase):
                correct_required_frames += 1
        if phase is None or not confirmed:
            continue
        if phase.get("layer_id"):
            layer_samples += 1
            if str(position["layer_id"]) == str(phase["layer_id"]):
                correct_layers += 1
        if not _position_matches_dict(tracker_raw, phase):
            false_lock_rows.add(row_index)
        expected_position = phase.get("position")
        if isinstance(expected_position, dict):
            position_errors.append(
                math.hypot(
                    float(position["x"]) - float(expected_position["x"]),
                    float(position["y"]) - float(expected_position["y"]),
                )
            )

    checkpoint_errors: list[float] = []
    checkpoint_tracking_samples = 0
    for checkpoint in checkpoints:
        row_index = _nearest_row_index(rows, float(checkpoint["timestamp_seconds"]))
        row = rows[row_index]
        tracker_raw = row["tracker"]
        assert isinstance(tracker_raw, dict)
        if not _is_confirmed(tracker_raw):
            continue
        checkpoint_tracking_samples += 1
        if not _position_matches_dict(tracker_raw, checkpoint):
            false_lock_rows.add(row_index)
        position = tracker_raw["position"]
        expected = checkpoint["position"]
        assert isinstance(position, dict) and isinstance(expected, dict)
        checkpoint_errors.append(
            math.hypot(
                float(position["x"]) - float(expected["x"]),
                float(position["y"]) - float(expected["y"]),
            )
        )

    acquisition_delays: list[float | None] = []
    for visible_at in visible_transitions:
        next_hidden = next(
            (
                float(row["timestamp_seconds"])
                for row in rows
                if float(row["timestamp_seconds"]) > visible_at
                and isinstance(row["gate"], dict)
                and not row["gate"].get("minimap_present")
            ),
            math.inf,
        )
        acquired = next(
            (
                float(row["timestamp_seconds"])
                for row in rows
                if float(row["timestamp_seconds"]) >= visible_at
                and float(row["timestamp_seconds"]) < next_hidden
                and isinstance(row["tracker"], dict)
                and row["tracker"].get("state") == PositionState.TRACKING.value
                and not row["tracker"].get("stale")
                and row["tracker"].get("position") is not None
            ),
            None,
        )
        acquisition_delays.append(None if acquired is None else round(acquired - visible_at, 4))

    stationary_jitter: list[float] = []
    for phase in expectations:
        stationary_from = phase.get("stationary_from_seconds")
        if stationary_from is None:
            continue
        samples: list[tuple[float, float]] = []
        for row in rows:
            timestamp = float(row["timestamp_seconds"])
            if not float(stationary_from) <= timestamp <= float(phase["end_seconds"]):
                continue
            tracker_raw = row["tracker"]
            assert isinstance(tracker_raw, dict)
            position = tracker_raw.get("position")
            if (
                tracker_raw.get("state") == PositionState.TRACKING.value
                and not tracker_raw.get("stale")
                and isinstance(position, dict)
                and (not phase.get("layer_id") or position.get("layer_id") == phase.get("layer_id"))
            ):
                samples.append((float(position["x"]), float(position["y"])))
        if len(samples) >= 2:
            median_x = statistics.median(point[0] for point in samples)
            median_y = statistics.median(point[1] for point in samples)
            stationary_jitter.extend(
                math.hypot(x - median_x, y - median_y) for x, y in samples
            )

    lost_duration = 0.0
    for current, following in zip(rows, rows[1:]):
        tracker_raw = current["tracker"]
        gate_raw = current["gate"]
        assert isinstance(tracker_raw, dict)
        assert isinstance(gate_raw, dict)
        if (
            gate_raw.get("minimap_present")
            and tracker_raw.get("state") == PositionState.LOST.value
        ):
            lost_duration += float(following["timestamp_seconds"]) - float(
                current["timestamp_seconds"]
            )

    runs = _confirmed_layer_runs(rows)
    one_frame_layer_runs = sum(
        1
        for index, (_, count) in enumerate(runs)
        if count == 1 and 0 < index < len(runs) - 1
    )
    successful_delays = [value for value in acquisition_delays if value is not None]
    max_delay = max(successful_delays) if successful_delays else None
    reacquisition_p95 = _percentile(successful_delays, 0.95)
    jitter_p95 = _percentile(stationary_jitter, 0.95)
    annotated = bool(expectations)
    tracking_coverage = (
        correct_required_frames / required_frames if required_frames else None
    )
    def required_visible(row: dict[str, object]) -> bool:
        phase = _phase_at(expectations, float(row["timestamp_seconds"]))
        gate_raw = row["gate"]
        assert isinstance(gate_raw, dict)
        return bool(
            phase is not None
            and phase.get("tracking") == "required"
            and gate_raw.get("minimap_present")
        )

    def untracked_required(row: dict[str, object]) -> bool:
        if not required_visible(row):
            return False
        tracker_raw = row["tracker"]
        assert isinstance(tracker_raw, dict)
        phase = _phase_at(expectations, float(row["timestamp_seconds"]))
        assert phase is not None
        return not (_is_confirmed(tracker_raw) and _position_matches_dict(tracker_raw, phase))

    def lost_required(row: dict[str, object]) -> bool:
        if not required_visible(row):
            return False
        tracker_raw = row["tracker"]
        assert isinstance(tracker_raw, dict)
        return tracker_raw.get("state") == PositionState.LOST.value

    longest_untracked = _longest_streak(rows, untracked_required)
    longest_lost = _longest_streak(rows, lost_required)
    all_position_errors = position_errors + checkpoint_errors
    metrics: dict[str, object] = {
        "total_frames": len(rows),
        "visible_frames": sum(bool(row["gate"]["minimap_present"]) for row in rows),
        "tracking_frames": sum(
            row["tracker"]["state"] == PositionState.TRACKING.value
            and not row["tracker"].get("stale")
            and row["tracker"].get("position") is not None
            for row in rows
        ),
        "required_tracking_coverage": (
            round(tracking_coverage, 4) if tracking_coverage is not None else None
        ),
        "false_locks": len(false_lock_rows),
        "layer_accuracy": round(correct_layers / layer_samples, 4) if layer_samples else None,
        "layer_samples": layer_samples,
        "position_checkpoint_count": len(checkpoints),
        "position_checkpoint_tracking_samples": checkpoint_tracking_samples,
        "one_frame_layer_runs": one_frame_layer_runs,
        "acquisition_delays_seconds": acquisition_delays,
        "max_acquisition_delay_seconds": max_delay,
        "reacquisition_p95_seconds": (
            round(reacquisition_p95, 4) if reacquisition_p95 is not None else None
        ),
        "unreacquired_transition_count": sum(value is None for value in acquisition_delays),
        "lost_duration_seconds": round(lost_duration, 4),
        "longest_lost_streak_seconds": round(longest_lost, 4),
        "longest_untracked_streak_seconds": round(longest_untracked, 4),
        "stationary_jitter_p95_px": round(jitter_p95, 3) if jitter_p95 is not None else None,
        "median_position_error_px": round(statistics.median(all_position_errors), 3) if all_position_errors else None,
        "p95_position_error_px": round(_percentile(all_position_errors, 0.95), 3) if all_position_errors else None,
        "mean_processing_ms": round(statistics.mean(processing_ms), 2),
        "p95_processing_ms": round(_percentile(processing_ms, 0.95), 2),
    }
    passed = None if not annotated else not evaluate_kpis(metrics, DEFAULT_KPIS)
    return {
        "format_version": SCENARIO_FORMAT_VERSION,
        "scenario": str(root),
        "name": manifest.get("name", root.name),
        "annotated": annotated,
        "passed": passed,
        "metrics": metrics,
        "frames": rows,
    }


def _position_matches_dict(tracker: dict[str, object], phase: dict[str, object]) -> bool:
    position = tracker.get("position")
    if not isinstance(position, dict):
        return False
    if phase.get("region_id") and position.get("region_id") != phase.get("region_id"):
        return False
    if phase.get("layer_id") and position.get("layer_id") != phase.get("layer_id"):
        return False
    expected_position = phase.get("position")
    if isinstance(expected_position, dict):
        error = math.hypot(
            float(position["x"]) - float(expected_position["x"]),
            float(position["y"]) - float(expected_position["y"]),
        )
        return error <= float(expected_position.get("tolerance_px", 20.0))
    return True
