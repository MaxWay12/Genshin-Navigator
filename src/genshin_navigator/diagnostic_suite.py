from __future__ import annotations

import json
from pathlib import Path

from .config import AppConfig
from .diagnostics import replay_diagnostic
from .pyramid import Locator


def run_diagnostic_suite(
    manifest_path: str | Path,
    config: AppConfig,
    matcher: Locator,
) -> dict[str, object]:
    manifest_file = Path(manifest_path).resolve()
    payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or int(payload.get("format_version", 0)) != 1:
        raise ValueError("Unsupported diagnostic suite format_version")
    bundles = payload.get("bundles")
    if not isinstance(bundles, list) or not bundles:
        raise ValueError("Diagnostic suite contains no bundles")
    criteria = payload.get("criteria", {})
    if not isinstance(criteria, dict):
        raise ValueError("Diagnostic suite criteria must be an object")
    default_require = bool(criteria.get("require_recovery", True))
    default_max = float(criteria.get("max_recovery_seconds", 1.0))
    if default_max <= 0:
        raise ValueError("max_recovery_seconds must be positive")

    results: list[dict[str, object]] = []
    for raw_entry in bundles:
        entry = {"path": raw_entry} if isinstance(raw_entry, str) else raw_entry
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("Each diagnostic suite bundle needs a path")
        bundle_path = (manifest_file.parent / entry["path"]).resolve()
        require_recovery = bool(entry.get("require_recovery", default_require))
        gating = bool(entry.get("gating", True))
        max_recovery = float(entry.get("max_recovery_seconds", default_max))
        report = replay_diagnostic(bundle_path, config, matcher)
        recovered = report["outcome"] == "transient_recovered"
        recovery_seconds = report.get("recovery_seconds")
        passed = (not require_recovery or recovered) and (
            recovery_seconds is None
            or float(recovery_seconds) <= max_recovery
        )
        reasons: list[str] = []
        if require_recovery and not recovered:
            reasons.append("did_not_recover")
        if recovery_seconds is not None and float(recovery_seconds) > max_recovery:
            reasons.append("recovery_too_slow")
        results.append(
            {
                "name": entry.get("name") or bundle_path.name,
                "path": entry["path"],
                "passed": passed,
                "gating": gating,
                "reasons": reasons,
                "outcome": report["outcome"],
                "recovery_seconds": recovery_seconds,
                "recovery_method": report.get("recovery_method"),
                "recovery_reference_id": report.get("recovery_reference_id"),
                "frame_count": report["frame_count"],
            }
        )

    gating_results = [result for result in results if result["gating"]]
    passed_count = sum(bool(result["passed"]) for result in gating_results)
    return {
        "format_version": 1,
        "name": payload.get("name", manifest_file.stem),
        "scope": "recovery_only_no_positional_ground_truth",
        "passed": passed_count == len(gating_results),
        "bundle_count": len(results),
        "gating_count": len(gating_results),
        "informational_count": len(results) - len(gating_results),
        "passed_count": passed_count,
        "failed_count": len(gating_results) - passed_count,
        "criteria": {
            "require_recovery": default_require,
            "max_recovery_seconds": default_max,
        },
        "results": results,
    }
