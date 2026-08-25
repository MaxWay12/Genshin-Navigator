from __future__ import annotations

import ctypes
import json
import os
import platform
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import AppConfig
from .pyramid import Locator, PyramidMatcher
from .scenario import evaluate_scenario


SUITE_FORMAT_VERSION = 1
DEFAULT_KPIS: dict[str, float] = {
    "max_false_locks": 0,
    "max_wrong_layer_positions": 0,
    "max_reacquire_seconds": 3.0,
    "max_one_frame_layer_runs": 0,
    "max_stationary_jitter_p95_px": 5.0,
}


def load_suite_manifest(path: str | Path) -> tuple[Path, dict[str, object]]:
    manifest_path = Path(path).resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or int(payload.get("format_version", 0)) != 1:
        raise ValueError("Unsupported benchmark suite format_version")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("Benchmark suite must contain scenarios")
    for item in scenarios:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("Benchmark suite scenario is invalid")
    return manifest_path.parent, payload


def _scenario_path(root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _evaluate_kpis(metrics: dict[str, object], kpis: dict[str, float]) -> list[str]:
    failures: list[str] = []
    if int(metrics.get("false_locks") or 0) > kpis["max_false_locks"]:
        failures.append("false_locks")
    layer_samples = int(metrics.get("layer_samples") or 0)
    layer_accuracy = metrics.get("layer_accuracy")
    wrong_layers = (
        round(layer_samples * (1.0 - float(layer_accuracy)))
        if layer_samples and layer_accuracy is not None else 0
    )
    if wrong_layers > kpis["max_wrong_layer_positions"]:
        failures.append("wrong_layer_positions")
    delays = metrics.get("acquisition_delays_seconds") or []
    if any(
        value is None or float(value) > kpis["max_reacquire_seconds"]
        for value in delays
    ):
        failures.append("reacquire_seconds")
    if int(metrics.get("one_frame_layer_runs") or 0) > kpis["max_one_frame_layer_runs"]:
        failures.append("one_frame_layer_runs")
    jitter = metrics.get("stationary_jitter_p95_px")
    if jitter is not None and float(jitter) > kpis["max_stationary_jitter_p95_px"]:
        failures.append("stationary_jitter_p95_px")
    return failures


def _system_display() -> tuple[tuple[int, int] | None, int | None]:
    try:
        user32 = ctypes.windll.user32
        return (
            (int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))),
            int(user32.GetDpiForSystem()),
        )
    except (AttributeError, OSError):
        return None, None


def compatibility_record(
    config: AppConfig,
    matcher: Locator,
    *,
    ui_scale: str,
    graphics_preset: str,
    passed: bool,
) -> dict[str, object]:
    resolution, dpi = _system_display()
    schema_version = None
    content_version = None
    if config.data.database_path.is_file():
        connection = sqlite3.connect(f"file:{config.data.database_path}?mode=ro", uri=True)
        try:
            schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            row = connection.execute(
                "SELECT content_version FROM content_snapshots WHERE region_id=?",
                (config.data.region_id,),
            ).fetchone()
            content_version = str(row[0]) if row else None
        except sqlite3.DatabaseError:
            pass
        finally:
            connection.close()
    atlas_versions = (
        [level.id for level in matcher.levels]
        if isinstance(matcher, PyramidMatcher) else ["single_reference"]
    )
    return {
        "format_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "windows": {
            "release": platform.release(),
            "build": platform.version(),
        },
        "resolution": list(resolution) if resolution else None,
        "dpi": dpi,
        "genshin_ui_scale": ui_scale or "unknown",
        "graphics_preset": graphics_preset or "unknown",
        "atlas_versions": atlas_versions,
        "content_version": content_version,
        "schema_version": schema_version,
        "passed": passed,
    }


def run_benchmark_suite(
    manifest: str | Path,
    config: AppConfig,
    matcher: Locator,
    *,
    ui_scale: str = "unknown",
    graphics_preset: str = "unknown",
) -> dict[str, object]:
    root, payload = load_suite_manifest(manifest)
    kpis = dict(DEFAULT_KPIS)
    configured = payload.get("kpis") or {}
    if not isinstance(configured, dict):
        raise ValueError("Benchmark suite kpis must be an object")
    for key, value in configured.items():
        if key not in kpis:
            raise ValueError(f"Unknown benchmark KPI: {key}")
        kpis[key] = float(value)
    results: list[dict[str, object]] = []
    gating_passed = True
    scenarios = payload["scenarios"]
    assert isinstance(scenarios, list)
    for item in scenarios:
        assert isinstance(item, dict)
        scenario_path = _scenario_path(root, str(item["path"]))
        report = evaluate_scenario(scenario_path, config, matcher)
        metrics = report["metrics"]
        assert isinstance(metrics, dict)
        failures = _evaluate_kpis(metrics, kpis)
        gating = bool(item.get("gating", True))
        passed = not failures
        if gating and not passed:
            gating_passed = False
        results.append(
            {
                "name": str(item.get("name") or report.get("name") or scenario_path.name),
                "path": str(item["path"]),
                "gating": gating,
                "passed": passed,
                "failures": failures,
                "metrics": metrics,
            }
        )
    return {
        "format_version": SUITE_FORMAT_VERSION,
        "suite": payload.get("name", Path(manifest).stem),
        "passed": gating_passed,
        "kpis": kpis,
        "scenarios": results,
        "compatibility": compatibility_record(
            config,
            matcher,
            ui_scale=ui_scale,
            graphics_preset=graphics_preset,
            passed=gating_passed,
        ),
    }


def write_report_atomic(report: dict[str, object], output: str | Path) -> Path:
    destination = Path(output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination
