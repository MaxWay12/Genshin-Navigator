from __future__ import annotations

import json
import math
import statistics
import time
from pathlib import Path

from .capture import load_image
from .config import MatcherConfig
from .matcher import MinimapMatcher


def evaluate_dataset(dataset_dir: str | Path, config: MatcherConfig | None = None) -> dict[str, object]:
    root = Path(dataset_dir).resolve()
    with (root / "annotations.json").open("r", encoding="utf-8") as stream:
        annotations = json.load(stream)

    matcher = MinimapMatcher(load_image(root / annotations["reference"]), config)
    rows: list[dict[str, object]] = []
    errors: list[float] = []
    durations_ms: list[float] = []

    for item in annotations["frames"]:
        started = time.perf_counter()
        result = matcher.locate(load_image(root / item["image"]))
        duration_ms = (time.perf_counter() - started) * 1000
        durations_ms.append(duration_ms)

        error_px: float | None = None
        if result.found and result.x_px is not None and result.y_px is not None:
            expected = item["expected"]
            error_px = math.hypot(
                result.x_px - float(expected["x_px"]),
                result.y_px - float(expected["y_px"]),
            )
            errors.append(error_px)

        rows.append(
            {
                "image": item["image"],
                "found": result.found,
                "x_px": result.x_px,
                "y_px": result.y_px,
                "confidence": result.confidence,
                "error_px": round(error_px, 3) if error_px is not None else None,
                "duration_ms": round(duration_ms, 2),
                "reason": result.reason,
            }
        )

    total = len(rows)
    found = len(errors)
    sorted_errors = sorted(errors)
    p95_index = max(0, math.ceil(len(sorted_errors) * 0.95) - 1) if sorted_errors else 0
    return {
        "dataset": str(root),
        "total": total,
        "found": found,
        "success_rate": round(found / total, 4) if total else 0.0,
        "median_error_px": round(statistics.median(errors), 3) if errors else None,
        "p95_error_px": round(sorted_errors[p95_index], 3) if errors else None,
        "max_error_px": round(max(errors), 3) if errors else None,
        "mean_duration_ms": round(statistics.mean(durations_ms), 2) if durations_ms else None,
        "frames": rows,
    }

