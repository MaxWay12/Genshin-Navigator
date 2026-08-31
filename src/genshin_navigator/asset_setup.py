from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .config import AppConfig
from .hoyolab_poi import (
    DEFAULT_LABEL_KINDS,
    build_catalog,
    build_space_metrics,
    content_version_for,
    fetch_labels,
    fetch_points,
    write_catalog,
)
from .hoyolab_underground import (
    FONTAINE_GROUP_IDS,
    download_point_groups,
    fetch_point_groups,
    select_point_groups,
)
from .underground_pyramid import build_underground_pyramid


ASSET_REVISION = "eea752b746ae1f2e0c1988a574f2b7b0"
WORLD_ORIGIN = (24206.0, 8918.0)
TILE_URL = (
    "https://act-webstatic.hoyoverse.com/map_manage/map/{map_id}/"
    "{revision}/{x}_{y}_{zoom}.webp"
)
ANCHOR_KINDS = {2: "statue", 3: "waypoint", 154: "domain"}


@dataclass(frozen=True)
class RegionPreset:
    region_id: str
    area_id: int
    tile_x: range
    tile_y: range
    surface_dir: str
    poi_file: str
    level_id: str
    underground: bool = False
    anchors: bool = False


PRESETS = {
    "fontaine": RegionPreset(
        "fontaine", 8, range(32, 44), range(12, 24),
        "hoyolab_fontaine_full_n1", "fontaine.json", "fontaine_surface_full_n1",
        underground=True,
    ),
    "sumeru_desert": RegionPreset(
        "sumeru_desert", 4, range(32, 44), range(22, 30),
        "hoyolab_sumeru_desert_n1", "sumeru-desert.json", "sumeru_desert_surface_n1",
        anchors=True,
    ),
}


