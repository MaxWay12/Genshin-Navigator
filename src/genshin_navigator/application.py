from __future__ import annotations

import time
from datetime import timedelta

from .calibration import load_calibration
from .capture import crop_roi, grab_screen, load_image
from .config import AppConfig
from .data_store import DataBundle, open_data_bundle
from .debug_view import DebugMapView
from .failure_recorder import FailureRecorder
from .hotkeys import HotkeyAction
from .matcher import MinimapMatcher
from .navigation import NavigationController
from .poi_guidance import HoyoLabPoiHintProvider, PoiHintService, SqlitePoiHintRepository
from .pyramid import Locator, PyramidMatcher, load_pyramid
from .screen_gate import MinimapScreenGate
from .tracker import LiveTracker


def build_locator(config: AppConfig) -> Locator:
    if config.pyramid_path is not None:
        return load_pyramid(config.pyramid_path, config.matcher)
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
    )


class LiveApplication:
    """Own the passive live pipeline and its dependencies."""

    def __init__(self, config: AppConfig, locator: Locator | None = None):
        self.config = config
        self.locator = locator or build_locator(config)

    def _view(self, data: DataBundle | None) -> DebugMapView:
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
        )

    def run(self) -> int:
        config, locator = self.config, self.locator
        tracker = LiveTracker(config.tracker)
        recorder = FailureRecorder(config.failure_recorder)
        gate = (
            MinimapScreenGate.from_config(config.screen_gate)
            if config.screen_gate.enabled else None
        )
        view = self._view(load_runtime_data(config))
        previous = time.perf_counter()
        try:
            while True:
                started = time.perf_counter()
                minimap = crop_roi(grab_screen(), config.roi)
                gate_result = gate.check(minimap) if gate else None
                if gate_result is not None and not gate_result.minimap_present:
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
                    print(f"failure incident saved: {incident}", flush=True)
                elapsed = max(now - previous, 1e-6)
                previous = now
                if not view.show(snapshot, 1.0 / elapsed):
                    return 0
                self._wait(started)
        finally:
            incident = recorder.close()
            if incident is not None:
                print(f"partial failure incident saved: {incident}", flush=True)
            view.close()

    def _wait(self, started: float) -> None:
        remaining = self.config.interval_seconds - (time.perf_counter() - started)
        if remaining > 0:
            time.sleep(remaining)
