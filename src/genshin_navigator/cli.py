from __future__ import annotations

import argparse
import ctypes
import json
import sqlite3
import sys
import time
from datetime import timedelta
from pathlib import Path

import cv2
import numpy as np

from .calibration import CalibrationSession, load_calibration
from .capture import crop_roi, grab_screen, load_image, save_screen
from .config import AppConfig, load_config
from .debug_view import DebugMapView
from .data_store import (
    DataBundle,
    SqliteDataProvider,
    collect_assets,
    open_data_bundle,
)
from .evaluation import evaluate_dataset
from .failure_recorder import FailureRecorder
from .hotkeys import HotkeyAction
from .matcher import MinimapMatcher
from .navigation import NavigationController
from .pyramid import Locator, PyramidMatcher, load_pyramid
from .hoyolab_poi import (
    DEFAULT_LABEL_KINDS,
    build_catalog,
    build_space_metrics,
    content_version_for,
    fetch_labels,
    fetch_points,
)
from .position import CoordinateSpace, MapPosition, PositionState
from .poi_guidance import (
    HoyoLabPoiHintProvider,
    PoiHintService,
    SqlitePoiHintRepository,
)
from .screen_gate import MinimapScreenGate
from .scenario import evaluate_scenario, record_scenario
from .tracker import LiveTracker


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="genshin-navigator",
        description="Passive minimap-based position estimator",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture", help="Save a desktop screenshot for ROI setup")
    capture.add_argument("--output", default="artifacts/screen.png")

    locate = subparsers.add_parser("locate", help="Locate once and print JSON")
    locate.add_argument("--config", default="config.json")
    locate.add_argument("--screenshot", help="Use a saved screenshot instead of the live desktop")

    watch = subparsers.add_parser("watch", help="Continuously locate from live screenshots")
    watch.add_argument("--config", default="config.json")

    track = subparsers.add_parser("track", help="Track and show a passive debug map")
    track.add_argument("--config", default="config.json")

    evaluate = subparsers.add_parser("evaluate", help="Measure localization on an annotated dataset")
    evaluate.add_argument("dataset", help="Directory containing annotations.json")

    record_sequence = subparsers.add_parser(
        "record-sequence",
        help="Record a privacy-safe minimap sequence for stateful evaluation",
    )
    record_sequence.add_argument("output", help="New scenario directory")
    record_sequence.add_argument("--config", default="config.json")
    record_sequence.add_argument("--duration", type=float, required=True)
    record_sequence.add_argument("--name", default="scenario")
    record_sequence.add_argument("--expected-region", default="fontaine")
    record_sequence.add_argument("--expected-start-layer")
    record_sequence.add_argument("--expected-end-layer")
    record_sequence.add_argument("--stationary-last-seconds", type=float, default=0.0)

    evaluate_sequence = subparsers.add_parser(
        "evaluate-sequence",
        help="Replay a minimap sequence through the live tracking pipeline",
    )
    evaluate_sequence.add_argument("scenario", help="Directory containing scenario.json")
    evaluate_sequence.add_argument("--config", default="config.json")
    evaluate_sequence.add_argument("--report", help="Optional JSON report path")

    calibrate = subparsers.add_parser(
        "calibrate-distance",
        help="Interactively calibrate HoYoLAB world units to displayed game meters",
    )
    calibrate.add_argument("--config", default="config.json")
    calibrate.add_argument(
        "--output", default="datasets/local/calibration/fontaine.json"
    )
    calibrate.add_argument("--samples", type=int, default=3)

    sync_data = subparsers.add_parser(
        "sync-data", help="Atomically update the offline Fontaine data store"
    )
    sync_data.add_argument("--config", default="config.json")
    sync_data.add_argument("--region", default=None)
    sync_data.add_argument("--map-version", default=None)

    data_status = subparsers.add_parser(
        "data-status", help="Show offline data, content, and asset status"
    )
    data_status.add_argument("--config", default="config.json")
    data_status.add_argument("--region", default=None)
    return parser


