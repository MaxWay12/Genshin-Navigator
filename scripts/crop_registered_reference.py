from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Crop a registered reference and preserve its canonical transform"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("registration", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--x", type=int, required=True)
    parser.add_argument("--y", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    args = parser.parse_args()

    source = cv2.imread(str(args.source), cv2.IMREAD_COLOR)
    if source is None:
        raise FileNotFoundError(f"Could not load source image: {args.source}")
    source_height, source_width = source.shape[:2]
    if args.width <= 0 or args.height <= 0:
        raise ValueError("Crop dimensions must be positive")
    if not (
        0 <= args.x < source_width
        and 0 <= args.y < source_height
        and args.x + args.width <= source_width
        and args.y + args.height <= source_height
    ):
        raise ValueError("Crop lies outside the source image")

    registration = json.loads(args.registration.read_text(encoding="utf-8"))
    source_to_canonical = np.asarray(
        registration["local_to_canonical"], dtype=np.float64
    )
    crop_to_source = np.float64(
        [[1.0, 0.0, args.x], [0.0, 1.0, args.y], [0.0, 0.0, 1.0]]
    )
    crop_to_canonical = source_to_canonical @ crop_to_source

    crop = source[args.y : args.y + args.height, args.x : args.x + args.width]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(args.output), crop):
        raise OSError(f"Could not write crop: {args.output}")

    metadata = {
        "source": str(args.source),
        "registration": str(args.registration),
        "crop": {
            "x": args.x,
            "y": args.y,
            "width": args.width,
            "height": args.height,
        },
        "local_to_canonical": crop_to_canonical.tolist(),
    }
    metadata_path = args.output.with_suffix(".json")
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