def _request_bytes(url: str, *, timeout: float = 30.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "GenshinNavigator/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _world_to_atlas(min_x: int, min_y: int) -> list[list[float]]:
    return [
        [0.5, 0.0, WORLD_ORIGIN[0] / 2.0 - min_x * 256],
        [0.0, 0.5, WORLD_ORIGIN[1] / 2.0 - min_y * 256],
        [0.0, 0.0, 1.0],
    ]


def _valid_image(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except (OSError, ValueError):
        return False


def _download_surface(output: Path, preset: RegionPreset) -> dict[str, Any]:
    tiles = output / "tiles"
    tiles.mkdir(parents=True, exist_ok=True)
    # Staging lives directly below datasets/local. Keeping verified source tiles
    # beside it lets an interrupted first run resume without exposing partial atlases.
    cache = output.parents[2] / "downloads" / preset.region_id / ASSET_REVISION
    cache.mkdir(parents=True, exist_ok=True)
    positions = [(x, y) for y in preset.tile_y for x in preset.tile_x]

    def fetch(position: tuple[int, int]) -> tuple[int, int, Path | None]:
        x, y = position
        destination = tiles / f"{x}_{y}_N1.webp"
        cached = cache / destination.name
        if cached.is_file() and _valid_image(cached):
            shutil.copy2(cached, destination)
            return x, y, destination
        if cached.exists():
            cached.unlink()
        url = TILE_URL.format(
            map_id=2, revision=ASSET_REVISION, x=x, y=y, zoom="N1"
        )
        try:
            data = _request_bytes(url)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return x, y, None
            raise
        temporary = cached.with_suffix(".webp.tmp")
        temporary.write_bytes(data)
        if not _valid_image(temporary):
            temporary.unlink(missing_ok=True)
            raise ValueError(f"Downloaded map tile is not a valid image: {x},{y}")
        os.replace(temporary, cached)
        shutil.copy2(cached, destination)
        return x, y, destination

    with ThreadPoolExecutor(max_workers=4) as executor:
        downloaded = list(executor.map(fetch, positions))
    width, height = len(preset.tile_x) * 256, len(preset.tile_y) * 256
    atlas = Image.new("RGB", (width, height), (13, 22, 31))
    for x, y, tile in downloaded:
        if tile is not None:
            with Image.open(tile) as image:
                atlas.paste(
                    image.convert("RGB"),
                    ((x - preset.tile_x.start) * 256, (y - preset.tile_y.start) * 256),
                )
    atlas.save(output / "atlas.png")
    metadata = {
        "source": "HoYoLAB Interactive Map",
        "source_acquisition": "downloaded locally by the user",
        "region_id": preset.region_id,
        "map_id": 2,
        "revision": ASSET_REVISION,
        "zoom": "N1",
        "tile_size": 256,
        "tile_bounds": {
            "min_x": preset.tile_x.start,
            "max_x": preset.tile_x.stop - 1,
            "min_y": preset.tile_y.start,
            "max_y": preset.tile_y.stop - 1,
        },
        "atlas_size": [width, height],
        "tile_count": sum(tile is not None for _, _, tile in downloaded),
        "requested_tile_count": len(downloaded),
        "missing_tile_count": sum(tile is None for _, _, tile in downloaded),
        "world_origin_zoom_0": list(WORLD_ORIGIN),
        "world_to_atlas": _world_to_atlas(preset.tile_x.start, preset.tile_y.start),
        "url_template": TILE_URL.format(
            map_id=2, revision=ASSET_REVISION, x="{x}", y="{y}", zoom="N1"
        ),
    }
    (output / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pyramid = {
        "region_id": preset.region_id,
        "canonical_size": [width, height],
        "default_map_layer_id": "surface",
        "levels": [{
            "id": preset.level_id,
            "image": "atlas.png",
            "resolution_scale": 1.0,
            "map_layer_id": "surface",
            "coordinate_space": "surface_atlas",
            "local_to_canonical": np.eye(3).tolist(),
            "matcher": {"max_features": 80000},
        }],
    }
    (output / "surface_pyramid.json").write_text(
        json.dumps(pyramid, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metadata


def _project(matrix: np.ndarray, x: float, y: float) -> tuple[float, float]:
    target = matrix @ np.asarray([x, y, 1.0], dtype=np.float64)
    return float(target[0] / target[2]), float(target[1] / target[2])


def _write_anchors(
    output: Path,
    icons_dir: Path,
    metadata: dict[str, Any],
    labels: list[dict[str, Any]],
    points: list[dict[str, Any]],
    preset: RegionPreset,
) -> None:
    matrix = np.asarray(metadata["world_to_atlas"], dtype=np.float64)
    width, height = map(int, metadata["atlas_size"])
    label_by_id = {int(item["id"]): item for item in labels}
    anchors = []
    for point in points:
        label_id = int(point.get("label_id", 0))
        if label_id not in ANCHOR_KINDS or int(point.get("area_id", 0)) != preset.area_id:
            continue
        x, y = _project(matrix, float(point["x_pos"]), float(point["y_pos"]))
        if 0 <= x < width and 0 <= y < height:
            anchors.append({
                "id": f"hoyolab:{point['id']}",
                "point_id": int(point["id"]),
                "kind": ANCHOR_KINDS[label_id],
                "label_id": label_id,
                "x": round(x, 4), "y": round(y, 4),
                "layer_id": "surface" if not point.get("point_group") else "underground",
                "point_group": point.get("point_group"),
            })
    icons_dir.mkdir(parents=True, exist_ok=True)
    icon_paths = {}
    for label_id, kind in ANCHOR_KINDS.items():
        destination = icons_dir / f"{kind}.png"
        data = _request_bytes(str(label_by_id[label_id]["icon"]))
        temporary = destination.with_suffix(".png.tmp")
        temporary.write_bytes(data)
        os.replace(temporary, destination)
        icon_paths[kind] = f"icons/{destination.name}"
    payload = {
        "format_version": 1,
        "source": "HoYoLAB Interactive Map",
        "source_acquisition": "downloaded locally by the user",
        "source_url": "https://act.hoyolab.com/ys/app/interactive-map/index.html#/map/2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "region_id": preset.region_id,
        "map_id": 2,
        "area_id": preset.area_id,
        "canonical_size": [width, height],
        "icon_paths": icon_paths,
        "anchors": sorted(anchors, key=lambda item: str(item["id"])),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _promote_assets(items: list[tuple[Path, Path]]) -> None:
    """Promote a complete staged region and roll every item back on failure."""
    promoted: list[tuple[Path, Path, Path, bool]] = []
    try:
        for staged, destination in items:
            destination.parent.mkdir(parents=True, exist_ok=True)
            previous = destination.with_name(f".{destination.name}.previous")
            if previous.exists():
                if previous.is_dir():
                    shutil.rmtree(previous)
                else:
                    previous.unlink()
            had_previous = destination.exists()
            if had_previous:
                os.replace(destination, previous)
            try:
                os.replace(staged, destination)
            except Exception:
                if had_previous and previous.exists() and not destination.exists():
                    os.replace(previous, destination)
                raise
            promoted.append((staged, destination, previous, had_previous))
    except Exception:
        for _, destination, previous, had_previous in reversed(promoted):
            if destination.exists():
                if destination.is_dir():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            if had_previous and previous.exists():
                os.replace(previous, destination)
        raise
    for _, _, previous, _ in promoted:
        if previous.exists():
            if previous.is_dir():
                shutil.rmtree(previous)
            else:
                previous.unlink()


def region_asset_status(config: AppConfig, region_id: str) -> dict[str, Any]:
    if region_id not in PRESETS:
        raise ValueError(f"No first-run asset preset for region {region_id!r}")
    preset = PRESETS[region_id]
    reference_root = config.data.surface_metadata_path.parent.parent
    poi_root = config.poi.catalog_path.parent
    final_surface = reference_root / preset.surface_dir
    final_underground = reference_root / "hoyolab_fontaine_underground"
    final_anchors = reference_root / "sumeru_semantic_anchors"
    final_poi = poi_root / preset.poi_file
    required = [final_surface / "atlas.png", final_surface / (
        "pyramid.json" if preset.underground else "surface_pyramid.json"
    ), final_poi]
    if preset.anchors:
        required.append(final_anchors / "anchors.json")
    missing = []
    for path in required:
        if path.exists():
            continue
        try:
            missing.append(str(path.relative_to(Path.cwd())))
        except ValueError:
            missing.append(path.name)
    return {
        "region_id": region_id,
        "ready": not missing,
        "required_asset_count": len(required),
        "missing_asset_count": len(missing),
        "missing_assets": missing,
    }


def setup_region(
    config: AppConfig,
    region_id: str,
    *,
    force: bool = False,
    progress=None,
) -> dict[str, Any]:
    if region_id not in PRESETS:
        raise ValueError(f"No first-run asset preset for region {region_id!r}")
    preset = PRESETS[region_id]
    reference_root = config.data.surface_metadata_path.parent.parent
    poi_root = config.poi.catalog_path.parent
    final_surface = reference_root / preset.surface_dir
    final_underground = reference_root / "hoyolab_fontaine_underground"
    final_anchors = reference_root / "sumeru_semantic_anchors"
    final_poi = poi_root / preset.poi_file
    required = [final_surface / "atlas.png", final_surface / (
        "pyramid.json" if preset.underground else "surface_pyramid.json"
    ), final_poi]
    if preset.anchors:
        required.append(final_anchors / "anchors.json")
    if not force and all(path.exists() for path in required):
        return {"region_id": region_id, "status": "already_ready"}

    notify = progress or (lambda _stage: None)

    staging_parent = reference_root.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".setup-{region_id}-", dir=staging_parent))
    try:
        staged_refs = staging / "references"
        staged_surface = staged_refs / preset.surface_dir
        staged_surface.mkdir(parents=True)
        notify("Downloading surface map tiles")
        metadata = _download_surface(staged_surface, preset)
        notify("Downloading official POI metadata")
        labels = fetch_labels(map_id=2, lang=config.data.lang)
        points = fetch_points(map_id=2, lang=config.data.lang)
        underground_metadata = None
        if preset.underground:
            notify("Downloading underground floors")
            groups = select_point_groups(
                fetch_point_groups(2, config.data.lang),
                group_ids=set(FONTAINE_GROUP_IDS),
                min_floors=1,
            )
            staged_underground = staged_refs / "hoyolab_fontaine_underground"
            underground_metadata = download_point_groups(
                staged_underground, groups, map_id=2, lang=config.data.lang, workers=4
            )
            build_underground_pyramid(
                staged_surface / "metadata.json",
                staged_surface / "surface_pyramid.json",
                staged_underground / "metadata.json",
                staged_surface / "pyramid.json",
            )
        if preset.anchors:
            notify("Building semantic anchor catalog")
            staged_anchors = staged_refs / "sumeru_semantic_anchors"
            _write_anchors(
                staged_anchors / "anchors.json", staged_anchors / "icons",
                metadata, labels, points, preset,
            )
        pois, stats = build_catalog(
            points, labels, metadata, underground_metadata,
            area_id=preset.area_id, label_kinds=DEFAULT_LABEL_KINDS,
        )
        spaces = build_space_metrics(metadata, underground_metadata)
        staged_poi = staging / "poi" / preset.poi_file
        write_catalog(
            staged_poi, pois,
            map_version=content_version_for(
                labels, points, asset_revision=ASSET_REVISION
            ),
            stats=stats, spaces=spaces,
        )

        notify("Validating and installing the completed region")
        promotion = [(staged_surface, final_surface)]
        if preset.underground:
            promotion.append(
                (staged_refs / "hoyolab_fontaine_underground", final_underground)
            )
        if preset.anchors:
            promotion.append((staged_refs / "sumeru_semantic_anchors", final_anchors))
        promotion.append((staged_poi, final_poi))
        _promote_assets(promotion)
        return {
            "region_id": region_id,
            "status": "installed",
            "poi_count": len(pois),
            "surface_tiles": metadata["tile_count"],
            "underground_floors": (
                underground_metadata["floor_count"] if underground_metadata else 0
            ),
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)
