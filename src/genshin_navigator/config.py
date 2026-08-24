from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Roi:
    left: int
    top: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("ROI width and height must be positive")


@dataclass(frozen=True)
class MatcherConfig:
    max_features: int = 10000
    ratio_threshold: float = 0.72
    min_matches: int = 12
    min_inliers: int = 8
    min_scale: float = 0.2
    max_scale: float = 5.0


@dataclass(frozen=True)
class TrackerConfig:
    min_confidence: float = 0.35
    min_inliers: int = 8
    acquire_hits: int = 2
    relocate_hits: int = 3
    candidate_radius_px: float = 18.0
    max_speed_px_per_second: float = 90.0
    jump_margin_px: float = 12.0
    smoothing_alpha: float = 0.4
    lost_timeout_seconds: float = 1.5

    def __post_init__(self) -> None:
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("tracker.min_confidence must be between 0 and 1")
        if self.min_inliers < 1 or self.acquire_hits < 1 or self.relocate_hits < 1:
            raise ValueError("tracker hit and inlier thresholds must be positive")
        if self.candidate_radius_px <= 0 or self.max_speed_px_per_second <= 0:
            raise ValueError("tracker distance thresholds must be positive")
        if not 0 < self.smoothing_alpha <= 1:
            raise ValueError("tracker.smoothing_alpha must be between 0 and 1")
        if self.lost_timeout_seconds <= 0:
            raise ValueError("tracker.lost_timeout_seconds must be positive")


@dataclass(frozen=True)
class LocalSearchConfig:
    enabled: bool = True
    radius_px: float = 80.0
    ratio_threshold: float = 0.82
    min_matches: int = 8
    min_inliers: int = 8

    def __post_init__(self) -> None:
        if self.radius_px <= 0:
            raise ValueError("local_search.radius_px must be positive")
        if not 0 < self.ratio_threshold < 1:
            raise ValueError("local_search.ratio_threshold must be within (0, 1)")
        if self.min_matches < 3 or self.min_inliers < 3:
            raise ValueError("local_search match and inlier thresholds must be at least 3")


@dataclass(frozen=True)
class FailureRecorderConfig:
    enabled: bool = False
    output_dir: Path = Path("artifacts/failures")
    pre_frames: int = 8
    post_frames: int = 8
    cooldown_seconds: float = 5.0
    record_acquisition_failures: bool = True
    acquisition_timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.pre_frames < 1 or self.post_frames < 0:
            raise ValueError("failure_recorder frame counts must be non-negative")
        if self.cooldown_seconds < 0:
            raise ValueError("failure_recorder.cooldown_seconds must be non-negative")
        if self.acquisition_timeout_seconds < 0:
            raise ValueError(
                "failure_recorder.acquisition_timeout_seconds must be non-negative"
            )


@dataclass(frozen=True)
class ScreenGateConfig:
    enabled: bool = False
    template_path: Path | None = None
    match_threshold: float = 0.14
    blank_std_threshold: float = 12.0

    def __post_init__(self) -> None:
        if not 0 <= self.match_threshold <= 1:
            raise ValueError("screen_gate.match_threshold must be between 0 and 1")
        if self.blank_std_threshold < 0:
            raise ValueError("screen_gate.blank_std_threshold must be non-negative")
        if self.enabled and self.template_path is None:
            raise ValueError("screen_gate.template_path is required when enabled")


@dataclass(frozen=True)
class PoiConfig:
    enabled: bool = False
    catalog_path: Path | None = None
    progress_path: Path = Path("artifacts/poi_progress.json")
    kinds: tuple[str, ...] = ("chest", "hydroculus", "waypoint")
    target_kinds: tuple[str, ...] = ("chest",)

    def __post_init__(self) -> None:
        if self.enabled and self.catalog_path is None:
            raise ValueError("poi.catalog_path is required when enabled")
        if not self.kinds:
            raise ValueError("poi.kinds must not be empty")
        if not self.target_kinds:
            raise ValueError("poi.target_kinds must not be empty")


