from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def _relative_path(path: Path, parent: Path) -> str:
    return Path(os.path.relpath(path.resolve(), parent.resolve())).as_posix()


def compose_local_to_canonical(
    world_to_canonical: list[list[float]],
    local_to_world: list[list[float]],
) -> list[list[float]]:
    # HoYoLAB underground overlay metadata uses the opposite world-Y direction
    # from the surface tile coordinate system. X is shared, but Y must be
    # reflected before applying the surface atlas transform.
    underground_world_to_surface_world = np.asarray(
        [[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    result = (
        np.asarray(world_to_canonical, dtype=np.float64)
        @ underground_world_to_surface_world
        @ np.asarray(local_to_world, dtype=np.float64)
    )
    return result.tolist()


def build_underground_pyramid(
    surface_metadata_path: Path,
    base_pyramid_path: Path,
    underground_metadata_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    surface_metadata = json.loads(surface_metadata_path.read_text(encoding="utf-8"))
    base = json.loads(base_pyramid_path.read_text(encoding="utf-8"))
    underground = json.loads(underground_metadata_path.read_text(encoding="utf-8"))
    region_id = str(surface_metadata.get("region_id", "fontaine"))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    surface_atlas = surface_metadata_path.parent / "atlas.png"
    levels = []
    replaced_surface = False
    for raw_level in base["levels"]:
        level = dict(raw_level)
        if region_id == "fontaine" and level.get("map_layer_id", base.get("default_map_layer_id", "surface")) == "surface" and not replaced_surface:
            level.update(
                {
                    "id": "fontaine_surface_full_n1",
                    "image": _relative_path(surface_atlas, output_path.parent),
                    "resolution_scale": 1.0,
                    "local_to_canonical": np.eye(3).tolist(),
                }
            )
            replaced_surface = True
        else:
            image = Path(level["image"])
            if not image.is_absolute():
                image = base_pyramid_path.parent / image
            level["image"] = _relative_path(image, output_path.parent)
        levels.append(level)

    world_to_canonical = surface_metadata["world_to_atlas"]
    skipped = []
    for group in underground["groups"]:
        for floor in group["floors"]:
            image_path = underground_metadata_path.parent / floor["path"]
            image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                skipped.append({"layer_id": floor["layer_id"], "reason": "unreadable"})
                continue
            detector = cv2.SIFT_create(nfeatures=500)
            keypoints = detector.detect(image, None)
            if len(keypoints) < 8:
                skipped.append(
                    {
                        "layer_id": floor["layer_id"],
                        "reason": "not_enough_features",
                        "features": len(keypoints),
                    }
                )
                continue
            levels.append(
                {
                    "id": f"{region_id}_ug_g{group['group_id']}_f{floor['floor_id']}",
                    "image": _relative_path(image_path, output_path.parent),
                    "resolution_scale": 2.0,
                    "map_layer_id": floor["layer_id"],
                    "coordinate_space": "layer_local",
                    "template_fallback": True,
                    "local_to_canonical": compose_local_to_canonical(
                        world_to_canonical, floor["local_to_world"]
                    ),
                    "matcher": {
                        "max_features": 20000,
                        "ratio_threshold": 0.76,
                        "min_matches": 8,
                        "min_inliers": 8,
                    },
                    "metadata": {
                        "group_id": group["group_id"],
                        "floor_id": floor["floor_id"],
                        "label": floor["label"],
                        "name": floor["name"],
                    },
                }
            )

    manifest = {
        "region_id": str(surface_metadata.get("region_id", "fontaine")),
        "canonical_size": surface_metadata["atlas_size"],
        "default_map_layer_id": "surface",
        "levels": levels,
        "build": {
            "surface_metadata": _relative_path(
                surface_metadata_path, output_path.parent
            ),
            "underground_metadata": _relative_path(
                underground_metadata_path, output_path.parent
            ),
            "underground_levels": sum(
                level.get("map_layer_id", "surface") != "surface" for level in levels
            ),
            "skipped_underground_levels": skipped,
        },
    }
    output_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
