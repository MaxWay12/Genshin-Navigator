from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "fetch_hoyolab_atlas.py"
SPEC = importlib.util.spec_from_file_location("fetch_hoyolab_atlas", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HoyolabAtlasScriptTests(unittest.TestCase):
    def test_inclusive_tile_range(self) -> None:
        self.assertEqual(list(MODULE._range("-2:1")), [-2, -1, 0, 1])

    def test_invalid_tile_range(self) -> None:
        with self.assertRaises(ValueError):
            MODULE._range("3:2")

    def test_world_transform_keeps_existing_fontaine_coordinates(self) -> None:
        self.assertEqual(
            MODULE._world_to_atlas(
                zoom="N1",
                origin_x=24206,
                origin_y=8918,
                min_x=32,
                min_y=12,
                tile_size=256,
            ),
            [
                [0.5, 0.0, 3911.0],
                [0.0, 0.5, 1387.0],
                [0.0, 0.0, 1.0],
            ],
        )


if __name__ == "__main__":
    unittest.main()
