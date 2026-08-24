from __future__ import annotations

import argparse
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image


DEFAULT_REVISION = "eea752b746ae1f2e0c1988a574f2b7b0"
DEFAULT_ORIGIN = (24206, 8918)
URL_TEMPLATE = (
    "https://act-webstatic.hoyoverse.com/map_manage/map/2/"
    "{revision}/{x}_{y}_{zoom}.webp"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a local atlas from observed HoYoLAB map tiles")
    parser.add_argument("output", type=Path)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--zoom", default="N1", choices=("N2", "N1"))
    parser.add_argument("--x", default="32:43", help="Inclusive tile range, for example 32:43")
    parser.add_argument("--y", default="12:19", help="Inclusive tile range, for example 12:19")
    parser.add_argument("--origin-x", type=float, default=DEFAULT_ORIGIN[0])
    parser.add_argument("--origin-y", type=float, default=DEFAULT_ORIGIN[1])
    return parser


def _range(value: str) -> range:
    match = re.fullmatch(r"(-?\d+):(-?\d+)", value)
    if not match:
        raise ValueError(f"Invalid inclusive range: {value}")
    start, end = map(int, match.groups())
    if start > end:
        raise ValueError(f"Range start exceeds end: {value}")
    return range(start, end + 1)


def main() -> int:
    args = _parser().parse_args()
    xs, ys = _range(args.x), _range(args.y)
    tiles_dir = args.output / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)

    def fetch(position: tuple[int, int]) -> tuple[int, int, Path]:
        x, y = position
        output = tiles_dir / f"{x}_{y}_{args.zoom}.webp"
        if not output.exists():
            url = URL_TEMPLATE.format(revision=args.revision, x=x, y=y, zoom=args.zoom)
            request = urllib.request.Request(url, headers={"User-Agent": "GenshinNavigator/0.1"})
            with urllib.request.urlopen(request, timeout=30) as response:
                output.write_bytes(response.read())
        return x, y, output

    positions = [(x, y) for y in ys for x in xs]
    with ThreadPoolExecutor(max_workers=4) as executor:
        tiles = list(executor.map(fetch, positions))

    tile_size = 256
    atlas = Image.new("RGB", (len(xs) * tile_size, len(ys) * tile_size), (13, 22, 31))
    for x, y, path in tiles:
        atlas.paste(Image.open(path).convert("RGB"), ((x - xs.start) * tile_size, (y - ys.start) * tile_size))
    atlas.save(args.output / "atlas.png")

    metadata = {
        "source": "HoYoLAB Interactive Map",
        "region_id": "fontaine",
        "map_id": 2,
        "revision": args.revision,
        "zoom": args.zoom,
        "tile_size": tile_size,
        "tile_bounds": {
            "min_x": xs.start,
            "max_x": xs.stop - 1,
            "min_y": ys.start,
            "max_y": ys.stop - 1,
        },
        "atlas_size": list(atlas.size),
        "tile_count": len(tiles),
        "world_origin_zoom_0": [args.origin_x, args.origin_y],
        "world_to_atlas": [
            [
                1.0 / (2 if args.zoom == "N1" else 4),
                0.0,
                args.origin_x / (2 if args.zoom == "N1" else 4)
                - xs.start * tile_size,
            ],
            [
                0.0,
                1.0 / (2 if args.zoom == "N1" else 4),
                args.origin_y / (2 if args.zoom == "N1" else 4)
                - ys.start * tile_size,
            ],
            [0.0, 0.0, 1.0],
        ],
        "url_template": URL_TEMPLATE.format(
            revision=args.revision, x="{x}", y="{y}", zoom=args.zoom
        ),
    }
    (args.output / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    surface_pyramid = {
        "region_id": "fontaine",
        "canonical_size": list(atlas.size),
        "default_map_layer_id": "surface",
        "levels": [
            {
                "id": f"fontaine_surface_{args.zoom.lower()}",
                "image": "atlas.png",
                "resolution_scale": 1.0,
                "map_layer_id": "surface",
                "coordinate_space": "surface_atlas",
                "local_to_canonical": [
                    [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                    [0.0, 0.0, 1.0],
                ],
                "matcher": {"max_features": 80000},
            }
        ],
    }
    (args.output / "surface_pyramid.json").write_text(
        json.dumps(surface_pyramid, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
