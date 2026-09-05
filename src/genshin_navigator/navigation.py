from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from math import atan2, degrees
from pathlib import Path

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


@dataclass(frozen=True)
class TargetCandidate:
    poi: PointOfInterest
    distance_m: float | None
    selected: bool
    selectable: bool


@dataclass(frozen=True)
class NavigationPreferences:
    target_kinds: frozenset[str]
    blacklisted_ids: frozenset[str] = frozenset()
    selected_targets: tuple[tuple[str, str, str, str], ...] = ()


class NavigationPreferencesStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self, default_kinds: set[str]) -> NavigationPreferences:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if int(raw.get("format_version", 0)) not in (1, 2):
                raise ValueError("unsupported preferences format")
            kinds = frozenset(map(str, raw.get("target_kinds", [])))
            blacklist = frozenset(map(str, raw.get("blacklisted_ids", [])))
            selected = tuple(tuple(row) for row in raw.get("selected_targets", [])
                             if isinstance(row, list) and len(row) == 4 and all(isinstance(item, str) for item in row)
                             and row[2] in {space.value for space in CoordinateSpace})
            return NavigationPreferences(kinds or frozenset(default_kinds), blacklist, selected)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return NavigationPreferences(frozenset(default_kinds))

    def save(self, preferences: NavigationPreferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "format_version": 2,
                "target_kinds": sorted(preferences.target_kinds),
                "blacklisted_ids": sorted(preferences.blacklisted_ids),
                "selected_targets": preferences.selected_targets,
            },
            ensure_ascii=False,
            indent=2,
        )
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


@dataclass(frozen=True)
class NavigationSummary:
    target_filter: str
    total: int
    collected: int
    remaining: int
    session_skipped: int
    blacklisted: int


