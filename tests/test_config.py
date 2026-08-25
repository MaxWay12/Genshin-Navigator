from pathlib import Path
import unittest

from genshin_navigator.config import (
    AppConfig,
    NavigationConfig,
    PoiGuidanceConfig,
    Roi,
)


class ConfigTests(unittest.TestCase):
    def test_guidance_and_navigation_hotkeys_cannot_overlap(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            AppConfig(
                map_path=Path("map.png"),
                pyramid_path=None,
                debug_map_path=Path("map.png"),
                roi=Roi(0, 0, 10, 10),
                navigation=NavigationConfig(),
                poi_guidance=PoiGuidanceConfig(toggle_details=0x64),
            )


if __name__ == "__main__":
    unittest.main()
