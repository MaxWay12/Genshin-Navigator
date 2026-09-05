"""Overlapping surface references in a single canonical atlas space."""
from pathlib import Path
import json

import cv2
import numpy as np

from PIL import Image
from .matcher import MinimapMatcher


def build_surface_sections(atlas: Image.Image, output: Path, region_id: str,
                           size: int = 1024, overlap: int = 256) -> list[dict]:
    if size <= overlap or overlap < 0:
        raise ValueError("Invalid surface section overlap")
    levels = []
    skipped = []
    detector = cv2.SIFT_create(nfeatures=20000, contrastThreshold=0.01, edgeThreshold=15, sigma=1.6)
    for y in range(0, atlas.height, size - overlap):
        for x in range(0, atlas.width, size - overlap):
            image = atlas.crop((x, y, min(x + size, atlas.width), min(y + size, atlas.height)))
            pixels = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
            features = detector.detect(MinimapMatcher._prepare(pixels), None)
            if len(features) < 12:
                skipped.append({"x": x, "y": y, "features": len(features), "reason": "not_enough_features"})
                continue
            filename = f"section_{x}_{y}.png"
            image.save(output / filename)
            levels.append({"id": f"{region_id}_surface_{x}_{y}", "image": filename,
                           "resolution_scale": 1.0, "map_layer_id": "surface",
                           "coordinate_space": "surface_atlas",
                           "local_to_canonical": [[1, 0, x], [0, 1, y], [0, 0, 1]],
                           "matcher": {"max_features": 20000}})
    (output / "section_report.json").write_text(json.dumps({"skipped": skipped}, indent=2), encoding="utf-8")
    if not levels:
        raise ValueError("Surface has no usable reference sections")
    return levels
