from __future__ import annotations

from dataclasses import asdict, dataclass
from math import atan2, degrees, hypot

import cv2
import numpy as np

from .position import CoordinateSpace

from .config import MatcherConfig


@dataclass(frozen=True)
class CandidateMatch:
    reference_id: str | None
    map_layer_id: str | None
    confidence: float
    matches: int
    inliers: int
    reason: str | None
    found: bool
    ambiguity_best_score: float | None = None
    ambiguity_second_score: float | None = None
    ambiguity_margin: float | None = None
    search_area: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class LocateResult:
    found: bool
    x_px: float | None = None
    y_px: float | None = None
    x_normalized: float | None = None
    y_normalized: float | None = None
    rotation_degrees: float | None = None
    scale: float | None = None
    confidence: float = 0.0
    matches: int = 0
    inliers: int = 0
    reason: str | None = None
    reference_id: str | None = None
    map_layer_id: str | None = None
    reference_resolution_scale: float | None = None
    reference_x_px: float | None = None
    reference_y_px: float | None = None
    canonical_scale: float | None = None
    match_method: str | None = None
    region_id: str | None = None
    coordinate_space: CoordinateSpace | None = None
    candidates: tuple[CandidateMatch, ...] = ()
    ambiguity_best_score: float | None = None
    ambiguity_second_score: float | None = None
    ambiguity_margin: float | None = None
    search_area: tuple[float, float, float, float] | None = None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        if self.coordinate_space is not None:
            result["coordinate_space"] = self.coordinate_space.value
        return result


