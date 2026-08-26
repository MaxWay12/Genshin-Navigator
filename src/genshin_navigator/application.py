from __future__ import annotations

import ctypes
import math
import platform
import time
from dataclasses import replace
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .anchor_localization import AnchorLocalizer
from .calibration import load_calibration
from .capture import crop_roi, grab_screen, load_image
from .config import AppConfig
from .data_store import DataBundle, open_data_bundle
from .debug_view import DebugMapView
from .edge_correlation import EdgeCorrelationLocalizer
from .failure_recorder import DiagnosticContext, FailureRecorder
from .hotkeys import HotkeyAction
from .matcher import LocateResult, MinimapMatcher
from .motion_localization import RelativeMotionLocalizer
from .storage_schema import SCHEMA_VERSION
from .navigation import NavigationController
from .poi_guidance import HoyoLabPoiHintProvider, PoiHintService, SqlitePoiHintRepository
from .pyramid import Locator, PyramidMatcher, load_pyramid
from .screen_gate import MinimapScreenGate
from .tracker import LiveTracker
from . import __version__


def build_locator(config: AppConfig) -> Locator:
    if config.pyramid_path is not None:
        anchor_localizer = (
            AnchorLocalizer.from_config(config.anchor_localization)
            if config.anchor_localization.enabled else None
        )
        motion_localizer = (
            RelativeMotionLocalizer(config.motion_fallback, config.data.region_id)
            if config.motion_fallback.enabled else None
        )
        edge_localizer = (
            EdgeCorrelationLocalizer(
                load_image(config.map_path),
                config.edge_correlation,
                config.data.region_id,
            )
            if config.edge_correlation.enabled and config.map_path is not None
            else None
        )
        return load_pyramid(
            config.pyramid_path,
            config.matcher,
            anchor_localizer=anchor_localizer,
            motion_localizer=motion_localizer,
            edge_localizer=edge_localizer,
            minimum_usable_confidence=config.tracker.min_confidence,
        )
    if config.map_path is None:
        raise ValueError("A map_path or pyramid_path is required")
    return MinimapMatcher(load_image(config.map_path), config.matcher)


def load_runtime_data(config: AppConfig) -> DataBundle | None:
    if not config.poi.enabled:
        return None
    return open_data_bundle(
        backend=config.data.storage_backend,
        database_path=config.data.database_path,
        catalog_path=config.poi.catalog_path,
        progress_path=config.poi.progress_path,
        region_id=config.data.region_id,
        backup_dir=config.data.backup_dir,
        backup_retention=config.data.backup_retention,
    )


