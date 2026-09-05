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
class AnchorLocalizationConfig:
    """Optional semantic-map anchors used after normal feature matching fails."""

    enabled: bool = False
    catalog_path: Path | None = None
    template_paths: dict[str, Path] = field(default_factory=dict)
    min_template_score: float = 0.30
    min_global_anchors: int = 3
    local_match_radius_px: float = 45.0
    max_residual_px: float = 12.0
    default_canonical_scale: float = 0.83

    def __post_init__(self) -> None:
        if self.enabled and (self.catalog_path is None or not self.template_paths):
            raise ValueError(
                "anchor_localization requires catalog_path and template_paths when enabled"
            )
        if not 0 < self.min_template_score <= 1:
            raise ValueError("anchor_localization.min_template_score must be within (0, 1]")
        if self.min_global_anchors < 2:
            raise ValueError("anchor_localization.min_global_anchors must be at least 2")
        if (
            self.local_match_radius_px <= 0
            or self.max_residual_px <= 0
        ):
            raise ValueError("anchor localization radii must be positive")
        if not 0.2 <= self.default_canonical_scale <= 5.0:
            raise ValueError("anchor default scale is implausible")


@dataclass(frozen=True)
class MotionFallbackConfig:
    """Conservative relative motion used only while a track already exists."""

    enabled: bool = False
    max_features: int = 160
    min_inliers: int = 20
    forward_backward_error_px: float = 1.5
    max_residual_px: float = 2.5
    max_screen_step_px: float = 12.0
    min_phase_response: float = 0.22
    max_phase_disagreement_px: float = 2.0
    max_consecutive_frames: int = 5
    confidence: float = 0.58

    def __post_init__(self) -> None:
        if self.max_features < self.min_inliers or self.min_inliers < 8:
            raise ValueError("motion fallback feature counts are invalid")
        if (
            self.forward_backward_error_px <= 0
            or self.max_residual_px <= 0
            or self.max_screen_step_px <= 0
            or self.max_phase_disagreement_px <= 0
        ):
            raise ValueError("motion fallback geometry thresholds must be positive")
        if not 0 < self.confidence <= 1:
            raise ValueError("motion fallback confidence must be within (0, 1]")
        if not 0 < self.min_phase_response <= 1:
            raise ValueError("motion fallback phase response must be within (0, 1]")
        if self.max_consecutive_frames < 1:
            raise ValueError("motion fallback frame budget must be positive")


@dataclass(frozen=True)
class EdgeCorrelationConfig:
    """Absolute fallback for low-feature, north-up surface minimaps."""

    enabled: bool = False
    scales: tuple[float, ...] = (0.80, 0.83, 0.86)
    rotations_degrees: tuple[float, ...] = (0.0,)
    min_score: float = 0.32
    min_peak_margin: float = 0.07
    exclusion_radius_px: float = 100.0
    canny_low: int = 30
    canny_high: int = 100
    coarse_scale: float = 0.35
    coarse_candidates: int = 3
    refine_radius_px: int = 16
    confidence: float = 0.52

    def __post_init__(self) -> None:
        if not self.scales or any(not 0.2 <= value <= 5.0 for value in self.scales):
            raise ValueError("edge correlation scales are invalid")
        if not self.rotations_degrees or any(abs(value) > 30 for value in self.rotations_degrees):
            raise ValueError("edge correlation rotations are invalid")
        if not 0 < self.min_score <= 1 or not 0 <= self.min_peak_margin <= 1:
            raise ValueError("edge correlation score thresholds are invalid")
        if self.exclusion_radius_px <= 0:
            raise ValueError("edge correlation exclusion radius must be positive")
        if not 0 <= self.canny_low < self.canny_high <= 255:
            raise ValueError("edge correlation Canny thresholds are invalid")
        if not 0.1 <= self.coarse_scale <= 0.75:
            raise ValueError("edge correlation coarse scale is invalid")
        if self.coarse_candidates < 2 or self.refine_radius_px < 2:
            raise ValueError("edge correlation refinement settings are invalid")
        if not 0 < self.confidence <= 1:
            raise ValueError("edge correlation confidence must be within (0, 1]")


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
        if not self.kinds:
            raise ValueError("poi.kinds must not be empty")
        if not self.target_kinds:
            raise ValueError("poi.target_kinds must not be empty")


