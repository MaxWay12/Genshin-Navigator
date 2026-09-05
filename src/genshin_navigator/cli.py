from __future__ import annotations

import argparse
import ctypes
import json
import sqlite3
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from .calibration import CalibrationSession
from .application import LiveApplication, build_locator, load_runtime_data
from .benchmark_suite import run_benchmark_suite, write_report_atomic
from .benchmark_compare import compare_benchmark_suites
from .capture import crop_roi, grab_roi, grab_screen, load_image, save_screen
from .config import AppConfig, load_config
from .data_store import (
    DataBundle,
    SqliteDataProvider,
    collect_assets,
)
from .evaluation import evaluate_dataset
from .diagnostics import replay_diagnostic
from .diagnostic_suite import run_diagnostic_suite
from .pyramid import Locator, PyramidMatcher
from .hoyolab_poi import (
    DEFAULT_LABEL_KINDS,
    build_catalog,
    build_space_metrics,
    content_version_for,
    fetch_labels,
    fetch_points,
)
from .hoyolab_auth import HoyoLabAuthSession
from .position import CoordinateSpace, MapPosition, PositionState
from .progress_sync import (
    HoyoLabRemoteProgressProvider,
    ProgressSyncService,
    SqliteProgressSyncStore,
)
from .progress_backup import ProgressTransferService
from .scenario import evaluate_scenario, record_scenario
from .scenario_annotation import annotate_scenario, annotation_image
from .region_manifest import load_region_manifest
from .asset_setup import region_asset_status, setup_region
from .roi_setup import check_config_roi, configure_roi
from . import __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="genshin-navigator",
        description="Passive minimap-based position estimator",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    launcher = subparsers.add_parser("launcher", help="Open Navigator settings and launcher")
    launcher.add_argument("--root", default=None)

    capture = subparsers.add_parser("capture", help="Save a desktop screenshot for ROI setup")
    capture.add_argument("--output", default="artifacts/screen.png")

    locate = subparsers.add_parser("locate", help="Locate once and print JSON")
    locate.add_argument("--config", default="config.json")
    locate.add_argument("--screenshot", help="Use a saved screenshot instead of the live desktop")

    watch = subparsers.add_parser("watch", help="Continuously locate from live screenshots")
    watch.add_argument("--config", default="config.json")

    track = subparsers.add_parser("track", help="Track and show a passive debug map")
    track.add_argument("--config", default="config.json")
    track.add_argument(
        "--regions", default=None,
        help="Optional product region manifest used with --region",
    )
    track.add_argument(
        "--region", default=None,
        help="Product region id from --regions",
    )

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
    record_sequence.add_argument(
        "--expected-region",
        default=None,
        help="Expected region (defaults to data.region_id from config)",
    )
    record_sequence.add_argument("--expected-start-layer")
    record_sequence.add_argument("--expected-end-layer")
    record_sequence.add_argument("--stationary-last-seconds", type=float, default=0.0)
    record_sequence.add_argument(
        "--required-throughout",
        action="store_true",
        help="Require a usable position whenever the minimap is visible",
    )
    record_sequence.add_argument("--ui-scale", default="unknown")
    record_sequence.add_argument("--graphics-preset", default="unknown")

    evaluate_sequence = subparsers.add_parser(
        "evaluate-sequence",
        help="Replay a minimap sequence through the live tracking pipeline",
    )
    evaluate_sequence.add_argument("scenario", help="Directory containing scenario.json")
    evaluate_sequence.add_argument("--config", default="config.json")
    evaluate_sequence.add_argument("--report", help="Optional JSON report path")

    suite = subparsers.add_parser(
        "benchmark-suite", help="Run a versioned golden scenario suite"
    )
    suite.add_argument("manifest")
    suite.add_argument("--config", default="config.json")
    suite.add_argument("--report", required=True)
    suite.add_argument("--ui-scale", default="unknown")
    suite.add_argument("--graphics-preset", default="unknown")

    compare = subparsers.add_parser(
        "benchmark-compare", help="Compare baseline and candidate localization configurations"
    )
    compare.add_argument("manifest")
    compare.add_argument("--baseline-config", required=True)
    compare.add_argument("--candidate-config", required=True)
    compare.add_argument("--report", required=True)
    compare.add_argument("--regression-manifest")
    compare.add_argument("--regression-config")
    compare.add_argument("--ui-scale", default="unknown")
    compare.add_argument("--graphics-preset", default="unknown")

    annotate = subparsers.add_parser(
        "annotate-scenario", help="Add canonical position checkpoints by clicking the atlas"
    )
    annotate.add_argument("scenario")
    annotate.add_argument("--config", default="config.json")
    annotate.add_argument("--region", default=None)
    annotate.add_argument("--layer", default="surface")
    annotate.add_argument("--tolerance-px", type=float, default=35.0)
    annotate.add_argument(
        "--timestamps",
        type=float,
        nargs="*",
        default=(),
        help="Optional target times; N/B jumps between their nearest frames",
    )

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
        "sync-data", help="Atomically update an offline regional data store"
    )
    sync_data.add_argument("--config", default="config.json")
    sync_data.add_argument("--region", default=None)
    sync_data.add_argument("--map-version", default=None)

    data_status = subparsers.add_parser(
        "data-status", help="Show offline data, content, and asset status"
    )
    data_status.add_argument("--config", default="config.json")
    data_status.add_argument("--region", default=None)

    setup = subparsers.add_parser(
        "setup-region",
        help="Download regional map data into the user's local cache",
    )
    setup.add_argument("--config", default="config.json")
    setup.add_argument("--region", default=None)
    setup.add_argument("--yes", action="store_true")
    setup.add_argument("--force", action="store_true")

    setup_status = subparsers.add_parser(
        "setup-status", help="Check regional assets and the minimap capture area"
    )
    setup_status.add_argument("--config", default="config.json")
    setup_status.add_argument("--region", default=None)

    roi_check = subparsers.add_parser(
        "roi-check", help="Check whether the configured minimap area is on screen"
    )
    roi_check.add_argument("--config", default="config.json")

    configure_roi_parser = subparsers.add_parser(
        "configure-roi", help="Select the minimap area without saving a screenshot"
    )
    configure_roi_parser.add_argument("--config", default="config.json")

    hoyolab_login = subparsers.add_parser(
        "hoyolab-login", help="Sign in to HoYoLAB in an isolated WebView2 window"
    )
    hoyolab_login.add_argument("--config", default="config.json")

    hoyolab_logout = subparsers.add_parser(
        "hoyolab-logout", help="Clear the isolated HoYoLAB session"
    )
    hoyolab_logout.add_argument("--config", default="config.json")

    progress_status = subparsers.add_parser(
        "progress-status", help="Show local and remote progress-sync state"
    )
    progress_status.add_argument("--config", default="config.json")
    progress_status.add_argument("--region", default=None)

    progress_sync = subparsers.add_parser(
        "progress-sync", help="Preview and additively sync HoYoLAB progress"
    )
    progress_sync.add_argument("--config", default="config.json")
    progress_sync.add_argument("--region", default=None)
    progress_sync.add_argument(
        "--yes", action="store_true", help="Apply the preview without prompting"
    )
    progress_export = subparsers.add_parser(
        "progress-export", help="Atomically export portable local progress"
    )
    progress_export.add_argument("output")
    progress_export.add_argument("--config", default="config.json")
    progress_export.add_argument("--region", default=None)

    progress_import = subparsers.add_parser(
        "progress-import", help="Preview and import portable local progress"
    )
    progress_import.add_argument("source")
    progress_import.add_argument("--config", default="config.json")
    progress_import.add_argument("--region", default=None)
    progress_import.add_argument("--replace", action="store_true")
    progress_import.add_argument("--yes", action="store_true")
    diagnostic = subparsers.add_parser(
        "diagnostic-record", help="Record an anonymized minimap diagnostic bundle"
    )
    diagnostic.add_argument("--config", default="config.json")
    diagnostic.add_argument("--duration", type=float, default=5.0)
    diagnostic_replay = subparsers.add_parser(
        "replay-diagnostic", help="Replay a diagnostic bundle (format v3 or v4)"
    )
    diagnostic_replay.add_argument("bundle")
    diagnostic_replay.add_argument("--config", default="config.json")
    diagnostic_replay.add_argument("--report", default=None)
    diagnostic_suite = subparsers.add_parser(
        "diagnostic-suite", help="Replay a recovery regression suite of diagnostic bundles"
    )
    diagnostic_suite.add_argument("manifest")
    diagnostic_suite.add_argument("--config", default="config.json")
    diagnostic_suite.add_argument("--report", required=True)
    return parser