class NavigationController:
    def __init__(
        self,
        catalog: PoiRepository,
        progress: ProgressRepository,
        *,
        target_kinds: set[str] | None = None,
        available_target_kinds: set[str] | None = None,
        calibration: DistanceCalibration | None = None,
        preferences_store: NavigationPreferencesStore | None = None,
        max_target_distance_m: float | None = None,
    ):
        self.catalog = catalog
        self.progress = progress
        default_kinds = set(target_kinds or ())
        self.available_target_kinds = set(available_target_kinds or default_kinds)
        self.preferences_store = preferences_store
        preferences = (
            preferences_store.load(default_kinds)
            if preferences_store is not None
            else NavigationPreferences(frozenset(default_kinds))
        )
        valid_kinds = set(preferences.target_kinds) & self.available_target_kinds
        self.target_kinds = valid_kinds or default_kinds or None
        self.blacklisted_ids = set(preferences.blacklisted_ids)
        self.max_target_distance_m = max_target_distance_m
        self.calibration = calibration
        self.skipped_ids: set[str] = set()
        self._selected_by_space: dict[SpaceKey, str] = {
            (region, layer, CoordinateSpace(space)): identifier
            for region, layer, space, identifier in preferences.selected_targets
        }
        self._orders: dict[SpaceKey, list[str]] = {}
        self._selection_ready = False
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
        excluded = self.progress.collected_ids | self.skipped_ids | self.blacklisted_ids
        candidates = self.catalog.nearest(
            position,
            kinds=self.target_kinds,
            exclude_ids=excluded,
            limit=max(1, len(self.catalog.pois)),
        )
        if self.max_target_distance_m is None:
            return candidates
        filtered = []
        for poi, distance in candidates:
            world_distance = self.catalog.world_distance(position, poi)
            meters = (
                world_distance * self.calibration.meters_per_world_unit
                if world_distance is not None
                and self.calibration is not None
                and self.calibration.supports_region(position.region_id)
                else None
            )
            if meters is None or meters <= self.max_target_distance_m:
                filtered.append((poi, distance))
        return filtered

    def _save_preferences(self) -> None:
        if self.preferences_store is not None:
            self.preferences_store.save(
                NavigationPreferences(
                    frozenset(self.target_kinds or ()),
                    frozenset(self.blacklisted_ids),
                    tuple((region, layer, space.value, identifier)
                          for (region, layer, space), identifier in sorted(self._selected_by_space.items())),
                )
            )

    def _ordered(self, position: MapPosition) -> list[tuple[PointOfInterest, float]]:
        key = self._key(position)
        eligible = self._eligible(position)
        by_id = {poi.id: (poi, distance) for poi, distance in eligible}
        order = self._orders.get(key, [])
        if not any(identifier in by_id for identifier in order):
            order = [poi.id for poi, _ in eligible]
            self._orders[key] = order
        return [by_id[identifier] for identifier in order if identifier in by_id]

    def refresh_candidates(self) -> None:
        if self._selection_ready and self._active_key is not None:
            self._orders.pop(self._active_key, None)

    def candidates(self, section: str = "available") -> tuple[TargetCandidate, ...]:
        position = self._last_position_by_space.get(self._active_key)
        if position is None:
            return ()
        if section == "available":
            points = [poi for poi, _ in self._ordered(position)]
        else:
            ids = self.skipped_ids if section == "skipped" else self.blacklisted_ids
            points = [poi for poi in self.catalog.on_layer(position) if poi.id in ids]
        result = []
        selected = self.current_target
        for poi in points:
            distance = self.catalog.world_distance(position, poi) if self._selection_ready else None
            meters = (distance * self.calibration.meters_per_world_unit
                      if distance is not None and self.calibration and self.calibration.supports_region(position.region_id) else None)
            result.append(TargetCandidate(poi, meters, bool(selected and selected.id == poi.id), self._selection_ready))
        return tuple(result)

    def select_target(self, identifier: str) -> bool:
        if not self._selection_ready or self._active_key is None:
            return False
        if not any(row.poi.id == identifier for row in self.candidates()):
            return False
        self._selected_by_space[self._active_key] = identifier
        self._save_preferences()
        return True

    def restore_target(self, identifier: str, section: str) -> bool:
        if not any(row.poi.id == identifier for row in self.candidates(section)):
            return False
        if section == "skipped":
            self.skipped_ids.discard(identifier)
        elif section == "hidden":
            self.blacklisted_ids.discard(identifier)
        else:
            return False
        self._save_preferences()
        return True

    def _advance_removed(self, removed_id: str) -> PointOfInterest | None:
        key = self._active_key
        position = self._last_position_by_space.get(key)
        if position is None:
            return None
        order = self._orders.get(key, [])
        index = order.index(removed_id) if removed_id in order else -1
        eligible = {poi.id for poi, _ in self._eligible(position)}
        next_id = next((identifier for identifier in order[index + 1:] + order[:index + 1]
                        if identifier in eligible), None)
        if next_id is None:
            self._selected_by_space.pop(key, None)
        else:
            self._selected_by_space[key] = next_id
        target = self._target_for(position)
        self._save_preferences()
        return target

    @property
    def target_filter_label(self) -> str:
        kinds = self.target_kinds or set()
        labels = {
            "chest": "сундуки",
            "hydroculus": "гидрокулы",
            "waypoint": "телепорты",
            "domain": "подземелья",
        }
        if kinds == self.available_target_kinds and len(kinds) > 1:
            return "все"
        return "+".join(labels.get(kind, kind) for kind in sorted(kinds)) or "все"

    @property
    def summary(self) -> NavigationSummary:
        region_id = self._active_key[0] if self._active_key else None
        points = [
            poi for poi in self.catalog.pois
            if (region_id is None or poi.region_id == region_id)
            and (self.target_kinds is None or poi.kind in self.target_kinds)
        ]
        ids = {poi.id for poi in points}
        collected = len(ids & self.progress.collected_ids)
        blacklisted = len(ids & self.blacklisted_ids)
        return NavigationSummary(
            self.target_filter_label,
            len(points),
            collected,
            max(0, len(points) - collected - blacklisted),
            len(ids & self.skipped_ids),
            blacklisted,
        )

    def cycle_target_filter(self) -> str:
        kinds = sorted(self.available_target_kinds)
        modes: list[frozenset[str]] = [frozenset({kind}) for kind in kinds]
        if len(kinds) > 1:
            modes.append(frozenset(kinds))
        current = frozenset(self.target_kinds or ())
        try:
            index = modes.index(current)
        except ValueError:
            index = -1
        self.target_kinds = set(modes[(index + 1) % len(modes)]) if modes else None
        if self._active_key is not None:
            self._selected_by_space.pop(self._active_key, None)
        self._orders.clear()
        self._save_preferences()
        return self.target_filter_label

    def blacklist_current(self) -> PointOfInterest | None:
        target = self.current_target
        if target is None or self._active_key is None or not self._selection_ready:
            return None
        self.blacklisted_ids.add(target.id)
        self._history.append(("blacklist", target.id, self._active_key))
        return self._advance_removed(target.id)

    def _target_for(self, position: MapPosition) -> PointOfInterest | None:
        key = self._key(position)
        selected_id = self._selected_by_space.get(key)
        candidates = self._ordered(position)
        by_id = {poi.id: poi for poi, _ in candidates}
        target = by_id.get(selected_id) if selected_id is not None else None
        if target is None and candidates:
            target = candidates[0][0]
            self._selected_by_space[key] = target.id
            self._save_preferences()
        elif target is None and selected_id is not None:
            self._selected_by_space.pop(key, None)
            self._save_preferences()
        return target

    def update(self, snapshot: TrackerSnapshot) -> NavigationSnapshot:
        position = snapshot.position
        self._selection_ready = bool(position is not None and snapshot.state is PositionState.TRACKING and not snapshot.stale)
        if self._selection_ready:
            key = self._key(position)
            if key != self._active_key:
                self._orders.pop(key, None)
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
            and self.calibration.supports_region(position.region_id)
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
        if not self._selection_ready:
            return self.current_target
        if self._active_key is None:
            return None
        position = self._last_position_by_space.get(self._active_key)
        if position is None:
            return self.current_target
        candidates = [poi for poi, _ in self._ordered(position)]
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
        self._save_preferences()
        return target

    def next_target(self) -> PointOfInterest | None:
        return self._cycle(1)

    def previous_target(self) -> PointOfInterest | None:
        return self._cycle(-1)

    def skip(self) -> PointOfInterest | None:
        target = self.current_target
        if target is None or self._active_key is None or not self._selection_ready:
            return None
        self.skipped_ids.add(target.id)
        self._history.append(("skip", target.id, self._active_key))
        return self._advance_removed(target.id)

    def mark_collected(self) -> PointOfInterest | None:
        target = self.current_target
        if target is None or self._active_key is None or not self._selection_ready:
            return None
        self.progress.mark_collected(target.id)
        self._history.append(("collected", target.id, self._active_key))
        return self._advance_removed(target.id)

    def undo(self) -> PointOfInterest | None:
        if not self._history:
            return self.current_target
        action, poi_id, key = self._history.pop()
        if action == "skip":
            self.skipped_ids.discard(poi_id)
        elif action == "blacklist":
            self.blacklisted_ids.discard(poi_id)
            self._save_preferences()
        else:
            self.progress.unmark_collected(poi_id)
        self._selected_by_space[key] = poi_id
        self._save_preferences()
        return self.current_target
