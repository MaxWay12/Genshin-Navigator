from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


API_URL = (
    "https://sg-public-api-static.hoyolab.com/common/map_user/ys_obc/"
    "v2/map/point_group"
)

# HoYoLAB point groups belonging to Fontaine, including the Fortress of
# Meropide, the Great Lake overlays, Erinnyes and Remuria.
FONTAINE_GROUP_IDS = frozenset(
    {
        20,
        33,
        49,
        52,
        62,
        65,
        90,
        91,
        92,
        97,
        98,
        99,
        100,
        101,
        102,
        103,
        104,
        105,
        107,
        108,
        109,
        110,
        111,
        112,
        113,
        114,
        115,
        116,
        129,
        217,
        218,
    }
)


def fetch_point_groups(map_id: int = 2, lang: str = "ru-ru") -> list[dict[str, Any]]:
    query = urllib.parse.urlencode(
        {"map_id": map_id, "app_sn": "ys_obc", "lang": lang}
    )
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        headers={
            "User-Agent": "GenshinNavigator/0.1",
            "x-rpc-language": lang,
            "x-rpc-map_version": "4.5",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("retcode") != 0:
        raise RuntimeError(payload.get("message") or "HoYoLAB API request failed")
    return payload.get("data", {}).get("list", [])


def select_point_groups(
    groups: Iterable[dict[str, Any]],
    *,
    group_ids: set[int] | None = None,
    near: tuple[float, float] | None = None,
    radius: float | None = None,
    min_floors: int = 1,
) -> list[dict[str, Any]]:
    selected = []
    for group in groups:
        floors = [floor for floor in group.get("floors", []) if floor.get("overlay")]
        if len(floors) < min_floors:
            continue
        if group_ids is not None and int(group["id"]) not in group_ids:
            continue
        if near is not None:
            dx = float(group.get("underground_entrance_x_pos", 0)) - near[0]
            dy = float(group.get("underground_entrance_y_pos", 0)) - near[1]
            if radius is not None and math.hypot(dx, dy) > radius:
                continue
        selected.append(group)
    return selected


def _safe_name(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-zА-Яа-я_-]+", "_", value.strip())
    return value.strip("_") or "floor"


def _floor_label(floor: dict[str, Any], index: int) -> str:
    return floor.get("floor_name_short") or f"B{index + 1}"


def download_point_groups(
    output: Path,
    groups: Iterable[dict[str, Any]],
    *,
    map_id: int,
    lang: str,
    workers: int = 4,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[dict[str, Any], Path]] = []
    exported_groups = []

    for group in groups:
        group_id = int(group["id"])
        group_dir = output / f"group_{group_id:04d}"
        group_dir.mkdir(parents=True, exist_ok=True)
        exported_floors = []
        for index, floor in enumerate(group.get("floors", [])):
            overlay = floor.get("overlay") or {}
            if not overlay.get("url"):
                continue
            label = _floor_label(floor, index)
            suffix = Path(urllib.parse.urlparse(overlay["url"]).path).suffix or ".png"
            path = group_dir / (
                f"floor_{int(floor['id']):04d}_{_safe_name(label).lower()}{suffix}"
            )
            jobs.append((floor, path))
            exported_floors.append(
                {
                    "floor_id": int(floor["id"]),
                    "layer_id": f"underground:map{map_id}:group{group_id}:floor{floor['id']}",
                    "label": label,
                    "name": floor.get("floor_name", ""),
                    "path": path.relative_to(output).as_posix(),
                    "source_url": overlay["url"],
                    "world_bounds": {
                        "min_x": float(overlay["l_x"]),
                        "max_x": float(overlay["r_x"]),
                        "min_y": float(overlay["l_y"]),
                        "max_y": float(overlay["r_y"]),
                    },
                    "local_to_world": [
                        [
                            (float(overlay["r_x"]) - float(overlay["l_x"]))
                            / float(overlay["width"]),
                            0.0,
                            float(overlay["l_x"]),
                        ],
                        [
                            0.0,
                            (float(overlay["r_y"]) - float(overlay["l_y"]))
                            / float(overlay["height"]),
                            float(overlay["l_y"]),
                        ],
                        [0.0, 0.0, 1.0],
                    ],
                }
            )
        exported_groups.append(
            {
                "group_id": group_id,
                "surface_name": group.get("g_floor_name", ""),
                "entrance": {
                    "x": float(group.get("underground_entrance_x_pos", 0)),
                    "y": float(group.get("underground_entrance_y_pos", 0)),
                },
                "default_floor_index": int(group.get("default_floor_index", 0)),
                "floors": exported_floors,
            }
        )

    def fetch(job: tuple[dict[str, Any], Path]) -> tuple[Path, tuple[int, int]]:
        floor, path = job
        if not path.exists():
            request = urllib.request.Request(
                floor["overlay"]["url"],
                headers={"User-Agent": "GenshinNavigator/0.1"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                path.write_bytes(response.read())
        with Image.open(path) as image:
            return path, image.size

    with ThreadPoolExecutor(max_workers=workers) as executor:
        sizes = dict(executor.map(fetch, jobs))

    floor_by_path = {
        output / floor["path"]: floor
        for group in exported_groups
        for floor in group["floors"]
    }
    for path, size in sizes.items():
        floor = floor_by_path[path]
        bounds = floor["world_bounds"]
        floor["image_size"] = list(size)
        floor["local_to_world"] = [
            [
                (bounds["max_x"] - bounds["min_x"]) / size[0],
                0.0,
                bounds["min_x"],
            ],
            [
                0.0,
                (bounds["max_y"] - bounds["min_y"]) / size[1],
                bounds["min_y"],
            ],
            [0.0, 0.0, 1.0],
        ]
        floor["pixel_to_world"] = {
            "x": "min_x + pixel_x / image_width * (max_x - min_x)",
            "y": "min_y + pixel_y / image_height * (max_y - min_y)",
        }
        floor["world_units_per_pixel"] = [
            (bounds["max_x"] - bounds["min_x"]) / size[0],
            (bounds["max_y"] - bounds["min_y"]) / size[1],
        ]

    metadata = {
        "source": "HoYoLAB Interactive Map",
        "api": API_URL,
        "map_id": map_id,
        "lang": lang,
        "coordinate_system": "HoYoLAB map x/y",
        "image_axes": {"x": "right", "y": "down"},
        "group_count": len(exported_groups),
        "floor_count": sum(len(group["floors"]) for group in exported_groups),
        "groups": exported_groups,
    }
    (output / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metadata
