from __future__ import annotations

import json
from dataclasses import dataclass, replace
from math import hypot
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from .capture import load_image
from .config import LocalSearchConfig, MatcherConfig
from .matcher import CandidateMatch, LocateResult, MinimapMatcher, UndergroundMinimapMatcher
from .position import CoordinateSpace, MapPosition


class Locator(Protocol):
    def locate(self, minimap: np.ndarray) -> LocateResult: ...


@dataclass(frozen=True)
class PyramidLevel:
    id: str
    matcher: Locator
    local_to_canonical: np.ndarray
    resolution_scale: float = 1.0
    map_layer_id: str = "surface"
    coordinate_space: CoordinateSpace = CoordinateSpace.SURFACE_ATLAS
    display_name: str = ""
    floor_label: str = ""

    def __post_init__(self) -> None:
        matrix = np.asarray(self.local_to_canonical, dtype=np.float64)
        if matrix.shape != (3, 3):
            raise ValueError(f"Pyramid level {self.id!r} must have a 3x3 homography")
        if not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-12:
            raise ValueError(f"Pyramid level {self.id!r} has an invalid homography")
        if self.resolution_scale <= 0:
            raise ValueError(f"Pyramid level {self.id!r} resolution_scale must be positive")
        if not self.map_layer_id.strip():
            raise ValueError(f"Pyramid level {self.id!r} map_layer_id must not be empty")
        object.__setattr__(self, "local_to_canonical", matrix)

    @property
    def layer_display_name(self) -> str:
        parts = [part for part in (self.display_name, self.floor_label) if part]
        return " · ".join(parts) if parts else (
            "Фонтейн · Поверхность"
            if self.map_layer_id == "surface"
            else self.map_layer_id
        )


