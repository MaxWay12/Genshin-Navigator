from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees

from .calibration import DistanceCalibration
from .poi import PointOfInterest, PoiRepository, ProgressRepository
from .position import CoordinateSpace, MapPosition, PositionState
from .tracker import TrackerSnapshot


SpaceKey = tuple[str, str, CoordinateSpace]


@dataclass(frozen=True)
class NavigationSnapshot:
    target: PointOfInterest | None
    position: MapPosition | None
    available: bool
    stale: bool
    distance_m: float | None
    bearing_degrees: float | None
    reason: str | None


class NavigationController:
    def __init__(
        self,
        catalog: PoiRepository,
        progress: ProgressRepository,
        *,
        target_kinds: set[str] | None = None,
        calibration: DistanceCalibration | None = None,
    ):
        self.catalog = catalog
        self.progress = progress
        self.target_kinds = target_kinds
        self.calibration = calibration
        self.skipped_ids: set[str] = set()
        self._selected_by_space: dict[SpaceKey, str] = {}
        self._last_position_by_space: dict[SpaceKey, MapPosition] = {}
        self._active_key: SpaceKey | None = None
        self._history: list[tuple[str, str, SpaceKey]] = []
        self._last_snapshot = NavigationSnapshot(
            None, None, False, True, None, None, "no_position"
        )

    @staticmethod
    def _key(position: MapPosition) -> SpaceKey:
        return position.region_id, position.layer_id, position.coordinate_space

    def _eligible(self, position: MapPosition) -> list[tuple[PointOfInterest, float]]:
        excluded = self.progress.collected_ids | self.skipped_ids
        return self.catalog.nearest(
            position,
            kinds=self.target_kinds,
            exclude_ids=excluded,
            limit=max(1, len(self.catalog.pois)),
        )

    def _target_for(self, position: MapPosition) -> PointOfInterest | None:
        key = self._key(position)
        selected_id = self._selected_by_space.get(key)
        candidates = self._eligible(position)
        by_id = {poi.id: poi for poi, _ in candidates}
        target = by_id.get(selected_id) if selected_id is not None else None
        if target is None and candidates:
            target = candidates[0][0]
            self._selected_by_space[key] = target.id
        return target

    def update(self, snapshot: TrackerSnapshot) -> NavigationSnapshot:
        position = snapshot.position
        if position is not None:
            key = self._key(position)
            self._active_key = key
            target = self._target_for(position)
        else:
            key = self._active_key
            target = self.current_target

        fresh = bool(
            position is not None
            and snapshot.state is PositionState.TRACKING
            and not snapshot.stale
        )
        if not fresh:
            self._last_snapshot = NavigationSnapshot(
                target=target,
                position=position,
                available=False,
                stale=True,
                distance_m=None,
                bearing_degrees=None,
                reason=snapshot.reason or snapshot.state.value.lower(),
            )
            return self._last_snapshot

        assert position is not None
        self._last_position_by_space[self._key(position)] = position
        if target is None:
            self._last_snapshot = NavigationSnapshot(
                None, position, False, False, None, None, "no_target"
            )
            return self._last_snapshot
        dx, dy = target.x - position.x, target.y - position.y
        bearing = (degrees(atan2(dx, -dy)) + 360.0) % 360.0
        world_distance = self.catalog.world_distance(position, target)
        distance_m = None
        if (
            world_distance is not None
            and self.calibration is not None
            and self.calibration.region_id == position.region_id
        ):
            distance_m = world_distance * self.calibration.meters_per_world_unit
        self._last_snapshot = NavigationSnapshot(
            target, position, True, False, distance_m, bearing, None
        )
        return self._last_snapshot

    @property
    def current_target(self) -> PointOfInterest | None:
        if self._active_key is None:
            return None
        target_id = self._selected_by_space.get(self._active_key)
        if target_id is None:
            return None
        return next((poi for poi in self.catalog.pois if poi.id == target_id), None)

    def _cycle(self, offset: int) -> PointOfInterest | None:
        if self._active_key is None:
            return None
        position = self._last_position_by_space.get(self._active_key)
        if position is None:
            return self.current_target
        candidates = [poi for poi, _ in self._eligible(position)]
        if not candidates:
            self._selected_by_space.pop(self._active_key, None)
            return None
        current = self.current_target
        index = next(
            (index for index, poi in enumerate(candidates) if current and poi.id == current.id),
            0,
        )
        target = candidates[(index + offset) % len(candidates)]
        self._selected_by_space[self._active_key] = target.id
        return target

    def next_target(self) -> PointOfInterest | None:
        return self._cycle(1)

    def previous_target(self) -> PointOfInterest | None:
        return self._cycle(-1)

    def skip(self) -> PointOfInterest | None:
        target = self.current_target
        if target is None or self._active_key is None:
            return None
        self.skipped_ids.add(target.id)
        self._history.append(("skip", target.id, self._active_key))
        self._selected_by_space.pop(self._active_key, None)
        position = self._last_position_by_space.get(self._active_key)
        return self._target_for(position) if position is not None else None

    def mark_collected(self) -> PointOfInterest | None:
        target = self.current_target
        if target is None or self._active_key is None:
            return None
        self.progress.mark_collected(target.id)
        self._history.append(("collected", target.id, self._active_key))
        self._selected_by_space.pop(self._active_key, None)
        position = self._last_position_by_space.get(self._active_key)
        return self._target_for(position) if position is not None else None

    def undo(self) -> PointOfInterest | None:
        if not self._history:
            return self.current_target
        action, poi_id, key = self._history.pop()
        if action == "skip":
            self.skipped_ids.discard(poi_id)
        else:
            self.progress.unmark_collected(poi_id)
        self._selected_by_space[key] = poi_id
        self._active_key = key
        return self.current_target
