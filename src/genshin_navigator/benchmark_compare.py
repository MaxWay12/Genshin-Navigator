from __future__ import annotations

from .benchmark_suite import run_benchmark_suite
from .config import AppConfig
from .pyramid import Locator, PyramidMatcher


DELTA_METRICS = (
    "required_tracking_coverage",
    "reacquisition_p95_seconds",
    "longest_untracked_streak_seconds",
    "stationary_jitter_p95_px",
    "edge_ambiguity_margin_p05",
    "absolute_fix_age_p95_seconds",
    "mean_processing_ms",
    "p95_processing_ms",
)


def _references(matcher: Locator) -> list[str]:
    if isinstance(matcher, PyramidMatcher):
        return [level.id for level in matcher.levels]
    return ["single_reference"]


def _scenario_map(report: dict[str, object]) -> dict[str, dict[str, object]]:
    scenarios = report.get("scenarios")
    assert isinstance(scenarios, list)
    return {
        str(item["name"]): item
        for item in scenarios
        if isinstance(item, dict) and "name" in item
    }


def _numeric_delta(before: object, after: object) -> float | None:
    if before is None or after is None or isinstance(before, bool) or isinstance(after, bool):
        return None
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return None
    return round(float(after) - float(before), 4)


def compare_benchmark_suites(
    manifest: str,
    baseline_config: AppConfig,
    baseline_matcher: Locator,
    candidate_config: AppConfig,
    candidate_matcher: Locator,
    *,
    regression: tuple[str, AppConfig, Locator] | None = None,
    ui_scale: str = "unknown",
    graphics_preset: str = "unknown",
) -> dict[str, object]:
    baseline = run_benchmark_suite(
        manifest,
        baseline_config,
        baseline_matcher,
        ui_scale=ui_scale,
        graphics_preset=graphics_preset,
    )
    candidate = run_benchmark_suite(
        manifest,
        candidate_config,
        candidate_matcher,
        ui_scale=ui_scale,
        graphics_preset=graphics_preset,
    )
    before_by_name = _scenario_map(baseline)
    after_by_name = _scenario_map(candidate)
    comparisons: list[dict[str, object]] = []
    regressed_scenarios: list[str] = []
    for name in sorted(set(before_by_name) | set(after_by_name)):
        before = before_by_name.get(name)
        after = after_by_name.get(name)
        if before is None or after is None:
            comparisons.append({"name": name, "status": "missing_from_one_run"})
            regressed_scenarios.append(name)
            continue
        before_metrics = before.get("metrics")
        after_metrics = after.get("metrics")
        assert isinstance(before_metrics, dict) and isinstance(after_metrics, dict)
        if bool(before.get("passed")) and not bool(after.get("passed")):
            regressed_scenarios.append(name)
        comparisons.append(
            {
                "name": name,
                "baseline_passed": bool(before.get("passed")),
                "candidate_passed": bool(after.get("passed")),
                "deltas": {
                    key: _numeric_delta(before_metrics.get(key), after_metrics.get(key))
                    for key in DELTA_METRICS
                },
            }
        )

    regression_report = None
    if regression is not None:
        regression_manifest, regression_config, regression_matcher = regression
        regression_report = run_benchmark_suite(
            regression_manifest,
            regression_config,
            regression_matcher,
            ui_scale=ui_scale,
            graphics_preset=graphics_preset,
        )

    candidate_passed = bool(candidate.get("passed"))
    regression_passed = (
        bool(regression_report.get("passed")) if regression_report is not None else None
    )
    accepted = bool(
        candidate_passed and not regressed_scenarios and regression_passed is True
    )
    if accepted:
        verdict = "detail_reference_accepted"
    elif not candidate_passed or regressed_scenarios:
        verdict = "detail_reference_insufficient"
    else:
        verdict = "candidate_passed_regression_not_run"
    return {
        "format_version": 1,
        "verdict": verdict,
        "accepted": accepted,
        "baseline_references": _references(baseline_matcher),
        "candidate_references": _references(candidate_matcher),
        "regressed_scenarios": regressed_scenarios,
        "comparisons": comparisons,
        "baseline": baseline,
        "candidate": candidate,
        "regression": regression_report,
    }