class LiveApplication:
    """Own the passive live pipeline and its dependencies."""

    def __init__(self, config: AppConfig, locator: Locator | None = None):
        self.config = config
        self.locator = locator or build_locator(config)

    def _view(
        self, data: DataBundle | None, report_callback=None
    ) -> DebugMapView:
        config, locator = self.config, self.locator
        if config.debug_map_path is None:
            raise ValueError("track requires debug_map_path or map_path")
        layer_maps = None
        layer_labels = None
        if isinstance(locator, PyramidMatcher):
            layer_maps = {
                level.map_layer_id: level.matcher.reference_map  # type: ignore[attr-defined]
                for level in locator.levels
                if level.map_layer_id != "surface"
                and hasattr(level.matcher, "reference_map")
            }
            layer_labels = locator.layer_labels
        catalog = data.catalog if data is not None else None
        progress = data.progress if data is not None else None
        navigation = (
            NavigationController(
                catalog,
                progress,
                target_kinds=set(config.poi.target_kinds),
                calibration=load_calibration(config.navigation.calibration_path),
            )
            if catalog is not None and progress is not None and config.navigation.enabled
            else None
        )
        hint_service = None
        if config.poi_guidance.enabled and navigation is not None:
            repository = (
                SqlitePoiHintRepository(
                    config.data.database_path, config.poi_guidance.cache_dir
                )
                if data is not None and data.backend == "sqlite"
                else None
            )
            hint_service = PoiHintService(
                HoyoLabPoiHintProvider(
                    map_id=config.data.map_id,
                    lang=config.data.lang,
                    timeout_seconds=config.poi_guidance.request_timeout_seconds,
                ),
                repository,
                refresh_after=timedelta(days=config.poi_guidance.refresh_after_days),
                negative_after=timedelta(hours=config.poi_guidance.negative_cache_hours),
                max_cache_bytes=round(config.poi_guidance.max_cache_mb * 1024 * 1024),
            )
        hotkeys = {
            HotkeyAction.PREVIOUS: config.navigation.hotkeys.previous,
            HotkeyAction.NEXT: config.navigation.hotkeys.next,
            HotkeyAction.SKIP: config.navigation.hotkeys.skip,
            HotkeyAction.COLLECTED_HOLD: config.navigation.hotkeys.collected_hold,
            HotkeyAction.UNDO: config.navigation.hotkeys.undo,
            HotkeyAction.TOGGLE_VIEW: config.navigation.hotkeys.toggle_view,
            HotkeyAction.TOGGLE_LOCK: config.navigation.hotkeys.toggle_lock,
            HotkeyAction.QUIT: config.navigation.hotkeys.quit,
            HotkeyAction.REPORT_ISSUE: config.navigation.hotkeys.report_issue,
        }
        if config.poi_guidance.enabled:
            hotkeys.update({
                HotkeyAction.TOGGLE_DETAILS: config.poi_guidance.toggle_details,
                HotkeyAction.PREVIOUS_PAGE: config.poi_guidance.previous_page,
                HotkeyAction.NEXT_PAGE: config.poi_guidance.next_page,
            })
        return DebugMapView(
            load_image(config.debug_map_path),
            layer_maps,
            poi_catalog=catalog,
            poi_kinds=set(config.poi.kinds),
            poi_target_kinds=set(config.poi.target_kinds),
            poi_progress=progress,
            navigation=navigation,
            layer_labels=layer_labels,
            default_view=config.navigation.default_view,
            hud_width=config.navigation.hud_width,
            hud_height=config.navigation.hud_height,
            hud_state_path=config.navigation.hud_state_path,
            collected_hold_seconds=config.navigation.collected_hold_seconds,
            global_hotkeys=config.navigation.global_hotkeys,
            hotkey_virtual_keys=hotkeys,
            hint_service=hint_service,
            report_callback=report_callback,
        )

    def _diagnostic_context(self, data: DataBundle | None) -> DiagnosticContext:
        try:
            app_version = version("genshin-navigator")
        except PackageNotFoundError:
            app_version = __version__
        content_version = None
        if data is not None and data.provider is not None:
            content_version = data.provider.status(self.config.data.region_id).get(
                "content_version"
            )
        references = (
            tuple(level.id for level in self.locator.levels)
            if isinstance(self.locator, PyramidMatcher)
            else ("single_reference",)
        )
        resolution = None
        dpi = None
        try:
            user32 = ctypes.windll.user32
            resolution = (
                int(user32.GetSystemMetrics(0)),
                int(user32.GetSystemMetrics(1)),
            )
            dpi = int(user32.GetDpiForSystem())
        except (AttributeError, OSError):
            pass
        return DiagnosticContext(
            app_version=app_version,
            schema_version=SCHEMA_VERSION if data and data.provider else None,
            content_version=str(content_version) if content_version else None,
            reference_versions=references,
            windows_build=platform.version(),
            screen_resolution=resolution,
            dpi=dpi,
        )

    def run(self) -> int:
        config, locator = self.config, self.locator
        tracker = LiveTracker(config.tracker)
        data = load_runtime_data(config)
        recorder = FailureRecorder(
            config.failure_recorder, self._diagnostic_context(data)
        )
        gate = (
            MinimapScreenGate.from_config(config.screen_gate)
            if config.screen_gate.enabled else None
        )
        view = self._view(data, recorder.request_manual_report)
        previous = time.perf_counter()
        try:
            while True:
                started = time.perf_counter()
                minimap = crop_roi(grab_screen(), config.roi)
                gate_result = gate.check(minimap) if gate else None
                if gate_result is not None and not gate_result.minimap_present:
                    if isinstance(locator, PyramidMatcher):
                        locator.reset_continuity()
                    now = time.perf_counter()
                    reason = gate_result.reason or "minimap_not_visible"
                    snapshot = tracker.pause(now, reason)
                    elapsed = max(now - previous, 1e-6)
                    previous = now
                    if not view.show(snapshot, 1.0 / elapsed, paused_reason=reason):
                        return 0
                    self._wait(started)
                    continue
                hint = tracker.position_hint
                localization = (
                    locator.locate_near(minimap, hint, config.local_search)
                    if config.local_search.enabled
                    and hint is not None
                    and isinstance(locator, PyramidMatcher)
                    else locator.locate(minimap)
                )
                now = time.perf_counter()
                snapshot = tracker.update(localization, now)
                was_active = recorder.active
                incident = recorder.observe(minimap, localization, snapshot, now)
                if recorder.active and not was_active:
                    print("tracking interruption; collecting minimap diagnostics...", flush=True)
                if incident is not None:
                    print(f"tracking diagnostic saved: {incident}", flush=True)
                    view.notify(
                        f"Diagnostic: {incident.parent.name}/{incident.name}", 6.0
                    )
                elapsed = max(now - previous, 1e-6)
                previous = now
                if not view.show(snapshot, 1.0 / elapsed):
                    return 0
                self._wait(started)
        finally:
            incident = recorder.close()
            if incident is not None:
                print(f"partial tracking diagnostic saved: {incident}", flush=True)
            view.close()

    def record_diagnostic(self, duration_seconds: float) -> Path:
        if duration_seconds <= 0:
            raise ValueError("Diagnostic duration must be positive")
        config, locator = self.config, self.locator
        data = load_runtime_data(config)
        frame_count = max(2, math.ceil(duration_seconds / config.interval_seconds) + 2)
        recorder = FailureRecorder(
            replace(
                config.failure_recorder,
                enabled=True,
                pre_frames=frame_count,
                post_frames=0,
            ),
            self._diagnostic_context(data),
            automatic=False,
        )
        tracker = LiveTracker(config.tracker)
        gate = (
            MinimapScreenGate.from_config(config.screen_gate)
            if config.screen_gate.enabled else None
        )
        started_at = time.perf_counter()
        last_timestamp = started_at
        while last_timestamp - started_at < duration_seconds:
            loop_started = time.perf_counter()
            minimap = crop_roi(grab_screen(), config.roi)
            gate_result = gate.check(minimap) if gate else None
            last_timestamp = time.perf_counter()
            if gate_result is not None and not gate_result.minimap_present:
                if isinstance(locator, PyramidMatcher):
                    locator.reset_continuity()
                reason = gate_result.reason or "minimap_not_visible"
                localization = LocateResult(found=False, reason=reason)
                snapshot = tracker.pause(last_timestamp, reason)
            else:
                hint = tracker.position_hint
                localization = (
                    locator.locate_near(minimap, hint, config.local_search)
                    if config.local_search.enabled
                    and hint is not None
                    and isinstance(locator, PyramidMatcher)
                    else locator.locate(minimap)
                )
                snapshot = tracker.update(localization, last_timestamp)
            recorder.observe(minimap, localization, snapshot, last_timestamp)
            self._wait(loop_started)
        saved = recorder.save_buffered_manual_report(last_timestamp)
        if saved is None:
            raise RuntimeError("No diagnostic frames were recorded")
        return saved

    def _wait(self, started: float) -> None:
        remaining = self.config.interval_seconds - (time.perf_counter() - started)
        if remaining > 0:
            time.sleep(remaining)
