from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from math import isfinite


class PositionState(str, Enum):
    LOST = "LOST"
    ACQUIRING = "ACQUIRING"
    TRACKING = "TRACKING"
    RELOCATING = "RELOCATING"


class CoordinateSpace(str, Enum):
    """Coordinate system used by a map position."""

    SURFACE_ATLAS = "surface_atlas"
    LAYER_LOCAL = "layer_local"


@dataclass(frozen=True)
class MapPosition:
    """Stable public position contract shared by navigation components."""

    region_id: str
    layer_id: str
    coordinate_space: CoordinateSpace
    x: float
    y: float
    confidence: float
    state: PositionState
    timestamp: float
    reference_id: str | None = None

    def __post_init__(self) -> None:
        if not self.region_id.strip():
            raise ValueError("MapPosition.region_id must not be empty")
        if not self.layer_id.strip():
            raise ValueError("MapPosition.layer_id must not be empty")
        if not all(isfinite(value) for value in (self.x, self.y, self.timestamp)):
            raise ValueError("MapPosition coordinates and timestamp must be finite")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("MapPosition.confidence must be between 0 and 1")

    def same_space(self, other: MapPosition) -> bool:
        return bool(
            self.region_id == other.region_id
            and self.layer_id == other.layer_id
            and self.coordinate_space is other.coordinate_space
        )

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["coordinate_space"] = self.coordinate_space.value
        result["state"] = self.state.value
        return result