def _runtime_data(config: AppConfig) -> DataBundle | None:
    if not config.poi.enabled:
        return None
    return open_data_bundle(
        backend=config.data.storage_backend,
        database_path=config.data.database_path,
        catalog_path=config.poi.catalog_path,
        progress_path=config.poi.progress_path,
        region_id=config.data.region_id,
    )


def _sync_data(config: AppConfig, region_id: str, map_version: str | None) -> dict[str, object]:
    if config.data.storage_backend == "json":
        raise ValueError("sync-data requires data.storage_backend=auto or sqlite")
    if config.data.surface_metadata_path is None or config.data.underground_metadata_path is None:
        raise ValueError(
            "sync-data requires data.surface_metadata_path and underground_metadata_path"
        )
    surface = json.loads(
        config.data.surface_metadata_path.read_text(encoding="utf-8")
    )
    underground = json.loads(
        config.data.underground_metadata_path.read_text(encoding="utf-8")
    )
    if str(surface.get("region_id", region_id)) != region_id:
        raise ValueError("Surface metadata region does not match requested region")
    requested_version = map_version or config.data.map_version
    labels = fetch_labels(
        map_id=config.data.map_id, lang=config.data.lang, map_version=requested_version
    )
    points = fetch_points(
        map_id=config.data.map_id, lang=config.data.lang, map_version=requested_version
    )
    pois, stats = build_catalog(
        points, labels, surface, underground,
        area_id=config.data.area_id, label_kinds=DEFAULT_LABEL_KINDS,
    )
    metrics = build_space_metrics(surface, underground)
    version = content_version_for(
        labels, points, explicit_version=requested_version,
        asset_revision=str(surface.get("revision") or "") or None,
    )
    provider = SqliteDataProvider(config.data.database_path)
    if provider.is_empty(region_id) and config.poi.catalog_path is not None and config.poi.catalog_path.exists():
        provider.import_legacy(
            config.poi.catalog_path, config.poi.progress_path, region_id=region_id
        )
    assets = collect_assets(
        region_id,
        config.data.surface_metadata_path,
        config.data.underground_metadata_path,
        pyramid_path=config.pyramid_path,
    )
    provider.replace_content(
        region_id, pois, metrics, content_version=version, assets=assets
    )
    status = provider.status(
        region_id,
        hint_refresh_after_days=config.poi_guidance.refresh_after_days,
    )
    status["sync_stats"] = stats
    return status


def _build_matcher(config: AppConfig) -> Locator:
    if config.pyramid_path is not None:
        return load_pyramid(config.pyramid_path, config.matcher)
    assert config.map_path is not None
    return MinimapMatcher(load_image(config.map_path), config.matcher)


def _locate_once(config: AppConfig, matcher: Locator, screenshot: str | None) -> dict[str, object]:
    frame = load_image(screenshot) if screenshot else grab_screen()
    minimap = crop_roi(frame, config.roi)
    timestamp = time.time()
    observation = matcher.locate(minimap)
    result = observation.to_dict()
    result["source"] = str(Path(screenshot).resolve()) if screenshot else "desktop"
    result["timestamp"] = timestamp
    result["position"] = None
    if observation.found and observation.x_px is not None and observation.y_px is not None:
        layer_id = observation.map_layer_id or "surface"
        coordinate_space = observation.coordinate_space or (
            CoordinateSpace.SURFACE_ATLAS
            if layer_id == "surface"
            else CoordinateSpace.LAYER_LOCAL
        )
        result["position"] = MapPosition(
            region_id=observation.region_id or "unknown",
            layer_id=layer_id,
            coordinate_space=coordinate_space,
            x=float(observation.x_px),
            y=float(observation.y_px),
            confidence=observation.confidence,
            state=PositionState.TRACKING,
            timestamp=timestamp,
            reference_id=observation.reference_id,
        ).to_dict()
    return result


