from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from genshin_navigator.hoyolab_poi import fetch_labels, fetch_points


KINDS = {
    2: "statue",
    3: "waypoint",
    154: "domain",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch official teleport/domain anchors for a canonical atlas"
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--area-id", type=int, required=True)
    parser.add_argument("--map-id", type=int, default=2)
    parser.add_argument("--lang", default="en-us")
    parser.add_argument("--icons-dir", type=Path)
    return parser


def _project(matrix: np.ndarray, x: float, y: float) -> tuple[float, float]:
    target = matrix @ np.asarray([x, y, 1.0], dtype=np.float64)
    return float(target[0] / target[2]), float(target[1] / target[2])


def main() -> int:
    args = _parser().parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    matrix = np.asarray(metadata["world_to_atlas"], dtype=np.float64)
    width, height = map(int, metadata["atlas_size"])
    labels = fetch_labels(args.map_id, args.lang)
    label_by_id = {int(item["id"]): item for item in labels}
    anchors: list[dict[str, object]] = []
    for point in fetch_points(args.map_id, args.lang):
        label_id = int(point.get("label_id", 0))
        if label_id not in KINDS or int(point.get("area_id", 0)) != args.area_id:
            continue
        x, y = _project(matrix, float(point["x_pos"]), float(point["y_pos"]))
        if not (0 <= x < width and 0 <= y < height):
            continue
        group = point.get("point_group")
        anchors.append(
            {
                "id": f"hoyolab:{point['id']}",
                "point_id": int(point["id"]),
                "kind": KINDS[label_id],
                "label_id": label_id,
                "x": round(x, 4),
                "y": round(y, 4),
                "layer_id": "surface" if not group else "underground",
                "point_group": group,
            }
        )

    icon_paths: dict[str, str] = {}
    if args.icons_dir is not None:
        args.icons_dir.mkdir(parents=True, exist_ok=True)
        for label_id, kind in KINDS.items():
            url = str(label_by_id[label_id]["icon"])
            destination = args.icons_dir / f"{kind}.png"
            request = urllib.request.Request(
                url, headers={"User-Agent": "GenshinNavigator/0.1"}
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, destination)
            icon_paths[kind] = os.path.relpath(destination, args.output.parent).replace("\\", "/")

    payload = {
        "format_version": 1,
        "source": "HoYoLAB Interactive Map",
        "source_url": "https://act.hoyolab.com/ys/app/interactive-map/index.html#/map/2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "region_id": str(metadata["region_id"]),
        "map_id": args.map_id,
        "area_id": args.area_id,
        "canonical_size": [width, height],
        "icon_paths": icon_paths,
        "anchors": sorted(anchors, key=lambda item: str(item["id"])),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, args.output)
    print(json.dumps({"anchors": len(anchors), "icons": icon_paths}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