class PyramidMatcher:
    """Locate on surface-atlas or independent layer-local coordinate spaces."""

    def __init__(
        self,
        canonical_size: tuple[int, int],
        levels: list[PyramidLevel],
        early_accept_confidence: float = 0.98,
        region_id: str = "unknown",
    ):
        width, height = canonical_size
        if width <= 0 or height <= 0:
            raise ValueError("Canonical atlas size must be positive")
        if not levels:
            raise ValueError("Reference pyramid has no levels")
        if not 0 < early_accept_confidence <= 1:
            raise ValueError("early_accept_confidence must be within (0, 1]")
        self.canonical_size = canonical_size
        self.levels = levels
        self.early_accept_confidence = early_accept_confidence
        self.region_id = region_id

    @property
    def layer_labels(self) -> dict[str, str]:
        labels: dict[str, str] = {"surface": "Фонтейн · Поверхность"}
        for level in self.levels:
            if level.map_layer_id != "surface" or level.display_name or level.floor_label:
                labels[level.map_layer_id] = level.layer_display_name
        return labels

    @staticmethod
    def _project(matrix: np.ndarray, x: float, y: float) -> tuple[float, float]:
        source = np.float64([x, y, 1.0])
        target = matrix @ source
        if abs(target[2]) < 1e-12:
            raise ValueError("Reference transform projects a point to infinity")
        return float(target[0] / target[2]), float(target[1] / target[2])

    @classmethod
    def _canonical_scale(cls, matrix: np.ndarray, x: float, y: float, local_scale: float) -> float:
        center = cls._project(matrix, x, y)
        x_step = cls._project(matrix, x + 1.0, y)
        y_step = cls._project(matrix, x, y + 1.0)
        transform_scale = (
            hypot(x_step[0] - center[0], x_step[1] - center[1])
            + hypot(y_step[0] - center[0], y_step[1] - center[1])
        ) / 2.0
        return local_scale * transform_scale

    def _to_position(self, result: LocateResult, level: PyramidLevel) -> LocateResult:
        assert result.x_px is not None and result.y_px is not None
        if level.coordinate_space is CoordinateSpace.LAYER_LOCAL:
            map_height, map_width = level.matcher.reference_map.shape[:2]  # type: ignore[attr-defined]
            return replace(
                result,
                x_normalized=round(result.x_px / map_width, 6),
                y_normalized=round(result.y_px / map_height, 6),
                reference_id=level.id,
                map_layer_id=level.map_layer_id,
                reference_resolution_scale=level.resolution_scale,
                reference_x_px=result.x_px,
                reference_y_px=result.y_px,
                canonical_scale=None,
                region_id=self.region_id,
                coordinate_space=CoordinateSpace.LAYER_LOCAL,
            )
        x, y = self._project(level.local_to_canonical, result.x_px, result.y_px)
        width, height = self.canonical_size
        if not (0 <= x < width and 0 <= y < height):
            return replace(
                result,
                found=False,
                x_px=None,
                y_px=None,
                x_normalized=None,
                y_normalized=None,
                reason="position_outside_canonical_map",
                reference_id=level.id,
                map_layer_id=level.map_layer_id,
                region_id=self.region_id,
                coordinate_space=CoordinateSpace.SURFACE_ATLAS,
            )
        canonical_scale = None
        if result.scale is not None:
            canonical_scale = self._canonical_scale(
                level.local_to_canonical, result.x_px, result.y_px, result.scale
            )
        return replace(
            result,
            x_px=round(x, 2),
            y_px=round(y, 2),
            x_normalized=round(x / width, 6),
            y_normalized=round(y / height, 6),
            reference_id=level.id,
            map_layer_id=level.map_layer_id,
            reference_resolution_scale=level.resolution_scale,
            reference_x_px=result.x_px,
            reference_y_px=result.y_px,
            canonical_scale=round(canonical_scale, 4) if canonical_scale is not None else None,
            region_id=self.region_id,
            coordinate_space=CoordinateSpace.SURFACE_ATLAS,
        )

    def _tag_failure(self, result: LocateResult, level: PyramidLevel) -> LocateResult:
        """Keep failed observations in the same diagnostic position namespace."""
        return replace(
            result,
            reference_id=level.id,
            map_layer_id=level.map_layer_id,
            region_id=self.region_id,
            coordinate_space=level.coordinate_space,
        )

    @staticmethod
    def _with_candidates(
        result: LocateResult, observations: list[LocateResult]
    ) -> LocateResult:
        ranked = sorted(
            observations,
            key=lambda item: (item.confidence, item.inliers, item.matches),
            reverse=True,
        )[:5]
        return replace(
            result,
            candidates=tuple(
                CandidateMatch(
                    reference_id=item.reference_id,
                    map_layer_id=item.map_layer_id,
                    confidence=item.confidence,
                    matches=item.matches,
                    inliers=item.inliers,
                    reason=item.reason,
                    found=item.found,
                )
                for item in ranked
            ),
        )

    def locate(self, minimap: np.ndarray) -> LocateResult:
        candidates: list[LocateResult] = []
        failures: list[LocateResult] = []
        attempts: list[tuple[PyramidLevel, LocateResult]] = []
        for level in self.levels:
            local_result = level.matcher.locate(minimap)
            attempts.append((level, local_result))
            if local_result.found:
                canonical_result = self._to_position(local_result, level)
                if canonical_result.found:
                    candidates.append(canonical_result)
                    if (
                        canonical_result.confidence >= self.early_accept_confidence
                        and canonical_result.inliers >= 20
                    ):
                        return self._with_candidates(
                            canonical_result, candidates + failures
                        )
                else:
                    failures.append(canonical_result)
            else:
                failures.append(self._tag_failure(local_result, level))

        best_confidence = max((item.confidence for item in candidates), default=0.0)
        if best_confidence < 0.35:
            fallback_attempts = sorted(
                (
                    (level, result)
                    for level, result in attempts
                    if hasattr(level.matcher, "locate_fallback")
                ),
                key=lambda item: (item[1].inliers, item[1].matches),
                reverse=True,
            )[:12]
            for level, _ in fallback_attempts:
                fallback = level.matcher.locate_fallback(minimap)  # type: ignore[attr-defined]
                if fallback.found:
                    canonical = self._to_position(fallback, level)
                    if canonical.found:
                        candidates.append(canonical)
                else:
                    failures.append(self._tag_failure(fallback, level))

        if candidates:
            best = max(candidates, key=lambda item: (item.confidence, item.inliers, item.matches))
            return self._with_candidates(best, candidates + failures)
        best = max(failures, key=lambda item: (item.inliers, item.matches, item.confidence))
        best = replace(best, reason="no_pyramid_level_matched")
        return self._with_candidates(best, failures)

    def locate_near(
        self,
        minimap: np.ndarray,
        position: MapPosition,
        config: LocalSearchConfig,
    ) -> LocateResult:
        """Search only near a continuous track on the same logical map layer."""
        if position.region_id != self.region_id:
            return LocateResult(
                found=False,
                reason="position_region_mismatch",
                map_layer_id=position.layer_id,
                region_id=position.region_id,
                coordinate_space=position.coordinate_space,
            )
        x_px, y_px = position.x, position.y
        map_layer_id = position.layer_id
        candidates: list[LocateResult] = []
        failures: list[LocateResult] = []
        fallback_levels: list[PyramidLevel] = []
        for level in self.levels:
            if level.map_layer_id != map_layer_id or not hasattr(level.matcher, "locate_near"):
                continue
            if level.coordinate_space is not position.coordinate_space:
                continue
            if hasattr(level.matcher, "locate_fallback"):
                fallback_levels.append(level)
            if level.coordinate_space is CoordinateSpace.LAYER_LOCAL:
                local_x, local_y = x_px, y_px
                local_radius = config.radius_px
            else:
                canonical_to_local = np.linalg.inv(level.local_to_canonical)
                canonical_center = np.float64([x_px, y_px, 1.0])
                local_center_h = canonical_to_local @ canonical_center
                if abs(local_center_h[2]) < 1e-12:
                    continue
                local_x = float(local_center_h[0] / local_center_h[2])
                local_y = float(local_center_h[1] / local_center_h[2])
                local_edge_h = canonical_to_local @ np.float64(
                    [x_px + config.radius_px, y_px, 1.0]
                )
                local_edge_x = float(local_edge_h[0] / local_edge_h[2])
                local_edge_y = float(local_edge_h[1] / local_edge_h[2])
                local_radius = hypot(local_edge_x - local_x, local_edge_y - local_y)
            map_height, map_width = level.matcher.reference_map.shape[:2]
            if not (0 <= local_x < map_width and 0 <= local_y < map_height):
                continue
            local_result = level.matcher.locate_near(  # type: ignore[attr-defined]
                minimap,
                (local_x, local_y),
                local_radius,
                ratio_threshold=config.ratio_threshold,
                min_matches=config.min_matches,
                min_inliers=config.min_inliers,
            )
            if local_result.found:
                canonical_result = self._to_position(local_result, level)
                if canonical_result.found and hypot(
                    float(canonical_result.x_px) - x_px,
                    float(canonical_result.y_px) - y_px,
                ) <= config.radius_px:
                    candidates.append(canonical_result)
                else:
                    failures.append(replace(canonical_result, found=False, reason="outside_local_search_radius"))
            else:
                failures.append(self._tag_failure(local_result, level))
        if candidates:
            best = max(candidates, key=lambda item: (item.confidence, item.inliers, item.matches))
            return self._with_candidates(best, candidates + failures)
        for level in fallback_levels:
            fallback = level.matcher.locate_fallback(minimap)  # type: ignore[attr-defined]
            if not fallback.found:
                continue
            canonical = self._to_position(fallback, level)
            if canonical.found and hypot(
                float(canonical.x_px) - x_px,
                float(canonical.y_px) - y_px,
            ) <= config.radius_px:
                candidates.append(canonical)
        if candidates:
            best = max(candidates, key=lambda item: (item.confidence, item.inliers, item.matches))
            return self._with_candidates(best, candidates + failures)
        if failures:
            best = max(failures, key=lambda item: (item.inliers, item.matches, item.confidence))
            best = replace(best, reason="no_local_level_matched")
            return self._with_candidates(best, failures)
        return LocateResult(
            found=False,
            reason="no_local_level_available",
            map_layer_id=map_layer_id,
            region_id=position.region_id,
            coordinate_space=position.coordinate_space,
        )


