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
from .scenario_kpis import DEFAULT_KPIS, evaluate_kpis


SUITE_FORMAT_VERSION = 1
LOW_OBSERVABILITY_CLASSIFICATION = "low_observability"
LOW_OBSERVABILITY_WAIVERS = frozenset(
    {
        "tracking_coverage",
        "reacquire_p95_seconds",
        "longest_untracked_streak_seconds",
    }
)


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
        classification = str(item.get("classification", "standard"))
        waived = item.get("waive_failures", [])
        if not isinstance(waived, list) or any(not isinstance(value, str) for value in waived):
            raise ValueError("Benchmark suite waive_failures must be a string list")
        if waived and classification != LOW_OBSERVABILITY_CLASSIFICATION:
            raise ValueError(
                "Failure waivers are only valid for low_observability scenarios"
            )
        unsupported = set(waived) - LOW_OBSERVABILITY_WAIVERS
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise ValueError(f"Unsafe or unknown low-observability waiver: {names}")
    return manifest_path.parent, payload


def _scenario_path(root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


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
        if key == "max_reacquire_seconds":
            # Backward-compatible alias used by the original suite manifests.
            kpis["max_reacquire_p95_seconds"] = float(value)
            continue
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
        failures = evaluate_kpis(metrics, kpis)
        classification = str(item.get("classification", "standard"))
        requested_waivers = set(item.get("waive_failures", []))
        waived_failures = [
            failure for failure in failures if failure in requested_waivers
        ]
        active_failures = [
            failure for failure in failures if failure not in requested_waivers
        ]
        gating = bool(item.get("gating", True))
        passed = not active_failures
        if gating and not passed:
            gating_passed = False
        results.append(
            {
                "name": str(item.get("name") or report.get("name") or scenario_path.name),
                "path": str(item["path"]),
                "gating": gating,
                "classification": classification,
                "passed": passed,
                "failures": active_failures,
                "observed_failures": failures,
                "waived_failures": waived_failures,
                "rationale": str(item.get("rationale") or ""),
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