@dataclass(frozen=True)
class NavigationConfig:
    enabled: bool = True
    calibration_path: Path = Path("datasets/local/calibration/fontaine.json")


@dataclass(frozen=True)
class AppConfig:
    map_path: Path | None
    pyramid_path: Path | None
    debug_map_path: Path | None
    roi: Roi
    interval_seconds: float = 0.5
    matcher: MatcherConfig = field(default_factory=MatcherConfig)
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    local_search: LocalSearchConfig = field(default_factory=LocalSearchConfig)
    failure_recorder: FailureRecorderConfig = field(default_factory=FailureRecorderConfig)
    screen_gate: ScreenGateConfig = field(default_factory=ScreenGateConfig)
    poi: PoiConfig = field(default_factory=PoiConfig)
    navigation: NavigationConfig = field(default_factory=NavigationConfig)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as stream:
        raw: dict[str, Any] = json.load(stream)

    def resolve_optional(value: str | None) -> Path | None:
        if value is None:
            return None
        result = Path(value)
        return result if result.is_absolute() else (config_path.parent / result).resolve()

    map_path = resolve_optional(raw.get("map_path"))
    pyramid_path = resolve_optional(raw.get("pyramid_path"))
    debug_map_path = resolve_optional(raw.get("debug_map_path"))
    if map_path is None and pyramid_path is None:
        raise ValueError("Config must define map_path or pyramid_path")

    recorder_raw = dict(raw.get("failure_recorder", {}))
    recorder_output = Path(recorder_raw.pop("output_dir", "artifacts/failures"))
    if not recorder_output.is_absolute():
        recorder_output = (config_path.parent / recorder_output).resolve()

    screen_gate_raw = dict(raw.get("screen_gate", {}))
    template_value = screen_gate_raw.pop("template_path", None)
    screen_gate_template = resolve_optional(template_value)

    poi_raw = dict(raw.get("poi", {}))
    poi_catalog = resolve_optional(poi_raw.pop("catalog_path", None))
    poi_progress_value = poi_raw.pop("progress_path", "artifacts/poi_progress.json")
    poi_progress = Path(poi_progress_value)
    if not poi_progress.is_absolute():
        poi_progress = (config_path.parent / poi_progress).resolve()
    poi_kinds = tuple(poi_raw.pop("kinds", ("chest", "hydroculus", "waypoint")))
    poi_target_kinds = tuple(poi_raw.pop("target_kinds", ("chest",)))

    navigation_raw = dict(raw.get("navigation", {}))
    calibration_value = navigation_raw.pop(
        "calibration_path", "datasets/local/calibration/fontaine.json"
    )
    calibration_path = Path(calibration_value)
    if not calibration_path.is_absolute():
        calibration_path = (config_path.parent / calibration_path).resolve()

    return AppConfig(
        map_path=map_path,
        pyramid_path=pyramid_path,
        debug_map_path=debug_map_path or map_path,
        roi=Roi(**raw["roi"]),
        interval_seconds=float(raw.get("interval_seconds", 0.5)),
        matcher=MatcherConfig(**raw.get("matcher", {})),
        tracker=TrackerConfig(**raw.get("tracker", {})),
        local_search=LocalSearchConfig(**raw.get("local_search", {})),
        failure_recorder=FailureRecorderConfig(
            output_dir=recorder_output, **recorder_raw
        ),
        screen_gate=ScreenGateConfig(
            template_path=screen_gate_template, **screen_gate_raw
        ),
        poi=PoiConfig(
            catalog_path=poi_catalog,
            progress_path=poi_progress,
            kinds=poi_kinds,
            target_kinds=poi_target_kinds,
            **poi_raw,
        ),
        navigation=NavigationConfig(
            calibration_path=calibration_path,
            **navigation_raw,
        ),
    )