def load_pyramid(path: str | Path, config: MatcherConfig | None = None) -> PyramidMatcher:
    manifest_path = Path(path).resolve()
    with manifest_path.open("r", encoding="utf-8") as stream:
        raw = json.load(stream)

    width, height = map(int, raw["canonical_size"])
    region_id = str(raw.get("region_id", "unknown"))
    default_map_layer_id = str(raw.get("default_map_layer_id", "surface"))
    levels: list[PyramidLevel] = []
    for item in raw["levels"]:
        image_path = Path(item["image"])
        if not image_path.is_absolute():
            image_path = (manifest_path.parent / image_path).resolve()
        level_config = config or MatcherConfig()
        if "matcher" in item:
            level_config = replace(level_config, **item["matcher"])
        matrix = np.asarray(item.get("local_to_canonical", np.eye(3)), dtype=np.float64)
        map_layer_id = str(item.get("map_layer_id", default_map_layer_id))
        coordinate_space = CoordinateSpace(
            item.get(
                "coordinate_space",
                CoordinateSpace.SURFACE_ATLAS.value
                if map_layer_id == "surface"
                else CoordinateSpace.LAYER_LOCAL.value,
            )
        )
        metadata = item.get("metadata") or {}
        levels.append(
            PyramidLevel(
                id=str(item["id"]),
                matcher=(
                    UndergroundMinimapMatcher(load_image(image_path), level_config)
                    if item.get("template_fallback", False)
                    else MinimapMatcher(load_image(image_path), level_config)
                ),
                local_to_canonical=matrix,
                resolution_scale=float(item.get("resolution_scale", 1.0)),
                map_layer_id=map_layer_id,
                coordinate_space=coordinate_space,
                display_name=str(metadata.get("name") or ""),
                floor_label=str(metadata.get("label") or ""),
            )
        )
    return PyramidMatcher((width, height), levels, region_id=region_id)
