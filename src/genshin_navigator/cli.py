from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .capture import crop_roi, grab_screen, load_image, save_screen
from .config import AppConfig, load_config
from .debug_view import DebugMapView
from .evaluation import evaluate_dataset
from .failure_recorder import FailureRecorder
from .matcher import MinimapMatcher
from .pyramid import Locator, PyramidMatcher, load_pyramid
from .poi import PoiCatalog, PoiProgress
from .position import CoordinateSpace, MapPosition, PositionState
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
    return parser


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
            if isinstance(matcher, PyramidMatcher):
                layer_maps = {
                    level.map_layer_id: level.matcher.reference_map  # type: ignore[attr-defined]
                    for level in matcher.levels
                    if level.map_layer_id != "surface"
                    and hasattr(level.matcher, "reference_map")
                }
            poi_catalog = (
                PoiCatalog.load(config.poi.catalog_path)
                if config.poi.enabled and config.poi.catalog_path is not None
                else None
            )
            poi_progress = PoiProgress.load(config.poi.progress_path) if poi_catalog else None
            view = DebugMapView(
                load_image(config.debug_map_path),
                layer_maps,
                poi_catalog=poi_catalog,
                poi_kinds=set(config.poi.kinds),
                poi_target_kinds=set(config.poi.target_kinds),
                poi_progress=poi_progress,
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
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
