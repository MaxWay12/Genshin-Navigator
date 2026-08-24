from __future__ import annotations

import json
import os
import statistics
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Iterable

from .poi import PoiCatalog, PointOfInterest
from .position import CoordinateSpace, MapPosition


@dataclass(frozen=True)
class CalibrationSample:
    start: MapPosition
    end: MapPosition
    shown_distance_m: float
    world_distance: float

    @property
    def meters_per_world_unit(self) -> float:
        return self.shown_distance_m / self.world_distance

    def to_dict(self) -> dict[str, object]:
        return {
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "shown_distance_m": self.shown_distance_m,
            "world_distance": self.world_distance,
            "meters_per_world_unit": self.meters_per_world_unit,
        }


@dataclass(frozen=True)
class DistanceCalibration:
    region_id: str
    meters_per_world_unit: float
    samples: tuple[CalibrationSample, ...] = ()
    max_relative_error: float = 0.1

    FORMAT_VERSION = 1

    def __post_init__(self) -> None:
        if not self.region_id.strip():
            raise ValueError("Calibration region_id must not be empty")
        if not isfinite(self.meters_per_world_unit) or self.meters_per_world_unit <= 0:
            raise ValueError("meters_per_world_unit must be positive and finite")

    def to_dict(self) -> dict[str, object]:
        return {
            "format_version": self.FORMAT_VERSION,
            "status": "valid",
            "region_id": self.region_id,
            "meters_per_world_unit": self.meters_per_world_unit,
            "max_relative_error": self.max_relative_error,
            "samples": [sample.to_dict() for sample in self.samples],
        }

    @classmethod
    def load(cls, path: str | Path) -> DistanceCalibration | None:
        source = Path(path)
        if not source.exists():
            return None
        raw = json.loads(source.read_text(encoding="utf-8"))
        if int(raw.get("format_version", 0)) != cls.FORMAT_VERSION:
            raise ValueError("Unsupported distance calibration format")
        if raw.get("status") != "valid":
            return None
        return cls(
            region_id=str(raw["region_id"]),
            meters_per_world_unit=float(raw["meters_per_world_unit"]),
            max_relative_error=float(raw.get("max_relative_error", 0.1)),
        )

    def save_atomic(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, target)
        return target


class CalibrationSession:
    def __init__(
        self,
        catalog: PoiCatalog,
        *,
        region_id: str = "fontaine",
        required_samples: int = 3,
        max_relative_error: float = 0.1,
    ):
        if required_samples < 1:
            raise ValueError("required_samples must be positive")
        self.catalog = catalog
        self.region_id = region_id
        self.required_samples = required_samples
        self.max_relative_error = max_relative_error
        self.samples: list[CalibrationSample] = []

    def add_sample(
        self, start: MapPosition, end: MapPosition, shown_distance_m: float
    ) -> CalibrationSample:
        if start.region_id != self.region_id or end.region_id != self.region_id:
            raise ValueError("Calibration sample belongs to another region")
        if not start.same_space(end):
            raise ValueError("Calibration endpoints must use the same coordinate space")
        if start.coordinate_space is not CoordinateSpace.SURFACE_ATLAS:
            raise ValueError("Distance calibration must be measured on the surface")
        if not isfinite(shown_distance_m) or not 100 <= shown_distance_m <= 300:
            raise ValueError("Shown distance must be between 100 and 300 meters")
        probe = PointOfInterest(
            id="calibration:end",
            kind="calibration",
            name="Calibration endpoint",
            region_id=end.region_id,
            layer_id=end.layer_id,
            coordinate_space=end.coordinate_space,
            x=end.x,
            y=end.y,
        )
        world_distance = self.catalog.world_distance(start, probe)
        if world_distance is None:
            raise ValueError("POI catalog has no metric for this coordinate space")
        if world_distance <= 1e-9:
            raise ValueError("Calibration endpoints are identical")
        sample = CalibrationSample(start, end, shown_distance_m, world_distance)
        self.samples.append(sample)
        return sample

    def result(self) -> DistanceCalibration:
        if len(self.samples) < self.required_samples:
            raise ValueError(
                f"Calibration is incomplete: {len(self.samples)}/{self.required_samples} samples"
            )
        selected = self.samples[: self.required_samples]
        factor = statistics.median(sample.meters_per_world_unit for sample in selected)
        errors = [
            abs(sample.world_distance * factor - sample.shown_distance_m)
            / sample.shown_distance_m
            for sample in selected
        ]
        worst_error = max(errors)
        if worst_error > self.max_relative_error:
            raise ValueError(
                f"Calibration samples disagree: worst deviation {worst_error:.1%} exceeds "
                f"{self.max_relative_error:.1%}"
            )
        return DistanceCalibration(
            region_id=self.region_id,
            meters_per_world_unit=factor,
            samples=tuple(selected),
            max_relative_error=self.max_relative_error,
        )

    def write_draft(self, path: str | Path, *, error: str | None = None) -> Path:
        final_path = Path(path)
        draft = final_path.with_suffix(final_path.suffix + ".draft")
        draft.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": 1,
            "status": "invalid" if error else "incomplete",
            "region_id": self.region_id,
            "required_samples": self.required_samples,
            "error": error,
            "samples": [sample.to_dict() for sample in self.samples],
        }
        temporary = draft.with_suffix(draft.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, draft)
        return draft


def load_calibration(path: str | Path | None) -> DistanceCalibration | None:
    return DistanceCalibration.load(path) if path is not None else None
