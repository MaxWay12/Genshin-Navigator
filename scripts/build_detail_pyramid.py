from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import cv2
from genshin_navigator.image_io import read_image


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add a genuinely detailed registered surface reference to a pyramid"
    )
    parser.add_argument("base_pyramid", type=Path)
    parser.add_argument("detail_image", type=Path)
    parser.add_argument("registration", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--id", required=True)
    parser.add_argument("--source", choices=("hoyolab", "appsample"), required=True)
    parser.add_argument("--resolution-scale", type=float, required=True)
    parser.add_argument("--min-registration-inliers", type=int, default=20)
    parser.add_argument("--max-registration-error-px", type=float, default=2.0)
    args = parser.parse_args()

    if args.resolution_scale <= 1.0:
        raise ValueError("Detail resolution scale must be greater than 1")
    detail = read_image(str(args.detail_image), cv2.IMREAD_GRAYSCALE)
    if detail is None:
        raise FileNotFoundError(f"Could not read detail image: {args.detail_image}")
    base = json.loads(args.base_pyramid.read_text(encoding="utf-8"))
    registration = json.loads(args.registration.read_text(encoding="utf-8"))
    inliers = int(registration.get("inliers", 0))
    error = float(registration.get("median_error_px", float("inf")))
    if inliers < args.min_registration_inliers:
        raise ValueError(
            f"Detail registration has {inliers} inliers; need {args.min_registration_inliers}"
        )
    if error > args.max_registration_error_px:
        raise ValueError(
            f"Detail registration median error is {error:.3f}px; maximum is "
            f"{args.max_registration_error_px:.3f}px"
        )
    levels = base.get("levels")
    if not isinstance(levels, list) or not levels:
        raise ValueError("Base pyramid has no levels")
    if any(str(item.get("id")) == args.id for item in levels if isinstance(item, dict)):
        raise ValueError(f"Pyramid already contains level {args.id!r}")

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    image_relative = Path(os.path.relpath(args.detail_image.resolve(), output.parent)).as_posix()
    candidate = dict(base)
    candidate["levels"] = [
        *levels,
        {
            "id": args.id,
            "image": image_relative,
            "resolution_scale": args.resolution_scale,
            "map_layer_id": "surface",
            "coordinate_space": "surface_atlas",
            "local_to_canonical": registration["local_to_canonical"],
            "metadata": {
                "source": args.source,
                "purpose": "sparse_ruins_portability_experiment",
                "registration_inliers": inliers,
                "registration_median_error_px": error,
            },
        },
    ]
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
