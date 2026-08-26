from __future__ import annotations

from dataclasses import asdict, dataclass
from math import hypot

from .config import TrackerConfig
from .matcher import LocateResult
from .position import CoordinateSpace, MapPosition, PositionState


TrackerState = PositionState


@dataclass(frozen=True)
class TrackerSnapshot:
    state: TrackerState
    x_px: float | None
    y_px: float | None
    raw_x_px: float | None
    raw_y_px: float | None
    confidence: float
    reference_id: str | None
    map_layer_id: str | None
    accepted: bool
    stale: bool
    reason: str | None
    position: MapPosition | None = None
    absolute_fix_age_seconds: float | None = None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["state"] = self.state.value
        result["position"] = self.position.to_dict() if self.position is not None else None
        return result


class LiveTracker:
    """Stabilize independent localization results without interacting with the game."""

    def __init__(self, config: TrackerConfig | None = None):
        self.config = config or TrackerConfig()
        self.state = TrackerState.LOST
        self._x: float | None = None
        self._y: float | None = None
        self._last_timestamp: float | None = None
        self._last_accepted_timestamp: float | None = None
        self._candidate_x: float | None = None
        self._candidate_y: float | None = None
        self._candidate_map_layer_id: str | None = None
        self._candidate_reference_id: str | None = None
        self._candidate_region_id: str | None = None
        self._candidate_coordinate_space: CoordinateSpace | None = None
        self._candidate_confidence = 0.0
        self._candidate_hits = 0
        self._map_layer_id: str | None = None
        self._reference_id: str | None = None
        self._region_id: str | None = None
        self._coordinate_space: CoordinateSpace | None = None
        self._confidence = 0.0
        self._last_absolute_fix_timestamp: float | None = None
        self._candidate_match_method: str | None = None

    @staticmethod
    def _distance(a_x: float, a_y: float, b_x: float, b_y: float) -> float:
        return hypot(a_x - b_x, a_y - b_y)

    def _valid(self, result: LocateResult) -> bool:
        enough_evidence = result.inliers >= self.config.min_inliers or (
            result.match_method == "template"
            and result.inliers >= 3
            and result.confidence >= max(self.config.min_confidence, 0.55)
        ) or (
            result.match_method == "anchors"
            and result.matches >= 1
            and result.confidence >= max(self.config.min_confidence, 0.5)
        )
        return bool(
            result.found
            and result.x_px is not None
            and result.y_px is not None
            and result.confidence >= self.config.min_confidence
            and enough_evidence
        )

    @staticmethod
    def _namespace(result: LocateResult) -> tuple[str, str, CoordinateSpace]:
        region_id = result.region_id or "unknown"
        layer_id = result.map_layer_id or "surface"
        coordinate_space = result.coordinate_space or (
            CoordinateSpace.SURFACE_ATLAS
            if layer_id == "surface"
            else CoordinateSpace.LAYER_LOCAL
        )
        return region_id, layer_id, coordinate_space

    def _set_candidate(self, x: float, y: float, result: LocateResult) -> None:
        region_id, layer_id, coordinate_space = self._namespace(result)
        self._candidate_x = x
        self._candidate_y = y
        self._candidate_map_layer_id = layer_id
        self._candidate_reference_id = result.reference_id
        self._candidate_region_id = region_id
        self._candidate_coordinate_space = coordinate_space
        self._candidate_confidence = result.confidence
        self._candidate_match_method = result.match_method
        self._candidate_hits = 1

    def _update_candidate(self, x: float, y: float, result: LocateResult) -> bool:
        if self._candidate_x is None or self._candidate_y is None:
            self._set_candidate(x, y, result)
            return False
        region_id, layer_id, coordinate_space = self._namespace(result)
        if (
            layer_id != self._candidate_map_layer_id
            or region_id != self._candidate_region_id
            or coordinate_space is not self._candidate_coordinate_space
            or self._distance(x, y, self._candidate_x, self._candidate_y)
            > self.config.candidate_radius_px
        ):
            self._set_candidate(x, y, result)
            return False
        count = self._candidate_hits + 1
        self._candidate_x += (x - self._candidate_x) / count
        self._candidate_y += (y - self._candidate_y) / count
        self._candidate_confidence = result.confidence
        self._candidate_match_method = result.match_method
        self._candidate_hits = count
        return True

    def _accept_candidate(self, timestamp: float) -> None:
        assert self._candidate_x is not None and self._candidate_y is not None
        self._x, self._y = self._candidate_x, self._candidate_y
        self._last_accepted_timestamp = timestamp
        self._map_layer_id = self._candidate_map_layer_id or self._map_layer_id
        self._reference_id = self._candidate_reference_id or self._reference_id
        self._region_id = self._candidate_region_id or self._region_id
        self._coordinate_space = (
            self._candidate_coordinate_space or self._coordinate_space
        )
        self._confidence = self._candidate_confidence
        if self._candidate_match_method != "motion":
            self._last_absolute_fix_timestamp = timestamp
        self._candidate_x = self._candidate_y = None
        self._candidate_map_layer_id = None
        self._candidate_reference_id = None
        self._candidate_region_id = None
        self._candidate_coordinate_space = None
        self._candidate_confidence = 0.0
        self._candidate_match_method = None
        self._candidate_hits = 0
        self.state = TrackerState.TRACKING

    @property
    def position_hint(self) -> MapPosition | None:
        if (
            self._x is None
            or self._y is None
            or self._map_layer_id is None
            or self._region_id is None
            or self._coordinate_space is None
        ):
            return None
        return MapPosition(
            region_id=self._region_id,
            layer_id=self._map_layer_id,
            coordinate_space=self._coordinate_space,
            x=self._x,
            y=self._y,
            confidence=self._confidence,
            state=self.state,
            timestamp=self._last_timestamp or 0.0,
            reference_id=self._reference_id,
        )

    def _snapshot(
        self,
        result: LocateResult,
        *,
        accepted: bool,
        stale: bool,
        reason: str | None,
    ) -> TrackerSnapshot:
        position = None
        if (
            self._x is not None
            and self._y is not None
            and self._region_id is not None
            and self._map_layer_id is not None
            and self._coordinate_space is not None
        ):
            position = MapPosition(
                region_id=self._region_id,
                layer_id=self._map_layer_id,
                coordinate_space=self._coordinate_space,
                x=round(self._x, 2),
                y=round(self._y, 2),
                confidence=self._confidence,
                state=self.state,
                timestamp=self._last_timestamp or 0.0,
                reference_id=self._reference_id,
            )
        return TrackerSnapshot(
            state=self.state,
            x_px=round(self._x, 2) if self._x is not None else None,
            y_px=round(self._y, 2) if self._y is not None else None,
            raw_x_px=result.x_px,
            raw_y_px=result.y_px,
            confidence=self._confidence if self._map_layer_id is not None else result.confidence,
            # The debug map must never jump to a one-frame localization guess.
            # Keep showing the last confirmed layer until a candidate is accepted.
            reference_id=self._reference_id,
            map_layer_id=self._map_layer_id,
            accepted=accepted,
            stale=stale,
            reason=reason,
            position=position,
            absolute_fix_age_seconds=(
                round(max(0.0, (self._last_timestamp or 0.0) - self._last_absolute_fix_timestamp), 4)
                if self._last_absolute_fix_timestamp is not None
                else None
            ),
        )

    def update(self, result: LocateResult, timestamp: float) -> TrackerSnapshot:
        if self._last_timestamp is not None and timestamp < self._last_timestamp:
            raise ValueError("Tracker timestamps must be monotonic")
        delta = 0.0 if self._last_timestamp is None else timestamp - self._last_timestamp
        self._last_timestamp = timestamp

        if not self._valid(result):
            expired = bool(
                self._last_accepted_timestamp is None
                or timestamp - self._last_accepted_timestamp > self.config.lost_timeout_seconds
            )
            if expired:
                self.state = TrackerState.LOST
                self._x = self._y = None
                self._candidate_x = self._candidate_y = None
                self._candidate_map_layer_id = None
                self._candidate_reference_id = None
                self._candidate_region_id = None
                self._candidate_coordinate_space = None
                self._candidate_confidence = 0.0
                self._candidate_match_method = None
                self._candidate_hits = 0
            elif self.state in (TrackerState.ACQUIRING, TrackerState.RELOCATING):
                self.state = TrackerState.TRACKING
            return self._snapshot(
                result,
                accepted=False,
                stale=not expired,
                reason=result.reason or "localization_below_tracker_threshold",
            )

        x, y = float(result.x_px), float(result.y_px)
        if self.state is TrackerState.LOST:
            self._set_candidate(x, y, result)
            if self.config.acquire_hits <= 1:
                self._accept_candidate(timestamp)
                return self._snapshot(result, accepted=True, stale=False, reason=None)
            self.state = TrackerState.ACQUIRING
            return self._snapshot(result, accepted=False, stale=False, reason="awaiting_confirmation")

        if self.state is TrackerState.ACQUIRING:
            self._update_candidate(x, y, result)
            if self._candidate_hits >= self.config.acquire_hits:
                self._accept_candidate(timestamp)
                return self._snapshot(result, accepted=True, stale=False, reason=None)
            return self._snapshot(result, accepted=False, stale=False, reason="awaiting_confirmation")

        if self._x is None or self._y is None:
            raise RuntimeError("Tracker has no position outside LOST/ACQUIRING state")
        allowed_distance = self.config.max_speed_px_per_second * delta + self.config.jump_margin_px
        distance_from_track = self._distance(x, y, self._x, self._y)
        result_region_id, result_layer_id, result_coordinate_space = self._namespace(result)
        same_layer = bool(
            result_layer_id == self._map_layer_id
            and result_region_id == self._region_id
            and result_coordinate_space is self._coordinate_space
        )

        if same_layer and distance_from_track <= allowed_distance:
            alpha = self.config.smoothing_alpha
            self._x += alpha * (x - self._x)
            self._y += alpha * (y - self._y)
            self._last_accepted_timestamp = timestamp
            self._candidate_x = self._candidate_y = None
            self._candidate_map_layer_id = None
            self._candidate_reference_id = None
            self._candidate_region_id = None
            self._candidate_coordinate_space = None
            self._candidate_confidence = 0.0
            self._candidate_match_method = None
            self._candidate_hits = 0
            self.state = TrackerState.TRACKING
            self._map_layer_id = result_layer_id
            self._reference_id = result.reference_id or self._reference_id
            self._region_id = result_region_id
            self._coordinate_space = result_coordinate_space
            self._confidence = result.confidence
            if result.match_method != "motion":
                self._last_absolute_fix_timestamp = timestamp
            return self._snapshot(result, accepted=True, stale=False, reason=None)

        if self.state is not TrackerState.RELOCATING:
            self._set_candidate(x, y, result)
            self.state = TrackerState.RELOCATING
        else:
            self._update_candidate(x, y, result)
        if self._candidate_hits >= self.config.relocate_hits:
            self._accept_candidate(timestamp)
            return self._snapshot(result, accepted=True, stale=False, reason="relocation_confirmed")
        return self._snapshot(result, accepted=False, stale=True, reason="possible_relocation")

    def pause(self, timestamp: float, reason: str = "minimap_not_visible") -> TrackerSnapshot:
        """Freeze tracker time while the game minimap is not on screen."""
        if self._last_timestamp is not None and timestamp < self._last_timestamp:
            raise ValueError("Tracker timestamps must be monotonic")
        if self._last_timestamp is not None and self._last_accepted_timestamp is not None:
            self._last_accepted_timestamp += timestamp - self._last_timestamp
        self._last_timestamp = timestamp
        return self._snapshot(
            LocateResult(found=False, reason=reason),
            accepted=False,
            stale=self._x is not None and self._y is not None,
            reason=reason,
        )
