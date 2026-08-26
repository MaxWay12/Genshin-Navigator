from __future__ import annotations


DEFAULT_KPIS: dict[str, float] = {
    "max_false_locks": 0,
    "max_wrong_layer_positions": 0,
    "min_tracking_coverage": 0.95,
    "max_untracked_streak_seconds": 2.0,
    "max_reacquire_p95_seconds": 3.0,
    "max_one_frame_layer_runs": 0,
    "max_stationary_jitter_p95_px": 5.0,
    "min_position_checkpoints": 0,
}


def evaluate_kpis(
    metrics: dict[str, object], kpis: dict[str, float]
) -> list[str]:
    failures: list[str] = []
    if int(metrics.get("false_locks") or 0) > kpis["max_false_locks"]:
        failures.append("false_locks")

    layer_samples = int(metrics.get("layer_samples") or 0)
    layer_accuracy = metrics.get("layer_accuracy")
    wrong_layers = (
        round(layer_samples * (1.0 - float(layer_accuracy)))
        if layer_samples and layer_accuracy is not None
        else 0
    )
    if wrong_layers > kpis["max_wrong_layer_positions"]:
        failures.append("wrong_layer_positions")

    coverage = metrics.get("required_tracking_coverage")
    if coverage is not None and float(coverage) < kpis["min_tracking_coverage"]:
        failures.append("tracking_coverage")

    longest_untracked = float(metrics.get("longest_untracked_streak_seconds") or 0.0)
    if longest_untracked > kpis["max_untracked_streak_seconds"]:
        failures.append("longest_untracked_streak_seconds")

    delays = metrics.get("acquisition_delays_seconds") or []
    if any(value is None for value in delays):
        failures.append("reacquire_p95_seconds")
    else:
        reacquire_p95 = metrics.get("reacquisition_p95_seconds")
        if (
            reacquire_p95 is not None
            and float(reacquire_p95) > kpis["max_reacquire_p95_seconds"]
        ):
            failures.append("reacquire_p95_seconds")

    if int(metrics.get("one_frame_layer_runs") or 0) > kpis["max_one_frame_layer_runs"]:
        failures.append("one_frame_layer_runs")

    jitter = metrics.get("stationary_jitter_p95_px")
    if jitter is not None and float(jitter) > kpis["max_stationary_jitter_p95_px"]:
        failures.append("stationary_jitter_p95_px")

    checkpoints = int(metrics.get("position_checkpoint_count") or 0)
    if checkpoints < kpis["min_position_checkpoints"]:
        failures.append("position_checkpoints")
    return failures