def _recording_phase_signal(phase: str) -> None:
    messages = {
        "started": "Recording started.",
        "stationary": "Stationary phase: stop moving now.",
        "finished": "Recording finished.",
    }
    print(messages[phase], flush=True)
    try:
        import winsound

        patterns = {
            "started": [(900, 180)],
            "stationary": [(500, 220), (500, 220)],
            "finished": [(1000, 130), (1200, 130), (1400, 180)],
        }
        for frequency, duration in patterns[phase]:
            winsound.Beep(frequency, duration)
    except (ImportError, RuntimeError, OSError):
        pass


def _runtime_data(config: AppConfig) -> DataBundle | None:
    return load_runtime_data(config)


def _auth_session(config: AppConfig) -> HoyoLabAuthSession:
    return HoyoLabAuthSession(config.progress_sync.auth_profile_dir)


def _progress_sync_service(config: AppConfig) -> ProgressSyncService:
    if not config.progress_sync.enabled:
        raise ValueError("Progress sync is disabled in config")
    if config.data.storage_backend == "json":
        raise ValueError("progress-sync requires data.storage_backend=auto or sqlite")
    data = _runtime_data(config)
    if data is None or data.provider is None:
        raise ValueError("progress-sync requires an initialized SQLite data store")
    cookie_header = _auth_session(config).cookie_header()
    remote = HoyoLabRemoteProgressProvider(
        cookie_header,
        map_id=config.data.map_id,
        lang=config.data.lang,
        timeout_seconds=config.progress_sync.request_timeout_seconds,
        retry_count=config.progress_sync.retry_count,
        min_write_interval_seconds=(
            config.progress_sync.min_write_interval_seconds
        ),
    )
    return ProgressSyncService(
        SqliteProgressSyncStore(config.data.database_path), remote
    )


