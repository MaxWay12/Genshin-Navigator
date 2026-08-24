from __future__ import annotations

import unittest

from genshin_navigator.underground_pyramid import compose_local_to_canonical


class UndergroundTransformTests(unittest.TestCase):
    def test_composes_overlay_and_surface_transforms(self) -> None:
        result = compose_local_to_canonical(
            [[0.5, 0, 10], [0, -0.5, 20], [0, 0, 1]],
            [[2, 0, -4], [0, -3, 8], [0, 0, 1]],
        )
        self.assertEqual(result, [[1.0, 0.0, 8.0], [0.0, -1.5, 24.0], [0.0, 0.0, 1.0]])


if __name__ == "__main__":
    unittest.main()