@dataclass(frozen=True)
class HotkeyConfig:
    previous: int = 0x64
    next: int = 0x66
    skip: int = 0x62
    collected_hold: int = 0x65
    undo: int = 0x68
    toggle_view: int = 0x60
    toggle_lock: int = 0x6E
    quit: int = 0x69
    report_issue: int = 0x6B
    toggle_pause: int = 0x6A
    cycle_target_filter: int = 0x6F
    blacklist_target: int = 0x6D

    def __post_init__(self) -> None:
        values = tuple(self.__dict__.values())
        if any(not 1 <= value <= 0xFE for value in values):
            raise ValueError("navigation.hotkeys values must be Windows virtual-key codes")
        if len(set(values)) != len(values):
            raise ValueError("navigation.hotkeys values must be unique")


def _default_alternative_hotkeys() -> dict[str, int]:
    return {
        "previous": 0x25,  # Left
        "next": 0x27,  # Right
        "skip": 0x28,  # Down
        "collected_hold": 0x20,  # Space
        "undo": 0x26,  # Up
        "toggle_view": 0x4D,  # M
        "toggle_lock": 0x4C,  # L
        "quit": 0x51,  # Q
        "report_issue": 0x52,  # R
        "toggle_pause": 0x50,  # P
        "cycle_target_filter": 0x46,  # F
        "blacklist_target": 0x42,  # B
        "toggle_details": 0x48,  # H
        "previous_page": 0x21,  # PageUp
        "next_page": 0x22,  # PageDown
    }


_ALTERNATIVE_HOTKEY_ACTIONS = frozenset(_default_alternative_hotkeys())


@dataclass(frozen=True)
class AlternativeHotkeyConfig:
    enabled: bool = True
    modifiers: int = 0x0003  # Ctrl + Alt
    bindings: dict[str, int] = field(default_factory=_default_alternative_hotkeys)

    def __post_init__(self) -> None:
        if self.modifiers < 1 or self.modifiers > 0x000F:
            raise ValueError("navigation.alternative_hotkeys.modifiers is invalid")
        if any(not 1 <= int(value) <= 0xFE for value in self.bindings.values()):
            raise ValueError("alternative hotkeys must be Windows virtual-key codes")
        if len(set(self.bindings.values())) != len(self.bindings):
            raise ValueError("alternative hotkey virtual keys must be unique")
        unknown = set(self.bindings) - _ALTERNATIVE_HOTKEY_ACTIONS
        if unknown:
            raise ValueError(
                "unknown alternative hotkey actions: " + ", ".join(sorted(unknown))
            )


@dataclass(frozen=True)
class NavigationConfig:
    enabled: bool = True
    calibration_path: Path = Path("datasets/local/calibration/fontaine.json")
    default_view: str = "hud"
    hud_width: int = 360
    hud_height: int = 180
    hud_state_path: Path = Path("datasets/local/ui/hud_state.json")
    navigation_state_path: Path = Path("datasets/local/ui/navigation_state.json")
    collected_hold_seconds: float = 1.0
    global_hotkeys: bool = True
    numpad_enabled: bool = True
    tray_enabled: bool = True
    max_target_distance_m: float | None = None
    hotkeys: HotkeyConfig = field(default_factory=HotkeyConfig)
    alternative_hotkeys: AlternativeHotkeyConfig = field(
        default_factory=AlternativeHotkeyConfig
    )

    def __post_init__(self) -> None:
        if self.default_view not in {"hud", "map"}:
            raise ValueError("navigation.default_view must be hud or map")
        if self.hud_width < 240 or self.hud_height < 120:
            raise ValueError("navigation HUD dimensions are too small")
        if self.collected_hold_seconds <= 0:
            raise ValueError("navigation.collected_hold_seconds must be positive")
        if self.max_target_distance_m is not None and self.max_target_distance_m <= 0:
            raise ValueError("navigation.max_target_distance_m must be positive")


