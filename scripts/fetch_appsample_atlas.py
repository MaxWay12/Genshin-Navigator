from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image


DEFAULT_REVISION = "v70-rc1"
URL_TEMPLATE = (
    "https://game-cdn.appsample.com/gim/map-teyvat/"
    "{revision}/{level}/tile-{x}_{y}.jpg"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a local atlas from observed Appsample map tiles"
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--level", type=int, default=15)
    parser.add_argument("--x", default="-4:19", help="Inclusive tile range")
    parser.add_argument("--y", default="20:35", help="Inclusive tile range")
    parser.add_argument("--workers", type=int, default=8)
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

    def fetch(position: tuple[int, int]) -> tuple[int, int, Path | None]:
        x, y = position
        output = tiles_dir / f"{x}_{y}_L{args.level}.jpg"
        if output.exists():
            return x, y, output
        url = URL_TEMPLATE.format(
            revision=args.revision, level=args.level, x=x, y=y
        )
        request = urllib.request.Request(
            url, headers={"User-Agent": "GenshinNavigator/0.1"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                output.write_bytes(response.read())
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return x, y, None
            raise
        return x, y, output

    positions = [(x, y) for y in ys for x in xs]
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        tiles = list(executor.map(fetch, positions))

    tile_size = 256
    atlas = Image.new(
        "RGB", (len(xs) * tile_size, len(ys) * tile_size), (13, 22, 31)
    )
    present = 0
    for x, y, path in tiles:
        if path is None:
            continue
        with Image.open(path) as tile:
            if tile.size != (tile_size, tile_size):
                raise ValueError(f"Unexpected tile size {tile.size}: {path}")
            atlas.paste(
                tile.convert("RGB"),
                (
                    (x - xs.start) * tile_size,
                    ((ys.stop - 1) - y) * tile_size,
                ),
            )
        present += 1
    atlas.save(args.output / "atlas.png")

    metadata = {
        "source": "Genshin Impact Map by Appsample",
        "revision": args.revision,
        "level": args.level,
        "tile_size": tile_size,
        "tile_y_direction": "up",
        "tile_bounds": {
            "min_x": xs.start,
            "max_x": xs.stop - 1,
            "min_y": ys.start,
            "max_y": ys.stop - 1,
        },
        "atlas_size": list(atlas.size),
        "requested_tile_count": len(tiles),
        "present_tile_count": present,
        "url_template": URL_TEMPLATE.format(
            revision=args.revision, level=args.level, x="{x}", y="{y}"
        ),
    }
    (args.output / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