class MinimapMatcher:
    """Locate a minimap crop on a reference map using local visual features."""

    def __init__(self, reference_map: np.ndarray, config: MatcherConfig | None = None):
        if reference_map is None or reference_map.size == 0:
            raise ValueError("Reference map is empty")
        self.config = config or MatcherConfig()
        self.reference_map = reference_map
        self._detector = cv2.SIFT_create(
            nfeatures=self.config.max_features,
            contrastThreshold=0.01,
            edgeThreshold=15,
            sigma=1.6,
        )
        map_gray = self._prepare(reference_map)
        self._map_keypoints, self._map_descriptors = self._detector.detectAndCompute(map_gray, None)
        if self._map_descriptors is None or len(self._map_keypoints) < self.config.min_matches:
            raise ValueError("Reference map does not contain enough visual features")

    @staticmethod
    def _prepare(image: np.ndarray) -> np.ndarray:
        if image.ndim == 2:
            gray = image
        else:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)

    @staticmethod
    def _minimap_mask(shape: tuple[int, int]) -> np.ndarray:
        height, width = shape
        radius = int(min(width, height) * 0.45)
        center = (width // 2, height // 2)
        mask = np.zeros((height, width), dtype=np.uint8)
        cv2.circle(mask, center, radius, 255, thickness=-1)
        # The player arrow is fixed at the center and must not influence matching.
        cv2.circle(mask, center, max(5, int(radius * 0.13)), 0, thickness=-1)
        return mask

    def locate(self, minimap: np.ndarray) -> LocateResult:
        return self._locate(
            minimap,
            self._map_keypoints,
            self._map_descriptors,
            ratio_threshold=self.config.ratio_threshold,
            min_matches=self.config.min_matches,
            min_inliers=self.config.min_inliers,
            confidence_inliers=20,
        )

    def locate_near(
        self,
        minimap: np.ndarray,
        center: tuple[float, float],
        radius_px: float,
        *,
        ratio_threshold: float,
        min_matches: int,
        min_inliers: int,
    ) -> LocateResult:
        """Match against reference features near a previously confirmed position."""
        center_x, center_y = center
        radius_squared = radius_px * radius_px
        indices = [
            index
            for index, keypoint in enumerate(self._map_keypoints)
            if (keypoint.pt[0] - center_x) ** 2 + (keypoint.pt[1] - center_y) ** 2
            <= radius_squared
        ]
        if len(indices) < max(2, min_matches):
            return LocateResult(found=False, reason="not_enough_local_reference_features")
        descriptors = self._map_descriptors[np.asarray(indices, dtype=np.int32)]
        keypoints = [self._map_keypoints[index] for index in indices]
        return self._locate(
            minimap,
            keypoints,
            descriptors,
            ratio_threshold=ratio_threshold,
            min_matches=min_matches,
            min_inliers=min_inliers,
            confidence_inliers=12,
        )

    def _locate(
        self,
        minimap: np.ndarray,
        map_keypoints: list[cv2.KeyPoint] | tuple[cv2.KeyPoint, ...],
        map_descriptors: np.ndarray,
        *,
        ratio_threshold: float,
        min_matches: int,
        min_inliers: int,
        confidence_inliers: int,
    ) -> LocateResult:
        if minimap is None or minimap.size == 0:
            return LocateResult(found=False, reason="minimap_is_empty")

        query_gray = self._prepare(minimap)
        query_mask = self._minimap_mask(query_gray.shape)
        query_keypoints, query_descriptors = self._detector.detectAndCompute(query_gray, query_mask)
        if query_descriptors is None or len(query_keypoints) < min_matches:
            return LocateResult(found=False, reason="not_enough_minimap_features")

        matcher = cv2.BFMatcher(cv2.NORM_L2)
        pairs = matcher.knnMatch(query_descriptors, map_descriptors, k=2)
        good = [a for pair in pairs if len(pair) == 2 for a, b in [pair] if a.distance < ratio_threshold * b.distance]
        if len(good) < min_matches:
            return LocateResult(
                found=False,
                matches=len(good),
                reason="not_enough_feature_matches",
            )

        query_points = np.float32([query_keypoints[m.queryIdx].pt for m in good])
        map_points = np.float32([map_keypoints[m.trainIdx].pt for m in good])
        transform, inlier_mask = cv2.estimateAffinePartial2D(
            query_points,
            map_points,
            method=cv2.RANSAC,
            ransacReprojThreshold=4.0,
            maxIters=3000,
            confidence=0.995,
            refineIters=20,
        )
        if transform is None or inlier_mask is None:
            return LocateResult(
                found=False,
                matches=len(good),
                reason="transform_not_found",
            )

        inliers = int(inlier_mask.sum())
        a, b = float(transform[0, 0]), float(transform[1, 0])
        scale = hypot(a, b)
        if inliers < min_inliers:
            return LocateResult(
                found=False,
                matches=len(good),
                inliers=inliers,
                reason="not_enough_inliers",
            )
        if not self.config.min_scale <= scale <= self.config.max_scale:
            return LocateResult(
                found=False,
                matches=len(good),
                inliers=inliers,
                scale=scale,
                reason="implausible_scale",
            )

        height, width = minimap.shape[:2]
        center = np.array([width / 2.0, height / 2.0, 1.0], dtype=np.float64)
        x_px, y_px = transform @ center
        map_height, map_width = self.reference_map.shape[:2]
        if not (0 <= x_px < map_width and 0 <= y_px < map_height):
            return LocateResult(
                found=False,
                matches=len(good),
                inliers=inliers,
                reason="position_outside_reference_map",
            )

        inlier_ratio = inliers / len(good)
        match_strength = min(1.0, inliers / max(confidence_inliers, min_inliers))
        confidence = round(inlier_ratio * match_strength, 4)
        return LocateResult(
            found=True,
            x_px=round(float(x_px), 2),
            y_px=round(float(y_px), 2),
            x_normalized=round(float(x_px / map_width), 6),
            y_normalized=round(float(y_px / map_height), 6),
            rotation_degrees=round(degrees(atan2(b, a)), 2),
            scale=round(scale, 4),
            confidence=confidence,
            matches=len(good),
            inliers=inliers,
        )


class UndergroundMinimapMatcher(MinimapMatcher):
    """SIFT matcher with an edge-correlation fallback for exact map overlays."""

    def __init__(self, reference_map: np.ndarray, config: MatcherConfig | None = None):
        super().__init__(reference_map, config)
        self._reference_gray = cv2.cvtColor(reference_map, cv2.COLOR_BGR2GRAY)

    def locate(self, minimap: np.ndarray) -> LocateResult:
        return self._locate(
            minimap,
            self._map_keypoints,
            self._map_descriptors,
            ratio_threshold=self.config.ratio_threshold,
            min_matches=self.config.min_matches,
            min_inliers=self.config.min_inliers,
            confidence_inliers=12,
        )

    def locate_fallback(self, minimap: np.ndarray) -> LocateResult:
        if minimap is None or minimap.size == 0:
            return LocateResult(found=False, reason="minimap_is_empty")
        tentative = self._locate(
            minimap,
            self._map_keypoints,
            self._map_descriptors,
            ratio_threshold=0.86,
            min_matches=5,
            min_inliers=3,
            confidence_inliers=12,
        )
        if (
            not tentative.found
            or tentative.x_px is None
            or tentative.y_px is None
            or tentative.scale is None
        ):
            return LocateResult(found=False, reason="template_fallback_has_no_hint")

        query_gray = cv2.cvtColor(minimap, cv2.COLOR_BGR2GRAY)
        query_mask = self._minimap_mask(query_gray.shape)
        best: tuple[float, float, tuple[int, int], tuple[int, int]] | None = None
        min_scale = max(0.35, tentative.scale - 0.1)
        max_scale = min(2.75, tentative.scale + 0.1)
        for scale in np.arange(min_scale, max_scale + 0.001, 0.025):
            width = max(8, round(query_gray.shape[1] * float(scale)))
            height = max(8, round(query_gray.shape[0] * float(scale)))
            if width > self._reference_gray.shape[1] or height > self._reference_gray.shape[0]:
                continue
            query = cv2.resize(query_gray, (width, height), interpolation=cv2.INTER_AREA)
            mask = cv2.resize(query_mask, (width, height), interpolation=cv2.INTER_NEAREST)
            predicted_left = round(tentative.x_px - width / 2)
            predicted_top = round(tentative.y_px - height / 2)
            radius = max(4, round(scale * 8))
            left = max(0, predicted_left - radius)
            top = max(0, predicted_top - radius)
            right = min(self._reference_gray.shape[1], predicted_left + width + radius)
            bottom = min(self._reference_gray.shape[0], predicted_top + height + radius)
            search = self._reference_gray[top:bottom, left:right]
            if search.shape[1] < width or search.shape[0] < height:
                continue
            scores = cv2.matchTemplate(
                search,
                query,
                cv2.TM_CCORR_NORMED,
                mask=mask,
            )
            _, score, _, location = cv2.minMaxLoc(scores)
            if best is None or score > best[0]:
                best = (
                    float(score),
                    float(scale),
                    (location[0] + left, location[1] + top),
                    (width, height),
                )

        if best is None:
            return LocateResult(found=False, reason="template_fallback_unavailable")
        score, scale, location, size = best
        if not np.isfinite(score) or score < 0.9:
            return LocateResult(
                found=False,
                confidence=max(0.0, round(score, 4)) if np.isfinite(score) else 0.0,
                reason="template_correlation_too_low",
            )
        x_px = location[0] + size[0] / 2.0
        y_px = location[1] + size[1] / 2.0
        map_height, map_width = self.reference_map.shape[:2]
        confidence = min(1.0, max(0.0, (score - 0.8) / 0.2))
        return LocateResult(
            found=True,
            x_px=round(x_px, 2),
            y_px=round(y_px, 2),
            x_normalized=round(x_px / map_width, 6),
            y_normalized=round(y_px / map_height, 6),
            rotation_degrees=0.0,
            scale=round(scale, 4),
            confidence=round(confidence, 4),
            matches=tentative.matches,
            inliers=tentative.inliers,
            reason="template_fallback",
            match_method="template",
        )
