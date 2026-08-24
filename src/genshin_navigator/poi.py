from __future__ import annotations

import json
import os
from dataclasses import dataclass
from math import hypot
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


class PoiCatalog:
    def __init__(self, pois: Iterable[PointOfInterest]):
        self.pois = tuple(pois)
        self._by_space: dict[tuple[str, str, CoordinateSpace], list[PointOfInterest]] = {}
        for poi in self.pois:
            key = (poi.region_id, poi.layer_id, poi.coordinate_space)
            self._by_space.setdefault(key, []).append(poi)

    @classmethod
    def load(cls, path: str | Path) -> PoiCatalog:
        with Path(path).open("r", encoding="utf-8") as stream:
            raw = json.load(stream)
        if int(raw.get("format_version", 0)) != 1:
            raise ValueError("Unsupported POI catalog format")
        return cls(PointOfInterest.from_dict(item) for item in raw["pois"])

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