def _sync_data(config: AppConfig, region_id: str, map_version: str | None) -> dict[str, object]:
    if config.data.storage_backend == "json":
        raise ValueError("sync-data requires data.storage_backend=auto or sqlite")
    if config.data.surface_metadata_path is None:
        raise ValueError("sync-data requires data.surface_metadata_path")
    surface = json.loads(
        config.data.surface_metadata_path.read_text(encoding="utf-8")
    )
    underground = (
        json.loads(config.data.underground_metadata_path.read_text(encoding="utf-8"))
        if config.data.underground_metadata_path is not None
        else None
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
    if region_id == "sumeru" and (stats["skipped_unknown_floor"] or not pois):
        raise ValueError("Incomplete Sumeru catalog; run setup-region before syncing")
    version = content_version_for(
        labels, points, explicit_version=requested_version,
        asset_revision=str(surface.get("revision") or "") or None,
    )
    provider = SqliteDataProvider(
        config.data.database_path,
        backup_dir=config.data.backup_dir,
        backup_retention=config.data.backup_retention,
    )
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
    return build_locator(config)


def _locate_once(config: AppConfig, matcher: Locator, screenshot: str | None) -> dict[str, object]:
    minimap = (
        crop_roi(load_image(screenshot), config.roi)
        if screenshot
        else grab_roi(config.roi)
    )
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
            minimap = grab_roi(config.roi)
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
        if args.command == "launcher":
            from .launcher import run_launcher
            return run_launcher(args.root)
        if args.command == "capture":
            print(save_screen(args.output))
            return 0

        if args.command == "evaluate":
            print(json.dumps(evaluate_dataset(args.dataset), ensure_ascii=False, indent=2))
            return 0

        if args.command == "benchmark-compare":
            baseline_config = load_config(args.baseline_config)
            candidate_config = load_config(args.candidate_config)
            baseline_matcher = _build_matcher(baseline_config)
            candidate_matcher = _build_matcher(candidate_config)
            regression = None
            if bool(args.regression_manifest) != bool(args.regression_config):
                raise ValueError(
                    "--regression-manifest and --regression-config must be provided together"
                )
            if args.regression_manifest:
                regression_config = load_config(args.regression_config)
                regression = (
                    args.regression_manifest,
                    regression_config,
                    _build_matcher(regression_config),
                )
            report = compare_benchmark_suites(
                args.manifest,
                baseline_config,
                baseline_matcher,
                candidate_config,
                candidate_matcher,
                regression=regression,
                ui_scale=args.ui_scale,
                graphics_preset=args.graphics_preset,
            )
            write_report_atomic(report, args.report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["accepted"] else 2

        config_path = args.config
        if args.command == "track" and args.region is not None:
            if args.regions is None:
                raise ValueError("track --region requires --regions")
            entry = load_region_manifest(args.regions).get(args.region)
            config_path = entry.config_path
            args.region = entry.id
        config = load_config(config_path)
        if args.command == "roi-check":
            report = check_config_roi(config).to_dict()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["valid"] else 2
        if args.command == "configure-roi":
            selected = configure_roi(config_path)
            if selected is None:
                print("ROI setup cancelled; config was not changed.")
                return 2
            print(json.dumps({
                "status": "configured",
                "roi": {
                    "left": selected.left,
                    "top": selected.top,
                    "width": selected.width,
                    "height": selected.height,
                },
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "setup-status":
            region_id = args.region or config.data.region_id
            assets = region_asset_status(config, region_id)
            roi = check_config_roi(config).to_dict()
            report = {"ready": bool(assets["ready"] and roi["valid"]), "assets": assets, "roi": roi}
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["ready"] else 2
        if args.command == "setup-region":
            region_id = args.region or config.data.region_id
            if not args.yes:
                print(
                    "This downloads map and POI content from endpoints used by the "
                    "HoYoLAB Interactive Map into datasets/local. The content is not "
                    "part of Genshin Navigator and remains subject to HoYoLAB terms."
                )
                try:
                    answer = input("Continue? [y/N] ").strip().lower()
                except EOFError:
                    answer = ""
                if answer not in {"y", "yes", "д", "да"}:
                    print("Regional data setup cancelled; no changes were made.")
                    return 0
            print(json.dumps(
                setup_region(
                    config,
                    region_id,
                    force=args.force,
                    progress=lambda stage: print(f"[setup] {stage}...", flush=True),
                ),
                ensure_ascii=False,
                indent=2,
            ))
            return 0
        if args.command == "annotate-scenario":
            atlas_path = config.debug_map_path or config.map_path
            if atlas_path is None:
                raise ValueError("annotate-scenario requires debug_map_path or map_path")
            atlas_path = annotation_image(atlas_path, config.pyramid_path, args.layer)
            saved = annotate_scenario(
                args.scenario,
                atlas_path,
                region_id=args.region or config.data.region_id,
                layer_id=args.layer,
                tolerance_px=args.tolerance_px,
                suggested_timestamps=args.timestamps,
            )
            print("Scenario checkpoints saved." if saved else "Annotation cancelled; no changes were made.")
            return 0 if saved else 2
        if args.command == "hoyolab-login":
            if not config.progress_sync.enabled:
                raise ValueError("Progress sync is disabled in config")
            connected = _auth_session(config).login()
            if connected:
                print("HoYoLAB connected. The isolated session will be reused.")
                return 0
            print("HoYoLAB login was closed before authorization.", file=sys.stderr)
            return 2
        if args.command == "hoyolab-logout":
            _auth_session(config).logout()
            print("HoYoLAB session removed.")
            return 0
        if args.command == "progress-status":
            if config.data.storage_backend == "json":
                raise ValueError("progress-status requires the SQLite data backend")
            region_id = args.region or config.data.region_id
            data = _runtime_data(config)
            assert data is not None and data.provider is not None
            report = data.provider.status(
                region_id,
                hint_refresh_after_days=config.poi_guidance.refresh_after_days,
            )
            report["backend"] = data.backend
            report["hoyolab_profile_present"] = _auth_session(config).profile_present
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if args.command == "progress-sync":
            region_id = args.region or config.data.region_id
            service = _progress_sync_service(config)
            plan = service.preview(region_id)
            print(json.dumps({"preview": plan.to_dict()}, ensure_ascii=False, indent=2))
            if not args.yes:
                try:
                    answer = input("Apply this additive sync? [y/N] ").strip().lower()
                except EOFError:
                    answer = ""
                if answer not in {"y", "yes", "д", "да"}:
                    print("Progress sync cancelled; no changes were made.")
                    return 0
            result = service.apply(plan)
            print(json.dumps({"result": result.to_dict()}, ensure_ascii=False, indent=2))
            return 0 if not result.failed_push_ids else 2
        if args.command in {"progress-export", "progress-import"}:
            if config.data.storage_backend == "json":
                raise ValueError(f"{args.command} requires the SQLite data backend")
            region_id = args.region or config.data.region_id
            data = _runtime_data(config)
            if data is None or data.provider is None:
                raise ValueError("Progress transfer requires an initialized SQLite store")
            transfer = ProgressTransferService(
                config.data.database_path,
                backup_dir=config.data.backup_dir,
                backup_retention=config.data.backup_retention,
            )
            if args.command == "progress-export":
                print(transfer.export(args.output, region_id))
                return 0
            plan = transfer.preview_import(
                args.source, region_id, replace=args.replace
            )
            print(json.dumps({"preview": plan.to_dict()}, ensure_ascii=False, indent=2))
            if not args.yes:
                try:
                    answer = input("Apply this progress import? [y/N] ").strip().lower()
                except EOFError:
                    answer = ""
                if answer not in {"y", "yes", "д", "да"}:
                    print("Progress import cancelled; no changes were made.")
                    return 0
            transfer.apply_import(plan)
            print(json.dumps({"result": "success", **plan.to_dict()}, ensure_ascii=False, indent=2))
            return 0
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
            from .asset_setup import PRESETS, region_asset_status
            if region_id in PRESETS:
                report["regional_assets"] = region_asset_status(config, region_id)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if args.command == "record-sequence":
            manifest = record_scenario(
                config,
                args.output,
                args.duration,
                name=args.name,
                expected_region_id=args.expected_region or config.data.region_id,
                expected_start_layer=args.expected_start_layer,
                expected_end_layer=args.expected_end_layer,
                stationary_last_seconds=args.stationary_last_seconds,
                required_throughout=args.required_throughout,
                genshin_ui_scale=args.ui_scale,
                graphics_preset=args.graphics_preset,
                phase_notifier=_recording_phase_signal,
            )
            print(manifest)
            return 0

        if args.command == "track":
            roi_check = check_config_roi(config)
            if not roi_check.valid:
                raise ValueError(roi_check.message)
        matcher = _build_matcher(config)
        if args.command == "diagnostic-record":
            saved = LiveApplication(config, matcher).record_diagnostic(args.duration)
            print(saved)
            return 0
        if args.command == "replay-diagnostic":
            report = replay_diagnostic(args.bundle, config, matcher)
            rendered = json.dumps(report, ensure_ascii=False, indent=2)
            if args.report:
                report_path = Path(args.report).resolve()
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(rendered, encoding="utf-8")
            print(rendered)
            return 0
        if args.command == "diagnostic-suite":
            report = run_diagnostic_suite(args.manifest, config, matcher)
            write_report_atomic(report, args.report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["passed"] else 2
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
        if args.command == "benchmark-suite":
            report = run_benchmark_suite(
                args.manifest,
                config,
                matcher,
                ui_scale=args.ui_scale,
                graphics_preset=args.graphics_preset,
            )
            write_report_atomic(report, args.report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["passed"] else 2
        if args.command == "locate":
            result = _locate_once(config, matcher, args.screenshot)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["found"] else 2

        if args.command == "track":
            return LiveApplication(config, matcher).run()
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