@dataclass(frozen=True)
class PoiGuidanceConfig:
    enabled: bool = True
    cache_dir: Path = Path("datasets/local/cache/poi")
    refresh_after_days: float = 7.0
    negative_cache_hours: float = 24.0
    max_cache_mb: float = 256.0
    request_timeout_seconds: float = 8.0
    toggle_details: int = 0x67
    previous_page: int = 0x61
    next_page: int = 0x63

    def __post_init__(self) -> None:
        if self.refresh_after_days <= 0 or self.negative_cache_hours <= 0:
            raise ValueError("poi_guidance cache ages must be positive")
        if self.max_cache_mb <= 0 or self.request_timeout_seconds <= 0:
            raise ValueError("poi_guidance size and timeout must be positive")
        keys = (self.toggle_details, self.previous_page, self.next_page)
        if any(not 1 <= value <= 0xFE for value in keys):
            raise ValueError("poi_guidance hotkeys must be Windows virtual-key codes")
        if len(set(keys)) != len(keys):
            raise ValueError("poi_guidance hotkeys must be unique")


@dataclass(frozen=True)
class DataConfig:
    storage_backend: str = "auto"
    database_path: Path = Path("datasets/local/data/genshin_navigator.db")
    backup_dir: Path = Path("datasets/local/backups")
    backup_retention: int = 5
    region_id: str = "fontaine"
    surface_metadata_path: Path | None = Path(
        "datasets/local/references/hoyolab_fontaine_full_n1/metadata.json"
    )
    underground_metadata_path: Path | None = Path(
        "datasets/local/references/hoyolab_fontaine_underground/metadata.json"
    )
    map_id: int = 2
    area_id: int = 8
    lang: str = "ru-ru"
    map_version: str | None = None

    def __post_init__(self) -> None:
        if self.storage_backend not in {"auto", "sqlite", "json"}:
            raise ValueError("data.storage_backend must be auto, sqlite, or json")
        if self.map_id < 1 or self.area_id < 1:
            raise ValueError("data map_id and area_id must be positive")
        if self.backup_retention < 1:
            raise ValueError("data.backup_retention must be positive")


@dataclass(frozen=True)
class ProgressSyncConfig:
    enabled: bool = True
    auth_profile_dir: Path = Path("datasets/local/auth/hoyolab_webview")
    request_timeout_seconds: float = 8.0
    retry_count: int = 1
    min_write_interval_seconds: float = 0.15

    def __post_init__(self) -> None:
        if self.request_timeout_seconds <= 0:
            raise ValueError("progress_sync.request_timeout_seconds must be positive")
        if self.retry_count < 0:
            raise ValueError("progress_sync.retry_count must be non-negative")
        if self.min_write_interval_seconds < 0.15:
            raise ValueError(
                "progress_sync.min_write_interval_seconds must be at least 0.15"
            )