def _run_calibration(
    config: AppConfig, matcher: Locator, output: str | Path, required_samples: int
) -> int:
    data = _runtime_data(config)
    if data is None:
        raise ValueError("calibrate-distance requires enabled POI data with metrics")
    catalog = data.catalog
    session = CalibrationSession(catalog, required_samples=required_samples)
    tracker = LiveTracker(config.tracker)
    screen_gate = (
        MinimapScreenGate.from_config(config.screen_gate)
        if config.screen_gate.enabled
        else None
    )
    output_path = Path(output).resolve()
    window = "Genshin Navigator - Distance Calibration"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window, 760, 270)
    try:
        cv2.setWindowProperty(window, cv2.WND_PROP_TOPMOST, 1)
    except cv2.error:
        pass
    stage = "start"
    start_position: MapPosition | None = None
    last_fresh: MapPosition | None = None
    last_fresh_seen = 0.0
    meter_input = ""
    message = ""
    f8_was_down = False
    try:
        while True:
            loop_started = time.perf_counter()
            minimap = crop_roi(grab_screen(), config.roi)
            gate = screen_gate.check(minimap) if screen_gate else None
            if gate is not None and not gate.minimap_present:
                snapshot = tracker.pause(loop_started, gate.reason or "minimap_not_visible")
            else:
                hint = tracker.position_hint
                result = (
                    matcher.locate_near(minimap, hint, config.local_search)
                    if config.local_search.enabled
                    and hint is not None
                    and isinstance(matcher, PyramidMatcher)
                    else matcher.locate(minimap)
                )
                snapshot = tracker.update(result, loop_started)
            if (
                snapshot.position is not None
                and snapshot.state is PositionState.TRACKING
                and not snapshot.stale
                and snapshot.position.coordinate_space is CoordinateSpace.SURFACE_ATLAS
            ):
                last_fresh = snapshot.position
                last_fresh_seen = loop_started

            panel = np.full((270, 760, 3), (18, 18, 18), dtype=np.uint8)
            lines = [
                f"Fontaine distance calibration  sample {len(session.samples) + 1}/{required_samples}",
                "Use a flat 100-300 m route and the in-game navigation distance.",
            ]
            if stage == "start":
                lines += [
                    "Stand at the start in Genshin and press F8 without Alt-Tab.",
                    "The last fresh surface fix is used; no full-screen image is saved.",
                ]
            elif stage == "distance":
                lines += [
                    "Type the distance shown by the game (100-300), then press Enter.",
                    f"distance: {meter_input or '_'} m",
                ]
            else:
                lines += [
                    "Walk to the destination in Genshin and press F8 without Alt-Tab.",
                    "Esc cancels. The last valid calibration is never overwritten on failure.",
                ]
            if message:
                lines.append(message)
            age = loop_started - last_fresh_seen if last_fresh is not None else float("inf")
            lines.append(
                f"tracker={snapshot.state.value}  fresh surface fix age="
                + (f"{age:.1f}s" if last_fresh is not None else "none")
            )
            for index, line in enumerate(lines):
                cv2.putText(
                    panel, line, (18, 32 + index * 31), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (220, 220, 220), 1, cv2.LINE_AA,
                )
            cv2.imshow(window, panel)
            key = cv2.waitKey(1) & 0xFF
            f8_down = bool(ctypes.windll.user32.GetAsyncKeyState(0x77) & 0x8000)
            f8_pressed = f8_down and not f8_was_down
            f8_was_down = f8_down
            capture_pressed = f8_pressed or key in (ord("c"), ord("C"))
            if key == 27:
                session.write_draft(output_path, error="cancelled")
                return 130
            if stage == "start" and capture_pressed:
                if last_fresh is None or age > 15.0:
                    message = "No recent fresh surface position; return to Genshin briefly."
                else:
                    start_position = last_fresh
                    stage = "distance"
                    message = "Start captured."
            elif stage == "distance":
                if ord("0") <= key <= ord("9") and len(meter_input) < 3:
                    meter_input += chr(key)
                elif key in (8, 127):
                    meter_input = meter_input[:-1]
                elif key in (10, 13):
                    shown = int(meter_input or "0")
                    if 100 <= shown <= 300:
                        stage = "end"
                        message = f"Distance {shown} m accepted."
                    else:
                        message = "Distance must be between 100 and 300 m."
            elif stage == "end" and capture_pressed:
                if last_fresh is None or age > 15.0:
                    message = "No recent fresh surface position; return to Genshin briefly."
                else:
                    assert start_position is not None
                    sample = session.add_sample(start_position, last_fresh, float(meter_input))
                    session.write_draft(output_path)
                    message = f"Sample factor: {sample.meters_per_world_unit:.5f} m/world-unit"
                    if len(session.samples) >= required_samples:
                        try:
                            calibration = session.result()
                        except ValueError as error:
                            session.write_draft(output_path, error=str(error))
                            raise
                        calibration.save_atomic(output_path)
                        print(json.dumps(calibration.to_dict(), ensure_ascii=False, indent=2))
                        return 0
                    stage = "start"
                    start_position = None
                    meter_input = ""
            remaining = config.interval_seconds - (time.perf_counter() - loop_started)
            if remaining > 0:
                time.sleep(remaining)
    finally:
        try:
            cv2.destroyWindow(window)
        except cv2.error:
            pass


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "capture":
            print(save_screen(args.output))
            return 0

        if args.command == "evaluate":
            print(json.dumps(evaluate_dataset(args.dataset), ensure_ascii=False, indent=2))
            return 0

        config = load_config(args.config)
        if args.command == "sync-data":
            region_id = args.region or config.data.region_id
            print(json.dumps(
                _sync_data(config, region_id, args.map_version),
                ensure_ascii=False, indent=2,
            ))
            return 0
        if args.command == "data-status":
            region_id = args.region or config.data.region_id
            if config.data.storage_backend == "json":
                data = _runtime_data(config)
                assert data is not None
                report = {
                    "backend": "json", "schema_version": None,
                    "region_id": region_id, "poi_count": len(data.catalog.pois),
                    "space_count": len(data.catalog.metrics),
                    "collected_count": len(data.progress.collected_ids),
                    "poi_guidance_cache": "disabled_in_json_backend",
                }
            else:
                data = _runtime_data(config)
                assert data is not None and data.provider is not None
                report = data.provider.status(
                    region_id,
                    hint_refresh_after_days=config.poi_guidance.refresh_after_days,
                )
                report["backend"] = data.backend
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if args.command == "record-sequence":
            manifest = record_scenario(
                config,
                args.output,
                args.duration,
                name=args.name,
                expected_region_id=args.expected_region,
                expected_start_layer=args.expected_start_layer,
                expected_end_layer=args.expected_end_layer,
                stationary_last_seconds=args.stationary_last_seconds,
            )
            print(manifest)
            return 0

        matcher = _build_matcher(config)
        if args.command == "calibrate-distance":
            return _run_calibration(config, matcher, args.output, args.samples)
        if args.command == "evaluate-sequence":
            report = evaluate_scenario(args.scenario, config, matcher)
            rendered = json.dumps(report, ensure_ascii=False, indent=2)
            if args.report:
                report_path = Path(args.report).resolve()
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(rendered, encoding="utf-8")
            print(rendered)
            return 0 if report["passed"] is not False else 2

        if args.command == "locate":
            result = _locate_once(config, matcher, args.screenshot)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["found"] else 2

        if args.command == "track":
            if config.debug_map_path is None:
                raise ValueError("track requires debug_map_path or map_path")
            tracker = LiveTracker(config.tracker)
            recorder = FailureRecorder(config.failure_recorder)
            screen_gate = (
                MinimapScreenGate.from_config(config.screen_gate)
                if config.screen_gate.enabled
                else None
            )
            layer_maps = None
            layer_labels = None
            if isinstance(matcher, PyramidMatcher):
                layer_maps = {
                    level.map_layer_id: level.matcher.reference_map  # type: ignore[attr-defined]
                    for level in matcher.levels
                    if level.map_layer_id != "surface"
                    and hasattr(level.matcher, "reference_map")
                }
                layer_labels = matcher.layer_labels
            data = _runtime_data(config)
            poi_catalog = data.catalog if data is not None else None
            poi_progress = data.progress if data is not None else None
            navigation = (
                NavigationController(
                    poi_catalog,
                    poi_progress,
                    target_kinds=set(config.poi.target_kinds),
                    calibration=load_calibration(config.navigation.calibration_path),
                )
                if poi_catalog is not None
                and poi_progress is not None
                and config.navigation.enabled
                else None
            )
            hint_service = None
            if config.poi_guidance.enabled and navigation is not None:
                hint_repository = (
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
                    hint_repository,
                    refresh_after=timedelta(days=config.poi_guidance.refresh_after_days),
                    negative_after=timedelta(hours=config.poi_guidance.negative_cache_hours),
                    max_cache_bytes=round(config.poi_guidance.max_cache_mb * 1024 * 1024),
                )
            hotkey_virtual_keys = {
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
                hotkey_virtual_keys.update({
                    HotkeyAction.TOGGLE_DETAILS: config.poi_guidance.toggle_details,
                    HotkeyAction.PREVIOUS_PAGE: config.poi_guidance.previous_page,
                    HotkeyAction.NEXT_PAGE: config.poi_guidance.next_page,
                })
            view = DebugMapView(
                load_image(config.debug_map_path),
                layer_maps,
                poi_catalog=poi_catalog,
                poi_kinds=set(config.poi.kinds),
                poi_target_kinds=set(config.poi.target_kinds),
                poi_progress=poi_progress,
                navigation=navigation,
                layer_labels=layer_labels,
                default_view=config.navigation.default_view,
                hud_width=config.navigation.hud_width,
                hud_height=config.navigation.hud_height,
                hud_state_path=config.navigation.hud_state_path,
                collected_hold_seconds=config.navigation.collected_hold_seconds,
                global_hotkeys=config.navigation.global_hotkeys,
                hotkey_virtual_keys=hotkey_virtual_keys,
                hint_service=hint_service,
            )
            previous = time.perf_counter()
            try:
                while True:
                    started = time.perf_counter()
                    frame = grab_screen()
                    minimap = crop_roi(frame, config.roi)
                    gate_result = screen_gate.check(minimap) if screen_gate else None
                    if gate_result is not None and not gate_result.minimap_present:
                        now = time.perf_counter()
                        reason = gate_result.reason or "minimap_not_visible"
                        snapshot = tracker.pause(now, reason)
                        elapsed = max(now - previous, 1e-6)
                        previous = now
                        if not view.show(snapshot, 1.0 / elapsed, paused_reason=reason):
                            return 0
                        remaining = config.interval_seconds - (time.perf_counter() - started)
                        if remaining > 0:
                            time.sleep(remaining)
                        continue
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
                    # A featureless frame (most often open water) must not trigger
                    # an immediate scan of every surface and underground level.
                    # Keep retrying the active layer until the tracker genuinely
                    # expires; the following iteration will then have no hint and
                    # perform one global reacquisition.
                    now = time.perf_counter()
                    snapshot = tracker.update(localization, now)
                    recorder_was_active = recorder.active
                    incident = recorder.observe(minimap, localization, snapshot, now)
                    if recorder.active and not recorder_was_active:
                        print(
                            "tracking interruption; collecting minimap diagnostics...",
                            flush=True,
                        )
                    if incident is not None:
                        print(f"failure incident saved: {incident}", flush=True)
                    elapsed = max(now - previous, 1e-6)
                    previous = now
                    if not view.show(snapshot, 1.0 / elapsed):
                        return 0
                    remaining = config.interval_seconds - (time.perf_counter() - started)
                    if remaining > 0:
                        time.sleep(remaining)
            finally:
                incident = recorder.close()
                if incident is not None:
                    print(f"partial failure incident saved: {incident}", flush=True)
                view.close()

        while True:
            result = _locate_once(config, matcher, None)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            time.sleep(config.interval_seconds)
    except KeyboardInterrupt:
        return 130
    except (
        OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError,
        sqlite3.DatabaseError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
