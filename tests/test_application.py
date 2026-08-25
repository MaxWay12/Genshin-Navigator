from __future__ import annotations

import unittest
from dataclasses import replace

from genshin_navigator.application import build_locator, load_runtime_data
from genshin_navigator.config import AppConfig, PoiConfig, Roi


class ApplicationCompositionTests(unittest.TestCase):
    @staticmethod
    def config() -> AppConfig:
        return AppConfig(None, None, None, Roi(0, 0, 16, 16))

    def test_disabled_poi_does_not_open_runtime_storage(self) -> None:
        config = replace(self.config(), poi=PoiConfig(enabled=False))

        self.assertIsNone(load_runtime_data(config))

    def test_locator_requires_map_or_pyramid(self) -> None:
        config = replace(self.config(), map_path=None, pyramid_path=None)

        with self.assertRaisesRegex(ValueError, "map_path or pyramid_path"):
            build_locator(config)


if __name__ == "__main__":
    unittest.main()