@dataclass(frozen=True)
class PerformanceConfig:
    mode: str = "balanced"
    tracking_interval_seconds: float | None = None
    global_search_interval_seconds: float | None = None
    global_search_max_interval_seconds: float = 1.2
    paused_interval_seconds: float = 0.25
    opencv_threads: int = 2
    show_metrics: bool = True

    def __post_init__(self) -> None:
        if self.mode not in {"quality", "balanced", "low_cpu"}:
            raise ValueError("performance.mode must be quality, balanced, or low_cpu")
        intervals = (
            self.tracking_interval_seconds,
            self.global_search_interval_seconds,
        )
        if any(value is not None and value <= 0 for value in intervals):
            raise ValueError("performance localization intervals must be positive")
        if self.global_search_max_interval_seconds < self.global_search_interval:
            raise ValueError(
                "performance.global_search_max_interval_seconds must not be below the global interval"
            )
        if self.paused_interval_seconds < 0.1:
            raise ValueError("performance.paused_interval_seconds must be at least 0.1")
        if not 0 <= self.opencv_threads <= 32:
            raise ValueError("performance.opencv_threads must be between 0 and 32")

    @property
    def tracking_interval(self) -> float:
        defaults = {"quality": 0.10, "balanced": 0.16, "low_cpu": 0.25}
        return self.tracking_interval_seconds or defaults[self.mode]

    @property
    def global_search_interval(self) -> float:
        defaults = {"quality": 0.15, "balanced": 0.35, "low_cpu": 0.60}
        return self.global_search_interval_seconds or defaults[self.mode]


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
    anchor_localization: AnchorLocalizationConfig = field(
        default_factory=AnchorLocalizationConfig
    )
    motion_fallback: MotionFallbackConfig = field(default_factory=MotionFallbackConfig)
    edge_correlation: EdgeCorrelationConfig = field(default_factory=EdgeCorrelationConfig)
    failure_recorder: FailureRecorderConfig = field(default_factory=FailureRecorderConfig)
    screen_gate: ScreenGateConfig = field(default_factory=ScreenGateConfig)
    poi: PoiConfig = field(default_factory=PoiConfig)
    navigation: NavigationConfig = field(default_factory=NavigationConfig)
    poi_guidance: PoiGuidanceConfig = field(default_factory=PoiGuidanceConfig)
    data: DataConfig = field(default_factory=DataConfig)
    progress_sync: ProgressSyncConfig = field(default_factory=ProgressSyncConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)

    def __post_init__(self) -> None:
        if (
            self.poi.enabled
            and self.data.storage_backend == "json"
            and self.poi.catalog_path is None
        ):
            raise ValueError("poi.catalog_path is required for the JSON data backend")
        if self.poi_guidance.enabled:
            navigation_keys = tuple(self.navigation.hotkeys.__dict__.values())
            guidance_keys = (
                self.poi_guidance.toggle_details,
                self.poi_guidance.previous_page,
                self.poi_guidance.next_page,
            )
            if len(set(navigation_keys + guidance_keys)) != len(
                navigation_keys + guidance_keys
            ):
                raise ValueError(
                    "navigation and poi_guidance hotkeys must not overlap"
                )


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

    anchor_raw = dict(raw.get("anchor_localization", {}))
    anchor_catalog = resolve_optional(anchor_raw.pop("catalog_path", None))
    anchor_templates = {
        str(kind): resolve_optional(value)
        for kind, value in dict(anchor_raw.pop("template_paths", {})).items()
    }
    if any(path is None for path in anchor_templates.values()):
        raise ValueError("anchor_localization template paths must not be null")

    edge_raw = dict(raw.get("edge_correlation", {}))
    edge_scales = tuple(edge_raw.pop("scales", (0.80, 0.83, 0.86)))
    edge_rotations = tuple(
        edge_raw.pop("rotations_degrees", (0.0,))
    )

    poi_raw = dict(raw.get("poi", {}))
    poi_catalog = resolve_optional(poi_raw.pop("catalog_path", None))
    poi_progress_value = poi_raw.pop("progress_path", "artifacts/poi_progress.json")
    poi_progress = Path(poi_progress_value)
    if not poi_progress.is_absolute():
        poi_progress = (config_path.parent / poi_progress).resolve()
    poi_kinds = tuple(poi_raw.pop("kinds", ("chest", "hydroculus", "waypoint")))
    poi_target_kinds = tuple(poi_raw.pop("target_kinds", ("chest",)))

    navigation_raw = dict(raw.get("navigation", {}))
    hotkeys_raw = dict(navigation_raw.pop("hotkeys", {}))
    alternative_hotkeys_raw = dict(
        navigation_raw.pop("alternative_hotkeys", {})
    )
    alternative_bindings = dict(
        alternative_hotkeys_raw.pop("bindings", _default_alternative_hotkeys())
    )
    calibration_value = navigation_raw.pop(
        "calibration_path", "datasets/local/calibration/fontaine.json"
    )
    calibration_path = Path(calibration_value)
    if not calibration_path.is_absolute():
        calibration_path = (config_path.parent / calibration_path).resolve()
    hud_state_value = navigation_raw.pop(
        "hud_state_path", "datasets/local/ui/hud_state.json"
    )
    hud_state_path = Path(hud_state_value)
    if not hud_state_path.is_absolute():
        hud_state_path = (config_path.parent / hud_state_path).resolve()
    navigation_state_value = navigation_raw.pop(
        "navigation_state_path", "datasets/local/ui/navigation_state.json"
    )
    navigation_state_path = Path(navigation_state_value)
    if not navigation_state_path.is_absolute():
        navigation_state_path = (config_path.parent / navigation_state_path).resolve()

    guidance_raw = dict(raw.get("poi_guidance", {}))
    guidance_cache_value = guidance_raw.pop(
        "cache_dir", "datasets/local/cache/poi"
    )
    guidance_cache_dir = Path(guidance_cache_value)
    if not guidance_cache_dir.is_absolute():
        guidance_cache_dir = (config_path.parent / guidance_cache_dir).resolve()
    guidance_hotkeys = dict(guidance_raw.pop("hotkeys", {}))

    data_raw = dict(raw.get("data", {}))
    database_value = data_raw.pop(
        "database_path", "datasets/local/data/genshin_navigator.db"
    )
    database_path = resolve_optional(database_value)
    assert database_path is not None
    backup_dir = resolve_optional(
        data_raw.pop("backup_dir", "datasets/local/backups")
    )
    assert backup_dir is not None
    surface_metadata_path = resolve_optional(
        data_raw.pop(
            "surface_metadata_path",
            "datasets/local/references/hoyolab_fontaine_full_n1/metadata.json",
        )
    )
    underground_metadata_path = resolve_optional(
        data_raw.pop(
            "underground_metadata_path",
            "datasets/local/references/hoyolab_fontaine_underground/metadata.json",
        )
    )
    progress_sync_raw = dict(raw.get("progress_sync", {}))
    auth_profile_value = progress_sync_raw.pop(
        "auth_profile_dir", "datasets/local/auth/hoyolab_webview"
    )
    auth_profile_dir = resolve_optional(auth_profile_value)
    assert auth_profile_dir is not None

    return AppConfig(
        map_path=map_path,
        pyramid_path=pyramid_path,
        debug_map_path=debug_map_path or map_path,
        roi=Roi(**raw["roi"]),
        interval_seconds=float(raw.get("interval_seconds", 0.5)),
        matcher=MatcherConfig(**raw.get("matcher", {})),
        tracker=TrackerConfig(**raw.get("tracker", {})),
        local_search=LocalSearchConfig(**raw.get("local_search", {})),
        anchor_localization=AnchorLocalizationConfig(
            catalog_path=anchor_catalog,
            template_paths={kind: path for kind, path in anchor_templates.items() if path is not None},
            **anchor_raw,
        ),
        motion_fallback=MotionFallbackConfig(**raw.get("motion_fallback", {})),
        edge_correlation=EdgeCorrelationConfig(
            scales=edge_scales,
            rotations_degrees=edge_rotations,
            **edge_raw,
        ),
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
            hud_state_path=hud_state_path,
            navigation_state_path=navigation_state_path,
            hotkeys=HotkeyConfig(**hotkeys_raw),
            alternative_hotkeys=AlternativeHotkeyConfig(
                bindings={str(key): int(value) for key, value in alternative_bindings.items()},
                **alternative_hotkeys_raw,
            ),
            **navigation_raw,
        ),
        poi_guidance=PoiGuidanceConfig(
            cache_dir=guidance_cache_dir,
            toggle_details=int(guidance_hotkeys.pop("toggle_details", 0x67)),
            previous_page=int(guidance_hotkeys.pop("previous_page", 0x61)),
            next_page=int(guidance_hotkeys.pop("next_page", 0x63)),
            **guidance_raw,
        ),
        data=DataConfig(
            database_path=database_path,
            backup_dir=backup_dir,
            surface_metadata_path=surface_metadata_path,
            underground_metadata_path=underground_metadata_path,
            **data_raw,
        ),
        progress_sync=ProgressSyncConfig(
            auth_profile_dir=auth_profile_dir,
            **progress_sync_raw,
        ),
        performance=PerformanceConfig(**raw.get("performance", {})),
    )
