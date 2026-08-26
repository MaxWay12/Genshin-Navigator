from __future__ import annotations

import json
from pathlib import Path

from .capture import load_image
from .config import AppConfig
from .pyramid import Locator, PyramidMatcher
from .screen_gate import MinimapScreenGate
from .tracker import LiveTracker, TrackerState


def _recorded_outcome(payload: dict[str, object]) -> str | None:
    explicit = payload.get("outcome")
    if isinstance(explicit, str):
        return explicit
    if payload.get("trigger") == "manual_report":
        return "manual_report"
    frames = payload.get("frames")
    if not isinstance(frames, list) or not frames:
        return None
    trigger_index = min(max(int(payload.get("trigger_frame_index", 0)), 0), len(frames) - 1)
    for frame in frames[trigger_index + 1 :]:
        if not isinstance(frame, dict):
            continue
        tracker = frame.get("tracker")
        if (
            isinstance(tracker, dict)
            and tracker.get("accepted") is True
            and tracker.get("state") == TrackerState.TRACKING.value
            and tracker.get("stale") is not True
        ):
            return "transient_recovered"
    return "unresolved"


def _safe_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"Diagnostic frame escapes bundle: {relative}") from error
    return candidate


def load_diagnostic_bundle(path: str | Path) -> tuple[Path, dict[str, object]]:
    root = Path(path).resolve()
    payload = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or int(payload.get("format_version", 0)) not in {3, 4}:
        raise ValueError("Unsupported diagnostic format_version")
    frames = payload.get("frames")
    if not isinstance(frames, list) or not frames:
        raise ValueError("Diagnostic bundle contains no frames")
    for frame in frames:
        if not isinstance(frame, dict) or not isinstance(frame.get("image"), str):
            raise ValueError("Diagnostic frame metadata is invalid")
        if not _safe_child(root, frame["image"]).is_file():
            raise ValueError(f"Diagnostic frame is missing: {frame['image']}")
    return root, payload


def replay_diagnostic(
    path: str | Path, config: AppConfig, matcher: Locator
) -> dict[str, object]:
    root, payload = load_diagnostic_bundle(path)
    reset_continuity = getattr(matcher, "reset_continuity", None)
    if callable(reset_continuity):
        reset_continuity()
    tracker = LiveTracker(config.tracker)
    gate = (
        MinimapScreenGate.from_config(config.screen_gate)
        if config.screen_gate.enabled else None
    )
    rows: list[dict[str, object]] = []
    frames = payload["frames"]
    assert isinstance(frames, list)
    for index, frame in enumerate(frames):
        assert isinstance(frame, dict)
        timestamp = float(frame.get("timestamp", index * config.interval_seconds))
        minimap = load_image(_safe_child(root, str(frame["image"])))
        gate_result = gate.check(minimap) if gate is not None else None
        if gate_result is not None and not gate_result.minimap_present:
            reset_continuity = getattr(matcher, "reset_continuity", None)
            if callable(reset_continuity):
                reset_continuity()
            localization = None
            snapshot = tracker.pause(timestamp, gate_result.reason or "minimap_not_visible")
        else:
            hint = tracker.position_hint
            localization = (
                matcher.locate_near(minimap, hint, config.local_search)
                if config.local_search.enabled
                and hint is not None
                and isinstance(matcher, PyramidMatcher)
                else matcher.locate(minimap)
            )
            snapshot = tracker.update(localization, timestamp)
        rows.append(
            {
                "image": frame["image"],
                "timestamp": timestamp,
                "localization": localization.to_dict() if localization else None,
                "tracker": snapshot.to_dict(),
            }
        )
    trigger_index = int(payload.get("trigger_frame_index", 0))
    trigger_index = min(max(trigger_index, 0), len(rows) - 1)
    trigger_timestamp = float(rows[trigger_index]["timestamp"])
    recovery_row = next(
        (
            row
            for row in rows[trigger_index + 1 :]
            if isinstance(row["tracker"], dict)
            and row["tracker"].get("accepted") is True
            and row["tracker"].get("state") == TrackerState.TRACKING.value
            and row["tracker"].get("stale") is not True
        ),
        None,
    )
    manual = payload.get("trigger") == "manual_report"
    recovery_seconds = (
        round(float(recovery_row["timestamp"]) - trigger_timestamp, 4)
        if recovery_row is not None and not manual
        else None
    )
    recovery_localization = recovery_row.get("localization") if recovery_row else None
    recovery_method = (
        recovery_localization.get("match_method") or "sift"
        if isinstance(recovery_localization, dict)
        else None
    )
    outcome = (
        "manual_report"
        if manual
        else "transient_recovered" if recovery_row is not None else "unresolved"
    )
    return {
        "format_version": int(payload["format_version"]),
        "trigger": payload.get("trigger"),
        "trigger_frame_index": trigger_index,
        "recorded_outcome": _recorded_outcome(payload),
        "outcome": outcome,
        "recovery_seconds": recovery_seconds,
        "recovery_method": recovery_method,
        "recovery_reference_id": (
            recovery_row["tracker"].get("reference_id")
            if recovery_row is not None and isinstance(recovery_row["tracker"], dict)
            else None
        ),
        "frame_count": len(rows),
        "tracking_frames": sum(
            isinstance(row["tracker"], dict)
            and row["tracker"].get("state") == TrackerState.TRACKING.value
            for row in rows
        ),
        "rows": rows,
    }
