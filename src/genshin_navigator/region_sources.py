"""Public map metadata and evidence-based regional membership (no auth)."""
from __future__ import annotations

import math
import re
from typing import Any

from .hoyolab_poi import API_ROOT, _fetch

MAP_LIST_URL = f"{API_ROOT}/v1/map/list"


def fetch_surface_metadata(map_id: int = 2, lang: str = "ru-ru", revision: str | None = None) -> dict[str, Any]:
    data = _fetch(MAP_LIST_URL, map_id=map_id, lang=lang, map_version=None)
    matches = [item for item in data.get("all_map_list", []) if int(item["id"]) == map_id]
    if len(matches) != 1:
        raise ValueError("Official map metadata is missing or ambiguous")
    detail = matches[0]["detail_v2"]
    version = revision or detail.get("map_version", "")
    origin = detail.get("origin", [])
    if not re.fullmatch(r"[a-fA-F0-9]{32}", version) or len(origin) != 2:
        raise ValueError("Invalid official map revision/origin")
    if not all(math.isfinite(float(value)) for value in origin):
        raise ValueError("Non-finite official map origin")
    return {"revision": version, "origin": list(map(float, origin)),
            "source_url": MAP_LIST_URL, "upstream_revision": detail["map_version"]}


def regional_groups(groups: list[dict], points: list[dict], area_id: int) -> tuple[list[dict], list[dict]]:
    """Resolve groups through official point/group links, never geographic guesses."""
    by_id = {int(p["id"]): p for p in points}
    evidence: dict[int, set[int]] = {}
    for point in points:
        link = point.get("point_group")
        if link:
            evidence.setdefault(int(link["group_id"]), set()).add(int(point["id"]))
    selected, audit = [], []
    for group in groups:
        ids = set(evidence.get(int(group["id"]), set()))
        for floor in group.get("floors", []):
            ids.update(map(int, floor.get("point_ids", [])))
            ids.update(map(int, floor.get("entrance_ids", [])))
        known = [by_id[i] for i in ids if i in by_id]
        areas = {int(p.get("area_id", 0)) for p in known} - {0}
        if area_id not in areas:
            continue
        if areas != {area_id}:
            raise ValueError(f"Ambiguous regional membership for group {group['id']}: {sorted(areas)}")
        selected.append(group)
        audit.append({"group_id": int(group["id"]), "area_id": area_id,
                      "unavailable_floor_ids": [int(f["id"]) for f in group.get("floors", [])
                                                if not f.get("overlay", {}).get("url")],
                      "evidence_point_ids": sorted(int(p["id"]) for p in known)})
    required = {int(p["point_group"]["group_id"]) for p in points
                if int(p.get("area_id", 0)) == area_id and p.get("point_group")}
    missing = required - {int(g["id"]) for g in selected}
    if missing or not selected:
        raise ValueError(f"Incomplete regional underground metadata: {sorted(missing)}")
    return selected, audit


def surface_bounds(points: list[dict], area_id: int, origin: list[float]) -> tuple[range, range]:
    coords = [(float(p["x_pos"]), float(p["y_pos"])) for p in points
              if int(p.get("area_id", 0)) == area_id and not p.get("point_group")]
    if not coords or not all(math.isfinite(v) for pair in coords for v in pair):
        raise ValueError("Missing/invalid regional surface points")
    # One tile of padding around the official regional point footprint.
    bounds = []
    for axis in (0, 1):
        tiles = [(pair[axis] + origin[axis]) / 512 for pair in coords]
        bounds.append(range(math.floor(min(tiles)) - 1, math.floor(max(tiles)) + 2))
    if len(bounds[0]) * len(bounds[1]) > 1024:
        raise ValueError("Regional tile bounds unexpectedly large")
    return bounds[0], bounds[1]
