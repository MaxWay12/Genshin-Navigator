from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

from .config import AnchorLocalizationConfig
from .matcher import LocateResult
from .position import CoordinateSpace, MapPosition


@dataclass(frozen=True)
class MapAnchor:
    id: str
    kind: str
    x: float
    y: float
    layer_id: str = "surface"


@dataclass(frozen=True)
class ObservedAnchor:
    kind: str
    x: float
    y: float
    score: float


class TemplateAnchorDetector:
    """Detect official map symbols without inspecting the game process."""

    def __init__(
        self,
        template_paths: dict[str, Path],
        *,
        min_score: float,
        scales: Iterable[float] = (0.38, 0.44, 0.50, 0.56, 0.62, 0.68),
    ) -> None:
        self.min_score = min_score
        self.scales = tuple(float(scale) for scale in scales)
        self.templates: list[tuple[str, np.ndarray]] = []
        for kind, path in template_paths.items():
            image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if image is None:
                raise FileNotFoundError(f"Could not load anchor template: {path}")
            if image.ndim != 3 or image.shape[2] not in (3, 4):
                raise ValueError(f"Anchor template must be RGB/RGBA: {path}")
            if image.shape[2] == 4:
                alpha = image[:, :, 3]
                bgr = image[:, :, :3]
            else:
                bgr = image
                alpha = np.full(image.shape[:2], 255, np.uint8)
            ys, xs = np.where(alpha > 32)
            if not len(xs):
                raise ValueError(f"Anchor template has no visible pixels: {path}")
            left, right = int(xs.min()), int(xs.max()) + 1
            top, bottom = int(ys.min()), int(ys.max()) + 1
            bgr = bgr[top:bottom, left:right]
            alpha = alpha[top:bottom, left:right]
            composited = cv2.bitwise_and(bgr, bgr, mask=alpha)
            gray = cv2.cvtColor(composited, cv2.COLOR_BGR2GRAY)
            for scale in self.scales:
                resized = cv2.resize(
                    gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
                )
                edges = cv2.Canny(resized, 45, 140)
                if edges.shape[0] >= 8 and edges.shape[1] >= 8:
                    self.templates.append((kind, edges))

    @staticmethod
    def _inside_minimap(x: float, y: float, width: int, height: int) -> bool:
        cx, cy = width / 2.0, height / 2.0
        distance = float(np.hypot(x - cx, y - cy))
        return 16.0 < distance < min(width, height) * 0.43

    def detect(self, minimap: np.ndarray) -> list[ObservedAnchor]:
        if minimap is None or minimap.size == 0:
            return []
        height, width = minimap.shape[:2]
        raw: list[ObservedAnchor] = []
        gray = cv2.cvtColor(minimap, cv2.COLOR_BGR2GRAY)
        minimap_edges = cv2.Canny(gray, 45, 140)
        for kind, template in self.templates:
            if template.shape[0] >= height or template.shape[1] >= width:
                continue
            scores = cv2.matchTemplate(
                minimap_edges, template, cv2.TM_CCOEFF_NORMED
            )
            # Only a handful of symbols can be visible. Extracting bounded local
            # maxima avoids materializing thousands of threshold coordinates.
            for _ in range(4):
                _, score, _, location = cv2.minMaxLoc(scores)
                if score < self.min_score:
                    break
                left, top = location
                center_x = left + template.shape[1] / 2.0
                center_y = top + template.shape[0] / 2.0
                if self._inside_minimap(center_x, center_y, width, height):
                    raw.append(ObservedAnchor(kind, center_x, center_y, float(score)))
                radius = 10
                scores[
                    max(0, top - radius):min(scores.shape[0], top + radius + 1),
                    max(0, left - radius):min(scores.shape[1], left + radius + 1),
                ] = -1.0
        # One symbol is often detected at adjacent positions and scales. Keep the
        # strongest result irrespective of type so it cannot vote twice.
        selected: list[ObservedAnchor] = []
        for item in sorted(raw, key=lambda value: value.score, reverse=True):
            if any(np.hypot(item.x - old.x, item.y - old.y) < 13.0 for old in selected):
                continue
            selected.append(item)
        return selected[:8]


