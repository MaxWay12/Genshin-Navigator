from __future__ import annotations

import json
import hashlib
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .poi import MapSpaceMetric, PointOfInterest
from .position import CoordinateSpace


API_ROOT = "https://sg-public-api-static.hoyolab.com/common/map_user/ys_obc"
LABEL_TREE_URL = f"{API_ROOT}/v1/map/label/tree"
POINT_LIST_URL = f"{API_ROOT}/v3/map/point/list"

DEFAULT_LABEL_KINDS = {
    2: "waypoint",
    3: "waypoint",
    17: "chest",
    44: "chest",
    45: "chest",
    46: "chest",
    269: "chest",
    508: "hydroculus",
}


def _fetch(endpoint: str, *, map_id: int, lang: str, map_version: str | None) -> dict[str, Any]:
    query = urllib.parse.urlencode({"map_id": map_id, "app_sn": "ys_obc", "lang": lang})
    headers = {
        "User-Agent": "GenshinNavigator/0.1",
        "x-rpc-language": lang,
    }
    if map_version:
        headers["x-rpc-map_version"] = map_version
    request = urllib.request.Request(
        f"{endpoint}?{query}",
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.load(response)
    if payload.get("retcode") != 0:
        raise RuntimeError(payload.get("message") or "HoYoLAB API request failed")
    return payload.get("data", {})


def fetch_labels(map_id: int = 2, lang: str = "ru-ru", map_version: str | None = None) -> list[dict[str, Any]]:
    data = _fetch(LABEL_TREE_URL, map_id=map_id, lang=lang, map_version=map_version)
    return [child for parent in data.get("tree", []) for child in parent.get("children", [])]


def fetch_points(map_id: int = 2, lang: str = "ru-ru", map_version: str | None = None) -> list[dict[str, Any]]:
    data = _fetch(POINT_LIST_URL, map_id=map_id, lang=lang, map_version=map_version)
    return data.get("point_list", [])


def content_version_for(
    labels: Iterable[dict[str, Any]],
    points: Iterable[dict[str, Any]],
    *,
    explicit_version: str | None = None,
    asset_revision: str | None = None,
) -> str:
    """Stable version of the actual normalized upstream response."""
    if explicit_version:
        return explicit_version
    payload = json.dumps(
        {
            "asset_revision": asset_revision,
            "labels": sorted(labels, key=lambda item: int(item.get("id", 0))),
            "points": sorted(points, key=lambda item: int(item.get("id", 0))),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()[:20]


def _project(matrix: np.ndarray, x: float, y: float) -> tuple[float, float]:
    target = matrix @ np.float64([x, y, 1.0])
    if abs(target[2]) < 1e-12:
        raise ValueError("POI transform projects a point to infinity")
    return float(target[0] / target[2]), float(target[1] / target[2])


def build_catalog(
    points: Iterable[dict[str, Any]],
    labels: Iterable[dict[str, Any]],
    surface_metadata: dict[str, Any],
    underground_metadata: dict[str, Any],
    *,
    area_id: int = 8,
    label_kinds: dict[int, str] | None = None,
) -> tuple[list[PointOfInterest], dict[str, int]]:
    selected = label_kinds or DEFAULT_LABEL_KINDS
    label_by_id = {int(item["id"]): item for item in labels}
    world_to_atlas = np.asarray(surface_metadata["world_to_atlas"], dtype=np.float64)
    if world_to_atlas[1, 1] <= 0:
        raise ValueError(
            "surface metadata uses the obsolete vertically flipped HoYoLAB transform"
        )
    atlas_width, atlas_height = map(int, surface_metadata["atlas_size"])
    floors: dict[tuple[int, int], tuple[str, np.ndarray, tuple[int, int]]] = {}
    for group in underground_metadata.get("groups", []):
        group_id = int(group["group_id"])
        for floor in group.get("floors", []):
            floors[(group_id, int(floor["floor_id"]))] = (
                str(floor["layer_id"]),
                np.linalg.inv(np.asarray(floor["local_to_world"], dtype=np.float64)),
                tuple(map(int, floor["image_size"])),
            )

    result: list[PointOfInterest] = []
    stats = {"selected": 0, "surface": 0, "underground": 0, "skipped_unknown_floor": 0, "skipped_outside_reference": 0}
    for point in points:
        label_id = int(point.get("label_id", 0))
        if int(point.get("area_id", 0)) != area_id or label_id not in selected:
            continue
        stats["selected"] += 1
        world_x, world_y = float(point["x_pos"]), float(point["y_pos"])
        point_group = point.get("point_group")
        if point_group:
            floor_key = (int(point_group["group_id"]), int(point_group["floor_id"]))
            floor = floors.get(floor_key)
            if floor is None:
                stats["skipped_unknown_floor"] += 1
                continue
            layer_id, world_to_local, image_size = floor
            x, y = _project(world_to_local, world_x, world_y)
            coordinate_space = CoordinateSpace.LAYER_LOCAL
            width, height = image_size
            target_stat = "underground"
        else:
            layer_id = "surface"
            x, y = _project(world_to_atlas, world_x, world_y)
            coordinate_space = CoordinateSpace.SURFACE_ATLAS
            width, height = atlas_width, atlas_height
            target_stat = "surface"
        if not (0 <= x < width and 0 <= y < height):
            stats["skipped_outside_reference"] += 1
            continue
        stats[target_stat] += 1
        label = label_by_id.get(label_id, {})
        result.append(
            PointOfInterest(
                id=f"hoyolab:{int(point['id'])}",
                kind=selected[label_id],
                name=str(label.get("name") or f"HoYoLAB label {label_id}"),
                region_id=str(surface_metadata.get("region_id", "fontaine")),
                layer_id=layer_id,
                coordinate_space=coordinate_space,
                x=x,
                y=y,
                label_id=label_id,
                icon_url=str(label.get("icon")) if label.get("icon") else None,
            )
        )
    return result, stats


def build_space_metrics(
    surface_metadata: dict[str, Any],
    underground_metadata: dict[str, Any],
) -> list[MapSpaceMetric]:
    region_id = str(surface_metadata.get("region_id", "fontaine"))
    atlas_to_world = np.linalg.inv(
        np.asarray(surface_metadata["world_to_atlas"], dtype=np.float64)
    )
    metrics = [
        MapSpaceMetric(
            region_id=region_id,
            layer_id="surface",
            coordinate_space=CoordinateSpace.SURFACE_ATLAS,
            local_to_world=(
                (float(atlas_to_world[0, 0]), float(atlas_to_world[0, 1])),
                (float(atlas_to_world[1, 0]), float(atlas_to_world[1, 1])),
            ),
        )
    ]
    for group in underground_metadata.get("groups", []):
        for floor in group.get("floors", []):
            matrix = np.asarray(floor["local_to_world"], dtype=np.float64)
            metrics.append(
                MapSpaceMetric(
                    region_id=region_id,
                    layer_id=str(floor["layer_id"]),
                    coordinate_space=CoordinateSpace.LAYER_LOCAL,
                    local_to_world=(
                        (float(matrix[0, 0]), float(matrix[0, 1])),
                        (float(matrix[1, 0]), float(matrix[1, 1])),
                    ),
                )
            )
    return metrics


def write_catalog(
    output: str | Path,
    pois: Iterable[PointOfInterest],
    *,
    map_version: str,
    stats: dict[str, int],
    spaces: Iterable[MapSpaceMetric] = (),
) -> Path:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    items = sorted(pois, key=lambda poi: (poi.layer_id, poi.kind, poi.id))
    payload = {
        "format_version": 1,
        "source": "HoYoLAB Interactive Map",
        "source_url": "https://act.hoyolab.com/ys/app/interactive-map/index.html#/map/2",
        "map_version": map_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "spaces": [metric.to_dict() for metric in spaces],
        "pois": [poi.to_dict() for poi in items],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
