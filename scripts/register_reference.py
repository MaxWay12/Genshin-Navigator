from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
from genshin_navigator.image_io import read_image
import numpy as np


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Register a detail reference to a canonical map with SIFT"
    )
    parser.add_argument("canonical", type=Path)
    parser.add_argument("detail", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--match-scale", type=float, default=0.5)
    parser.add_argument("--max-features", type=int, default=60000)
    parser.add_argument("--ratio", type=float, default=0.72)
    return parser


def main() -> int:
    args = _parser().parse_args()
    canonical = read_image(str(args.canonical), cv2.IMREAD_GRAYSCALE)
    detail = read_image(str(args.detail), cv2.IMREAD_GRAYSCALE)
    if canonical is None or detail is None:
        raise FileNotFoundError("Could not load one or both reference images")
    if not 0 < args.match_scale <= 1:
        raise ValueError("--match-scale must be within (0, 1]")

    detail_small = cv2.resize(
        detail,
        None,
        fx=args.match_scale,
        fy=args.match_scale,
        interpolation=cv2.INTER_AREA,
    )
    canonical_mask = np.where(canonical > 18, 255, 0).astype(np.uint8)
    detail_mask = np.where(detail_small > 18, 255, 0).astype(np.uint8)
    sift = cv2.SIFT_create(nfeatures=args.max_features)
    detail_kp, detail_desc = sift.detectAndCompute(detail_small, detail_mask)
    canonical_kp, canonical_desc = sift.detectAndCompute(canonical, canonical_mask)
    if detail_desc is None or canonical_desc is None:
        raise RuntimeError("Not enough features for registration")

    matcher = cv2.BFMatcher(cv2.NORM_L2)
    pairs = matcher.knnMatch(detail_desc, canonical_desc, k=2)
    good = [first for first, second in pairs if first.distance < args.ratio * second.distance]
    if len(good) < 12:
        raise RuntimeError(f"Only {len(good)} good matches found")

    source = np.float32(
        [np.array(detail_kp[item.queryIdx].pt) / args.match_scale for item in good]
    )
    target = np.float32([canonical_kp[item.trainIdx].pt for item in good])
    matrix, inlier_mask = cv2.findHomography(
        source, target, cv2.RANSAC, ransacReprojThreshold=3.0
    )
    if matrix is None or inlier_mask is None:
        raise RuntimeError("Homography estimation failed")
    inliers = int(inlier_mask.sum())
    if inliers < 10:
        raise RuntimeError(f"Only {inliers} registration inliers found")

    projected = cv2.perspectiveTransform(source[:, None, :], matrix)[:, 0, :]
    errors = np.linalg.norm(projected - target, axis=1)
    inlier_errors = errors[inlier_mask.ravel().astype(bool)]
    result = {
        "canonical": str(args.canonical),
        "detail": str(args.detail),
        "canonical_size": [int(canonical.shape[1]), int(canonical.shape[0])],
        "detail_size": [int(detail.shape[1]), int(detail.shape[0])],
        "match_scale": args.match_scale,
        "good_matches": len(good),
        "inliers": inliers,
        "median_error_px": float(np.median(inlier_errors)),
        "local_to_canonical": matrix.tolist(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
