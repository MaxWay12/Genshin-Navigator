from __future__ import annotations

import argparse
from pathlib import Path

import cv2
from genshin_navigator.image_io import write_image

from genshin_navigator.capture import load_image
from genshin_navigator.poi import PoiCatalog
from genshin_navigator.position import CoordinateSpace, MapPosition, PositionState


def main() -> int:
    parser = argparse.ArgumentParser(description="Render POI positions over a reference map")
    parser.add_argument("atlas", type=Path)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--layer", default="surface")
    args = parser.parse_args()

    image = load_image(args.atlas).copy()
    catalog = PoiCatalog.load(args.catalog)
    position = MapPosition(
        region_id="fontaine",
        layer_id=args.layer,
        coordinate_space=(
            CoordinateSpace.SURFACE_ATLAS
            if args.layer == "surface"
            else CoordinateSpace.LAYER_LOCAL
        ),
        x=0,
        y=0,
        confidence=1,
        state=PositionState.TRACKING,
        timestamp=0,
    )
    colors = {
        "chest": (40, 180, 255),
        "hydroculus": (255, 210, 60),
        "waypoint": (255, 120, 60),
    }
    for poi in catalog.on_layer(position):
        cv2.circle(
            image,
            (round(poi.x), round(poi.y)),
            5,
            colors.get(poi.kind, (255, 255, 255)),
            thickness=-1,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not write_image(str(args.output), image):
        raise OSError(f"Could not write {args.output}")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
