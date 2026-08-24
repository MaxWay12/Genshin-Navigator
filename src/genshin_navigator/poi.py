from __future__ import annotations

import json
import os
from dataclasses import dataclass
from math import hypot, isfinite
from pathlib import Path
from typing import Iterable

from .position import CoordinateSpace, MapPosition


@dataclass(frozen=True)
class PointOfInterest:
    id: str
    kind: str
    name: str
    region_id: str
    layer_id: str
    coordinate_space: CoordinateSpace
    x: float
    y: float
    label_id: int | None = None
    icon_url: str | None = None

    def same_space(self, position: MapPosition) -> bool:
        return bool(
            self.region_id == position.region_id
            and self.layer_id == position.layer_id
            and self.coordinate_space is position.coordinate_space
        )

    def distance_to(self, position: MapPosition) -> float:
        if not self.same_space(position):
            raise ValueError("Cannot measure distance across different map layers")
        return hypot(self.x - position.x, self.y - position.y)

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> PointOfInterest:
        return cls(
            id=str(raw["id"]),
            kind=str(raw["kind"]),
            name=str(raw["name"]),
            region_id=str(raw["region_id"]),
            layer_id=str(raw["layer_id"]),
            coordinate_space=CoordinateSpace(str(raw["coordinate_space"])),
            x=float(raw["x"]),
            y=float(raw["y"]),
            label_id=int(raw["label_id"]) if raw.get("label_id") is not None else None,
            icon_url=str(raw["icon_url"]) if raw.get("icon_url") else None,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "region_id": self.region_id,
            "layer_id": self.layer_id,
            "coordinate_space": self.coordinate_space.value,
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "label_id": self.label_id,
            "icon_url": self.icon_url,
        }


@dataclass(frozen=True)
class MapSpaceMetric:
    """Linear conversion from a coordinate space delta to HoYoLAB world units."""

    region_id: str
    layer_id: str
    coordinate_space: CoordinateSpace
    local_to_world: tuple[tuple[float, float], tuple[float, float]]

    def __post_init__(self) -> None:
        (a, b), (c, d) = self.local_to_world
        if not all(isfinite(value) for value in (a, b, c, d)):
            raise ValueError("Map-space metric must contain finite values")
        if abs(a * d - b * c) < 1e-12:
            raise ValueError("Map-space metric must be invertible")

    @property
    def key(self) -> tuple[str, str, CoordinateSpace]:
        return self.region_id, self.layer_id, self.coordinate_space

    def world_delta(self, dx: float, dy: float) -> tuple[float, float]:
        (a, b), (c, d) = self.local_to_world
        return a * dx + b * dy, c * dx + d * dy

    def world_distance(self, dx: float, dy: float) -> float:
        world_dx, world_dy = self.world_delta(dx, dy)
        return hypot(world_dx, world_dy)

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> MapSpaceMetric:
        matrix = raw["local_to_world"]
        if not isinstance(matrix, list) or len(matrix) != 2:
            raise ValueError("local_to_world must be a 2x2 matrix")
        rows = tuple(tuple(float(value) for value in row) for row in matrix)
        if any(len(row) != 2 for row in rows):
            raise ValueError("local_to_world must be a 2x2 matrix")
        return cls(
            region_id=str(raw["region_id"]),
            layer_id=str(raw["layer_id"]),
            coordinate_space=CoordinateSpace(str(raw["coordinate_space"])),
            local_to_world=rows,  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "region_id": self.region_id,
            "layer_id": self.layer_id,
            "coordinate_space": self.coordinate_space.value,
            "local_to_world": [list(row) for row in self.local_to_world],
        }


class PoiCatalog:
    def __init__(
        self,
        pois: Iterable[PointOfInterest],
        metrics: Iterable[MapSpaceMetric] = (),
    ):
        self.pois = tuple(pois)
        self.metrics = tuple(metrics)
        self._by_space: dict[tuple[str, str, CoordinateSpace], list[PointOfInterest]] = {}
        for poi in self.pois:
            key = (poi.region_id, poi.layer_id, poi.coordinate_space)
            self._by_space.setdefault(key, []).append(poi)
        self._metrics_by_space = {metric.key: metric for metric in self.metrics}

    @classmethod
    def load(cls, path: str | Path) -> PoiCatalog:
        with Path(path).open("r", encoding="utf-8") as stream:
            raw = json.load(stream)
        if int(raw.get("format_version", 0)) != 1:
            raise ValueError("Unsupported POI catalog format")
        return cls(
            (PointOfInterest.from_dict(item) for item in raw["pois"]),
            (MapSpaceMetric.from_dict(item) for item in raw.get("spaces", [])),
        )

    def on_layer(self, position: MapPosition) -> tuple[PointOfInterest, ...]:
        key = (position.region_id, position.layer_id, position.coordinate_space)
        return tuple(self._by_space.get(key, ()))

    def nearest(
        self,
        position: MapPosition,
        *,
        kinds: set[str] | None = None,
        exclude_ids: set[str] | None = None,
        limit: int = 1,
    ) -> list[tuple[PointOfInterest, float]]:
        if limit < 1:
            raise ValueError("Nearest POI limit must be positive")
        candidates = (
            poi
            for poi in self.on_layer(position)
            if kinds is None or poi.kind in kinds
            if exclude_ids is None or poi.id not in exclude_ids
        )
        ranked = sorted(
            ((poi, poi.distance_to(position)) for poi in candidates),
            key=lambda item: (item[1], item[0].id),
        )
        return ranked[:limit]

    def world_distance(self, position: MapPosition, poi: PointOfInterest) -> float | None:
        if not poi.same_space(position):
            raise ValueError("Cannot measure distance across different map layers")
        key = (position.region_id, position.layer_id, position.coordinate_space)
        metric = self._metrics_by_space.get(key)
        if metric is None:
            return None
        return metric.world_distance(poi.x - position.x, poi.y - position.y)


class PoiProgress:
    def __init__(self, path: str | Path, collected_ids: Iterable[str] = ()):
        self.path = Path(path)
        self.collected_ids = set(collected_ids)

    @classmethod
    def load(cls, path: str | Path) -> PoiProgress:
        progress_path = Path(path)
        if not progress_path.exists():
            return cls(progress_path)
        with progress_path.open("r", encoding="utf-8") as stream:
            raw = json.load(stream)
        if int(raw.get("format_version", 0)) != 1:
            raise ValueError("Unsupported POI progress format")
        return cls(progress_path, map(str, raw.get("collected_ids", [])))

    def mark_collected(self, poi_id: str) -> None:
        self.collected_ids.add(poi_id)
        self._save()

    def unmark_collected(self, poi_id: str) -> None:
        self.collected_ids.discard(poi_id)
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "collected_ids": sorted(self.collected_ids),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
