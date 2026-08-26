from __future__ import annotations

import cv2
import numpy as np

from .config import MotionFallbackConfig
from .matcher import LocateResult
from .position import CoordinateSpace, MapPosition


class RelativeMotionLocalizer:
    """Bridge short SIFT gaps from robust frame-to-frame minimap translation."""

    def __init__(self, config: MotionFallbackConfig, region_id: str) -> None:
        self.config = config
        self.region_id = region_id
        self._previous_gray: np.ndarray | None = None
        self._consecutive_frames = 0
        self._armed = False

    @staticmethod
    def _gray(minimap: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(minimap, cv2.COLOR_BGR2GRAY)

    @staticmethod
    def _mask(shape: tuple[int, int]) -> np.ndarray:
        height, width = shape
        center = (width // 2, height // 2)
        mask = np.zeros((height, width), np.uint8)
        cv2.circle(mask, center, int(min(width, height) * 0.39), 255, -1)
        cv2.circle(mask, center, int(min(width, height) * 0.12), 0, -1)
        return mask

    def observe(self, minimap: np.ndarray) -> None:
        self._previous_gray = self._gray(minimap)
        self._consecutive_frames = 0
        self._armed = True

    def reset(self) -> None:
        self._previous_gray = None
        self._consecutive_frames = 0
        self._armed = False

    def _phase_translation(
        self, previous: np.ndarray, current: np.ndarray
    ) -> tuple[np.ndarray, float] | None:
        mask = self._mask(previous.shape).astype(np.float32) / 255.0
        window = cv2.createHanningWindow(
            (previous.shape[1], previous.shape[0]), cv2.CV_32F
        )
        first = previous.astype(np.float32) * mask
        second = current.astype(np.float32) * mask
        first -= float(first[mask > 0].mean()) * mask
        second -= float(second[mask > 0].mean()) * mask
        shift, response = cv2.phaseCorrelate(first, second, window * mask)
        vector = np.asarray(shift, dtype=np.float64)
        if (
            response < self.config.min_phase_response
            or float(np.linalg.norm(vector)) > self.config.max_screen_step_px
        ):
            return None
        return vector, float(response)

    def _result(
        self,
        position: MapPosition,
        canonical_scale: float,
        vector: np.ndarray,
        matches: int,
        inliers: int,
        reason: str,
        confidence: float,
    ) -> LocateResult:
        self._consecutive_frames += 1
        x = position.x - canonical_scale * float(vector[0])
        y = position.y - canonical_scale * float(vector[1])
        return LocateResult(
            found=True,
            x_px=round(x, 2),
            y_px=round(y, 2),
            rotation_degrees=0.0,
            scale=round(canonical_scale, 4),
            canonical_scale=round(canonical_scale, 4),
            confidence=confidence,
            matches=matches,
            inliers=inliers,
            reason=reason,
            reference_id="relative_motion",
            map_layer_id=position.layer_id,
            match_method="motion",
            region_id=position.region_id,
            coordinate_space=position.coordinate_space,
        )

    def locate_near(
        self,
        minimap: np.ndarray,
        position: MapPosition,
        canonical_scale: float,
    ) -> LocateResult:
        current = self._gray(minimap)
        if not self._armed:
            return LocateResult(found=False, reason="motion_requires_absolute_fix")
        previous = self._previous_gray
        self._previous_gray = current
        if previous is None or previous.shape != current.shape:
            return LocateResult(found=False, reason="motion_history_unavailable")
        if (
            position.region_id != self.region_id
            or position.coordinate_space is not CoordinateSpace.SURFACE_ATLAS
        ):
            return LocateResult(found=False, reason="motion_namespace_mismatch")
        if self._consecutive_frames >= self.config.max_consecutive_frames:
            return LocateResult(found=False, reason="motion_budget_exhausted")

        points = cv2.goodFeaturesToTrack(
            previous,
            maxCorners=self.config.max_features,
            qualityLevel=0.015,
            minDistance=5,
            mask=self._mask(previous.shape),
            blockSize=5,
        )
        if points is None or len(points) < self.config.min_inliers:
            phase = self._phase_translation(previous, current)
            if phase is None:
                return LocateResult(found=False, reason="motion_not_enough_features")
            vector, response = phase
            return self._result(
                position,
                canonical_scale,
                vector,
                self.config.min_inliers,
                self.config.min_inliers,
                "relative_motion_phase",
                min(self.config.confidence, 0.55 + 0.1 * response),
            )
        forward, status_forward, _ = cv2.calcOpticalFlowPyrLK(
            previous, current, points, None, winSize=(21, 21), maxLevel=3
        )
        if forward is None or status_forward is None:
            return LocateResult(found=False, reason="motion_forward_flow_failed")
        backward, status_backward, _ = cv2.calcOpticalFlowPyrLK(
            current, previous, forward, None, winSize=(21, 21), maxLevel=3
        )
        if backward is None or status_backward is None:
            return LocateResult(found=False, reason="motion_backward_flow_failed")

        old = points.reshape(-1, 2)
        new = forward.reshape(-1, 2)
        returned = backward.reshape(-1, 2)
        valid = (
            status_forward.ravel().astype(bool)
            & status_backward.ravel().astype(bool)
            & (np.linalg.norm(returned - old, axis=1) <= self.config.forward_backward_error_px)
        )
        vectors = new[valid] - old[valid]
        if len(vectors) < self.config.min_inliers:
            phase = self._phase_translation(previous, current)
            if phase is None:
                return LocateResult(
                    found=False, reason="motion_not_enough_consistent_tracks"
                )
            vector, response = phase
            return self._result(
                position,
                canonical_scale,
                vector,
                self.config.min_inliers,
                self.config.min_inliers,
                "relative_motion_phase",
                min(self.config.confidence, 0.55 + 0.1 * response),
            )
        median = np.median(vectors, axis=0)
        residuals = np.linalg.norm(vectors - median, axis=1)
        inlier_vectors = vectors[residuals <= self.config.max_residual_px]
        if len(inlier_vectors) < self.config.min_inliers:
            phase = self._phase_translation(previous, current)
            if phase is None:
                return LocateResult(found=False, reason="motion_residual_too_high")
            vector, response = phase
            return self._result(
                position,
                canonical_scale,
                vector,
                self.config.min_inliers,
                self.config.min_inliers,
                "relative_motion_phase",
                min(self.config.confidence, 0.55 + 0.1 * response),
            )
        median = np.median(inlier_vectors, axis=0)
        screen_step = float(np.linalg.norm(median))
        if screen_step > self.config.max_screen_step_px:
            return LocateResult(found=False, reason="motion_step_too_large")
        phase = self._phase_translation(previous, current)
        if phase is None:
            return LocateResult(found=False, reason="motion_phase_unconfirmed")
        phase_vector, _ = phase
        if (
            float(np.linalg.norm(median - phase_vector))
            > self.config.max_phase_disagreement_px
        ):
            return LocateResult(found=False, reason="motion_estimators_disagree")

        count = len(inlier_vectors)
        return self._result(
            position,
            canonical_scale,
            median,
            len(vectors),
            count,
            "relative_motion_lk",
            self.config.confidence,
        )
