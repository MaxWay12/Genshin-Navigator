from __future__ import annotations

import unittest

import numpy as np

from genshin_navigator.matcher import LocateResult
from genshin_navigator.pyramid import PyramidLevel, PyramidMatcher
from genshin_navigator.position import CoordinateSpace, MapPosition, PositionState


class StubMatcher:
    def __init__(self, result: LocateResult):
        self.result = result

    def locate(self, minimap: np.ndarray) -> LocateResult:
        return self.result


class LocalStubMatcher(StubMatcher):
    def __init__(self, result: LocateResult):
        super().__init__(LocateResult(found=False, reason="global_not_expected"))
        self.local_result = result
        self.reference_map = np.zeros((100, 100, 3), dtype=np.uint8)

    def locate_near(self, minimap, center, radius_px, **kwargs):
        self.center = center
        self.radius_px = radius_px
        return self.local_result


class PyramidMatcherTests(unittest.TestCase):
    def test_selects_successful_level_and_returns_canonical_coordinates(self) -> None:
        base = PyramidLevel(
            id="base",
            matcher=StubMatcher(LocateResult(found=False, matches=3, reason="no_match")),
            local_to_canonical=np.eye(3),
        )
        detail = PyramidLevel(
            id="city_x2",
            matcher=StubMatcher(
                LocateResult(
                    found=True,
                    x_px=180.0,
                    y_px=360.0,
                    scale=0.56,
                    confidence=0.8,
                    matches=20,
                    inliers=16,
                )
            ),
            local_to_canonical=np.array(
                [[0.5, 0.0, 1120.0], [0.0, 0.5, 634.0], [0.0, 0.0, 1.0]]
            ),
            resolution_scale=2.0,
        )
        matcher = PyramidMatcher((3072, 2048), [base, detail])

        result = matcher.locate(np.zeros((216, 216, 3), dtype=np.uint8))

        self.assertTrue(result.found)
        self.assertEqual(result.reference_id, "city_x2")
        self.assertEqual(result.map_layer_id, "surface")
        self.assertEqual(result.reference_x_px, 180.0)
        self.assertEqual(result.reference_y_px, 360.0)
        self.assertAlmostEqual(result.x_px or 0, 1210.0)
        self.assertAlmostEqual(result.y_px or 0, 814.0)
        self.assertAlmostEqual(result.canonical_scale or 0, 0.28)
        self.assertEqual(result.coordinate_space, CoordinateSpace.SURFACE_ATLAS)

    def test_selects_candidate_with_stronger_geometric_evidence(self) -> None:
        weak = PyramidLevel(
            id="weak",
            matcher=StubMatcher(
                LocateResult(found=True, x_px=10, y_px=10, confidence=0.4, inliers=10)
            ),
            local_to_canonical=np.eye(3),
        )
        strong = PyramidLevel(
            id="strong",
            matcher=StubMatcher(
                LocateResult(found=True, x_px=20, y_px=20, confidence=0.9, inliers=8)
            ),
            local_to_canonical=np.eye(3),
        )

        result = PyramidMatcher((100, 100), [weak, strong]).locate(
            np.zeros((20, 20, 3), dtype=np.uint8)
        )

        self.assertEqual(result.reference_id, "strong")
        self.assertEqual(result.x_px, 20.0)

    def test_stops_after_nearly_certain_candidate(self) -> None:
        certain = PyramidLevel(
            id="certain",
            matcher=StubMatcher(
                LocateResult(
                    found=True,
                    x_px=30,
                    y_px=40,
                    confidence=0.99,
                    matches=25,
                    inliers=24,
                )
            ),
            local_to_canonical=np.eye(3),
        )

        class MustNotRun:
            def locate(self, minimap: np.ndarray) -> LocateResult:
                raise AssertionError("later pyramid level should not run")

        later = PyramidLevel(
            id="later",
            matcher=MustNotRun(),
            local_to_canonical=np.eye(3),
        )

        result = PyramidMatcher((100, 100), [certain, later]).locate(
            np.zeros((20, 20, 3), dtype=np.uint8)
        )

        self.assertEqual(result.reference_id, "certain")

    def test_underground_search_uses_layer_local_coordinates(self) -> None:
        from genshin_navigator.config import LocalSearchConfig

        local = LocalStubMatcher(
            LocateResult(found=True, x_px=22, y_px=29, confidence=0.8, matches=12, inliers=10)
        )
        level = PyramidLevel(
            id="underground_a",
            matcher=local,
            local_to_canonical=np.array(
                [[0.5, 0.0, 10.0], [0.0, 0.5, 20.0], [0.0, 0.0, 1.0]]
            ),
            map_layer_id="underground",
            coordinate_space=CoordinateSpace.LAYER_LOCAL,
        )
        matcher = PyramidMatcher((100, 100), [level], region_id="fontaine")
        hint = MapPosition(
            region_id="fontaine",
            layer_id="underground",
            coordinate_space=CoordinateSpace.LAYER_LOCAL,
            x=20,
            y=30,
            confidence=0.9,
            state=PositionState.TRACKING,
            timestamp=1.0,
        )

        result = matcher.locate_near(
            np.zeros((20, 20, 3), dtype=np.uint8),
            hint,
            LocalSearchConfig(radius_px=10),
        )

        self.assertTrue(result.found)
        self.assertEqual(result.map_layer_id, "underground")
        self.assertAlmostEqual(local.center[0], 20)
        self.assertAlmostEqual(local.center[1], 30)
        self.assertAlmostEqual(local.radius_px, 10)
        self.assertEqual(result.x_px, 22.0)
        self.assertEqual(result.y_px, 29.0)
        self.assertEqual(result.coordinate_space, CoordinateSpace.LAYER_LOCAL)
        self.assertEqual(result.region_id, "fontaine")

    def test_underground_global_result_ignores_surface_projection(self) -> None:
        local = LocalStubMatcher(
            LocateResult(found=True, x_px=42, y_px=73, confidence=0.9, inliers=12)
        )
        level = PyramidLevel(
            id="floor_a",
            matcher=local,
            local_to_canonical=np.array(
                [[0.5, 0, 1200], [0, -0.5, 300], [0, 0, 1]], dtype=np.float64
            ),
            map_layer_id="underground:floor_a",
            coordinate_space=CoordinateSpace.LAYER_LOCAL,
        )

        result = PyramidMatcher(
            (3072, 3072), [level], region_id="fontaine"
        )._to_position(local.local_result, level)

        self.assertEqual(result.x_px, 42)
        self.assertEqual(result.y_px, 73)
        self.assertEqual(result.coordinate_space, CoordinateSpace.LAYER_LOCAL)


if __name__ == "__main__":
    unittest.main()
