from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from genshin_navigator.capture import crop_roi, load_image
from genshin_navigator.config import load_config
from genshin_navigator.pyramid import load_pyramid


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate a pyramid against paired in-game/map screenshots"
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    args = parser.parse_args()
    annotations = json.loads(
        (args.dataset / "annotations.json").read_text(encoding="utf-8")
    )
    config = load_config(args.config)
    matcher = load_pyramid(config.pyramid_path, config.matcher)
    rows = []
    for item in annotations["frames"]:
        minimap = crop_roi(load_image(args.dataset / item["image"]), config.roi)
        started = time.perf_counter()
        result = matcher.locate(minimap)
        elapsed_ms = (time.perf_counter() - started) * 1000
        expected = item["expected"]
        layer_ok = result.map_layer_id == expected["map_layer_id"]
        error = None
        if (
            result.reference_x_px is not None
            and result.reference_y_px is not None
            and "reference_position" in expected
        ):
            position = expected["reference_position"]
            error = math.hypot(
                result.reference_x_px - position[0],
                result.reference_y_px - position[1],
            )
        tolerance = expected.get("tolerance_px", 25)
        rows.append(
            {
                "image": item["image"],
                "found": result.found,
                "layer": result.map_layer_id,
                "layer_ok": layer_ok,
                "reference_position": [result.reference_x_px, result.reference_y_px],
                "error_px": round(error, 2) if error is not None else None,
                "position_ok": error is None or error <= tolerance,
                "confidence": result.confidence,
                "method": result.match_method or "sift",
                "duration_ms": round(elapsed_ms, 2),
            }
        )
    passed = sum(row["found"] and row["layer_ok"] and row["position_ok"] for row in rows)
    report = {"passed": passed, "total": len(rows), "frames": rows}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed == len(rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
