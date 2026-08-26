from __future__ import annotations

from dataclasses import dataclass
from math import hypot

import cv2
import numpy as np

from .config import EdgeCorrelationConfig
from .matcher import LocateResult, MinimapMatcher
from .position import CoordinateSpace


@dataclass(frozen=True)
class _Peak:
    score: float
    x: float
    y: float
    scale: float
    rotation: float


class EdgeCorrelationLocalizer:
    """Find sparse surface minimaps by a unique global edge-correlation peak."""

    def __init__(
        self,
        reference_map: np.ndarray,
        config: EdgeCorrelationConfig,
        region_id: str,
    ) -> None:
        if reference_map is None or reference_map.size == 0:
            raise ValueError("Edge correlation reference map is empty")
        self.config = config
        self.region_id = region_id
        self.reference_map = reference_map
        gray = self._gray(reference_map)
        self._reference_edges = cv2.Canny(
            gray, config.canny_low, config.canny_high
        )
        self._coarse_edges = cv2.resize(
            self._reference_edges,
            None,
            fx=config.coarse_scale,
            fy=config.coarse_scale,
            interpolation=cv2.INTER_AREA,
        )

    @staticmethod
    def _gray(image: np.ndarray) -> np.ndarray:
        if image.ndim == 2:
            return image
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    @staticmethod
    def _rotate(image: np.ndarray, angle: float, interpolation: int) -> np.ndarray:
        height, width = image.shape[:2]
        matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle, 1.0)
        return cv2.warpAffine(
            image,
            matrix,
            (width, height),
            flags=interpolation,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )

    @staticmethod
    def _top_peaks(
        scores: np.ndarray,
        template_size: tuple[int, int],
        scale: float,
        rotation: float,
        exclusion_radius: float,
        count: int = 2,
    ) -> list[_Peak]:
        finite = np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0)
        peaks: list[_Peak] = []
        work = finite.copy()
        width, height = template_size
        for _ in range(count):
            _, score, _, location = cv2.minMaxLoc(work)
            if score < 0:
                break
            x = location[0] + width / 2.0
            y = location[1] + height / 2.0
            peaks.append(_Peak(float(score), x, y, scale, rotation))
            cv2.circle(
                work,
                location,
                max(1, round(exclusion_radius)),
                -1.0,
                thickness=-1,
            )
        return peaks

    def _refine_peak(
        self,
        peak: _Peak,
        query: np.ndarray,
        mask: np.ndarray,
    ) -> _Peak | None:
        reference_height, reference_width = self._reference_edges.shape
        coarse_height, coarse_width = self._coarse_edges.shape
        scale_x = reference_width / coarse_width
        scale_y = reference_height / coarse_height
        predicted_x = peak.x * scale_x
        predicted_y = peak.y * scale_y
        query_height, query_width = query.shape
        radius = self.config.refine_radius_px
        left = max(0, round(predicted_x - query_width / 2.0 - radius))
        top = max(0, round(predicted_y - query_height / 2.0 - radius))
        right = min(
            reference_width, round(predicted_x + query_width / 2.0 + radius)
        )
        bottom = min(
            reference_height, round(predicted_y + query_height / 2.0 + radius)
        )
        search = self._reference_edges[top:bottom, left:right]
        if search.shape[0] < query_height or search.shape[1] < query_width:
            return None
        scores = cv2.matchTemplate(
            search, query, cv2.TM_CCORR_NORMED, mask=mask
        )
        finite = np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, score, _, location = cv2.minMaxLoc(finite)
        return _Peak(
            float(score),
            left + location[0] + query_width / 2.0,
            top + location[1] + query_height / 2.0,
            peak.scale,
            peak.rotation,
        )

    def locate(self, minimap: np.ndarray) -> LocateResult:
        if minimap is None or minimap.size == 0:
            return LocateResult(found=False, reason="minimap_is_empty")
        gray = self._gray(minimap)
        edges = cv2.Canny(gray, self.config.canny_low, self.config.canny_high)
        base_mask = MinimapMatcher._minimap_mask(gray.shape)
        if cv2.countNonZero(edges & base_mask) < 30:
            return LocateResult(found=False, reason="edge_correlation_too_sparse")

        peaks: list[_Peak] = []
        reference_height, reference_width = self._reference_edges.shape
        for scale in self.config.scales:
            width = max(8, round(edges.shape[1] * scale))
            height = max(8, round(edges.shape[0] * scale))
            if width >= reference_width or height >= reference_height:
                continue
            query = cv2.resize(edges, (width, height), interpolation=cv2.INTER_AREA)
            mask = cv2.resize(
                base_mask, (width, height), interpolation=cv2.INTER_NEAREST
            )
            for rotation in self.config.rotations_degrees:
                rotated_query = self._rotate(query, rotation, cv2.INTER_LINEAR)
                rotated_mask = self._rotate(mask, rotation, cv2.INTER_NEAREST)
                coarse_query = cv2.resize(
                    rotated_query,
                    None,
                    fx=self.config.coarse_scale,
                    fy=self.config.coarse_scale,
                    interpolation=cv2.INTER_AREA,
                )
                coarse_mask = cv2.resize(
                    rotated_mask,
                    (coarse_query.shape[1], coarse_query.shape[0]),
                    interpolation=cv2.INTER_NEAREST,
                )
                scores = cv2.matchTemplate(
                    self._coarse_edges,
                    coarse_query,
                    cv2.TM_CCORR_NORMED,
                    mask=coarse_mask,
                )
                coarse_peaks = self._top_peaks(
                    scores,
                    (coarse_query.shape[1], coarse_query.shape[0]),
                    scale,
                    rotation,
                    self.config.exclusion_radius_px * self.config.coarse_scale,
                    self.config.coarse_candidates,
                )
                for coarse_peak in coarse_peaks:
                    refined = self._refine_peak(
                        coarse_peak, rotated_query, rotated_mask
                    )
                    if refined is not None:
                        peaks.append(refined)

        if not peaks:
            return LocateResult(found=False, reason="edge_correlation_unavailable")
        peaks.sort(key=lambda item: item.score, reverse=True)
        best = peaks[0]
        distinct = next(
            (
                item
                for item in peaks[1:]
                if hypot(item.x - best.x, item.y - best.y)
                > self.config.exclusion_radius_px
            ),
            None,
        )
        second_score = distinct.score if distinct is not None else 0.0
        margin = best.score - second_score
        evidence = {
            "ambiguity_best_score": round(max(0.0, best.score), 4),
            "ambiguity_second_score": round(max(0.0, second_score), 4),
            "ambiguity_margin": round(margin, 4),
            "search_area": (0.0, 0.0, float(reference_width), float(reference_height)),
        }
        if best.score < self.config.min_score:
            return LocateResult(
                found=False,
                confidence=round(max(0.0, best.score), 4),
                reason="edge_correlation_score_too_low",
                match_method="edge_correlation",
                region_id=self.region_id,
                coordinate_space=CoordinateSpace.SURFACE_ATLAS,
                **evidence,
            )
        if margin < self.config.min_peak_margin:
            return LocateResult(
                found=False,
                confidence=round(max(0.0, best.score), 4),
                reason="edge_correlation_ambiguous",
                match_method="edge_correlation",
                region_id=self.region_id,
                coordinate_space=CoordinateSpace.SURFACE_ATLAS,
                **evidence,
            )

        support = max(8, round(best.score * 30))
        confidence = min(
            self.config.confidence,
            0.35
            + 0.17
            * min(1.0, (best.score - self.config.min_score) / max(0.01, 1 - self.config.min_score)),
        )
        return LocateResult(
            found=True,
            x_px=round(best.x, 2),
            y_px=round(best.y, 2),
            x_normalized=round(best.x / reference_width, 6),
            y_normalized=round(best.y / reference_height, 6),
            rotation_degrees=round(best.rotation, 2),
            scale=round(best.scale, 4),
            canonical_scale=round(best.scale, 4),
            confidence=round(confidence, 4),
            matches=support,
            inliers=support,
            reason="edge_correlation_unique_peak",
            reference_id="edge_correlation",
            map_layer_id="surface",
            match_method="edge_correlation",
            region_id=self.region_id,
            coordinate_space=CoordinateSpace.SURFACE_ATLAS,
            **evidence,
        )