def load_anchor_catalog(path: str | Path) -> tuple[str, tuple[int, int], list[MapAnchor]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if int(payload.get("format_version", 0)) != 1:
        raise ValueError("Unsupported anchor catalog format")
    anchors = [
        MapAnchor(
            id=str(item["id"]),
            kind=str(item["kind"]),
            x=float(item["x"]),
            y=float(item["y"]),
            layer_id=str(item.get("layer_id", "surface")),
        )
        for item in payload.get("anchors", [])
    ]
    if not anchors:
        raise ValueError("Anchor catalog is empty")
    width, height = map(int, payload["canonical_size"])
    return str(payload["region_id"]), (width, height), anchors


class AnchorLocalizer:
    """Resolve semantic anchors globally or relative to a confirmed track."""

    def __init__(
        self,
        region_id: str,
        canonical_size: tuple[int, int],
        anchors: list[MapAnchor],
        detector: TemplateAnchorDetector,
        config: AnchorLocalizationConfig,
    ) -> None:
        self.region_id = region_id
        self.canonical_size = canonical_size
        self.anchors = anchors
        self.detector = detector
        self.config = config
        self._canonical_scale = config.default_canonical_scale
        self._local_armed = False

    @classmethod
    def from_config(cls, config: AnchorLocalizationConfig) -> "AnchorLocalizer":
        assert config.catalog_path is not None
        region_id, size, anchors = load_anchor_catalog(config.catalog_path)
        detector = TemplateAnchorDetector(
            config.template_paths, min_score=config.min_template_score
        )
        return cls(region_id, size, anchors, detector, config)

    def observe_primary(self, result: LocateResult) -> None:
        scale = result.canonical_scale or result.scale
        if result.found and scale is not None and 0.2 <= scale <= 5.0:
            self._canonical_scale = float(scale)
            self._local_armed = True

    def reset_continuity(self) -> None:
        self._local_armed = False

    def _result(
        self,
        positions: list[tuple[float, float]],
        observations: list[ObservedAnchor],
        *,
        local: bool,
    ) -> LocateResult:
        center = np.median(np.asarray(positions, dtype=np.float64), axis=0)
        count = len(positions)
        score = float(np.mean([item.score for item in observations]))
        # Edge-template correlation is deliberately conservative and its raw
        # range is not a probability. Geometry (unique nearby assignment and
        # residual checks) supplies most of the confidence for local fallback.
        score_span = max(0.05, 0.65 - self.config.min_template_score)
        quality = min(
            1.0,
            max(0.0, (score - self.config.min_template_score) / score_span),
        )
        confidence = 0.55 + 0.15 * quality + min(0.15, 0.05 * (count - 1))
        if not local:
            confidence = max(confidence, 0.72)
        width, height = self.canonical_size
        return LocateResult(
            found=True,
            x_px=round(float(center[0]), 2),
            y_px=round(float(center[1]), 2),
            x_normalized=round(float(center[0] / width), 6),
            y_normalized=round(float(center[1] / height), 6),
            rotation_degrees=0.0,
            scale=round(self._canonical_scale, 4),
            canonical_scale=round(self._canonical_scale, 4),
            confidence=round(confidence, 4),
            matches=count,
            inliers=count,
            reason="anchor_local" if local else "anchor_global",
            reference_id="semantic_anchors",
            map_layer_id="surface",
            match_method="anchors",
            region_id=self.region_id,
            coordinate_space=CoordinateSpace.SURFACE_ATLAS,
        )

    def locate_near(self, minimap: np.ndarray, hint: MapPosition) -> LocateResult:
        if not self._local_armed:
            return LocateResult(found=False, reason="anchor_requires_absolute_fix")
        if hint.region_id != self.region_id or hint.layer_id != "surface":
            return LocateResult(found=False, reason="anchor_namespace_mismatch")
        observed = self.detector.detect(minimap)
        if not observed:
            return LocateResult(found=False, reason="no_anchor_symbols")
        image_height, image_width = minimap.shape[:2]
        cx, cy = image_width / 2.0, image_height / 2.0
        used: set[str] = set()
        positions: list[tuple[float, float]] = []
        accepted_observations: list[ObservedAnchor] = []
        for item in observed:
            predicted_x = hint.x + self._canonical_scale * (item.x - cx)
            predicted_y = hint.y + self._canonical_scale * (item.y - cy)
            choices = sorted(
                (
                    (float(np.hypot(anchor.x - predicted_x, anchor.y - predicted_y)), anchor)
                    for anchor in self.anchors
                    if anchor.kind == item.kind and anchor.id not in used
                ),
                key=lambda pair: pair[0],
            )
            if not choices or choices[0][0] > self.config.local_match_radius_px:
                continue
            if len(choices) > 1 and choices[1][0] - choices[0][0] < 4.0:
                continue
            anchor = choices[0][1]
            used.add(anchor.id)
            positions.append(
                (
                    anchor.x - self._canonical_scale * (item.x - cx),
                    anchor.y - self._canonical_scale * (item.y - cy),
                )
            )
            accepted_observations.append(item)
        if not positions:
            return LocateResult(found=False, reason="no_local_anchor_assignment")
        candidate = np.median(np.asarray(positions), axis=0)
        delta_x = float(candidate[0] - hint.x)
        delta_y = float(candidate[1] - hint.y)
        distance_from_hint = float(np.hypot(delta_x, delta_y))
        if distance_from_hint > self.config.local_match_radius_px:
            return LocateResult(found=False, reason="anchor_position_outside_local_radius")
        if len(positions) > 1 and max(
            float(np.hypot(x - candidate[0], y - candidate[1])) for x, y in positions
        ) > self.config.max_residual_px:
            return LocateResult(found=False, reason="anchor_residual_too_high")
        return self._result(positions, accepted_observations, local=True)

    def locate(self, minimap: np.ndarray) -> LocateResult:
        observed = self.detector.detect(minimap)
        if len(observed) < self.config.min_global_anchors:
            return LocateResult(found=False, reason="not_enough_global_anchors")
        height, width = minimap.shape[:2]
        cx, cy = width / 2.0, height / 2.0
        votes: list[tuple[int, str, float, float]] = []
        for index, item in enumerate(observed):
            for anchor in self.anchors:
                if anchor.kind == item.kind:
                    votes.append(
                        (
                            index,
                            anchor.id,
                            anchor.x - self._canonical_scale * (item.x - cx),
                            anchor.y - self._canonical_scale * (item.y - cy),
                        )
                    )
        best: list[tuple[int, str, float, float]] = []
        for seed in votes:
            cluster: list[tuple[int, str, float, float]] = []
            used_observations: set[int] = set()
            used_anchors: set[str] = set()
            nearby = sorted(
                votes,
                key=lambda value: np.hypot(value[2] - seed[2], value[3] - seed[3]),
            )
            for vote in nearby:
                if vote[0] in used_observations or vote[1] in used_anchors:
                    continue
                if np.hypot(vote[2] - seed[2], vote[3] - seed[3]) > self.config.max_residual_px:
                    break
                cluster.append(vote)
                used_observations.add(vote[0])
                used_anchors.add(vote[1])
            if len(cluster) > len(best):
                best = cluster
        if len(best) < self.config.min_global_anchors:
            return LocateResult(found=False, reason="no_consistent_global_anchor_cluster")
        positions = [(item[2], item[3]) for item in best]
        used_observed = [observed[item[0]] for item in best]
        return self._result(positions, used_observed, local=False)
