from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a scaled reference level from a clean atlas")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--scale", type=float, default=2.0)
    args = parser.parse_args()
    if args.scale <= 0:
        raise ValueError("scale must be positive")

    source = cv2.imread(str(args.source), cv2.IMREAD_COLOR)
    if source is None:
        raise ValueError(f"Could not read source atlas: {args.source}")
    height, width = source.shape[:2]
    scaled = cv2.resize(
        source,
        (round(width * args.scale), round(height * args.scale)),
        interpolation=cv2.INTER_LINEAR,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(args.output), scaled):
        raise OSError(f"Could not write scaled atlas: {args.output}")
    metadata = {
        "source": str(args.source),
        "output": str(args.output),
        "scale": args.scale,
        "source_size": [width, height],
        "output_size": [scaled.shape[1], scaled.shape[0]],
        "local_to_source": [
            [1.0 / args.scale, 0.0, 0.0],
            [0.0, 1.0 / args.scale, 0.0],
            [0.0, 0.0, 1.0],
        ],
    }
    args.output.with_suffix(".json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
