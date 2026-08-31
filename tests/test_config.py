from pathlib import Path
import unittest

from genshin_navigator.config import (
    AppConfig,
    AlternativeHotkeyConfig,
    NavigationConfig,
    PerformanceConfig,
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

    def test_global_search_backoff_cap_cannot_be_below_initial_interval(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be below"):
            PerformanceConfig(
                global_search_interval_seconds=0.8,
                global_search_max_interval_seconds=0.5,
            )

    def test_alternative_hotkey_names_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown alternative"):
            AlternativeHotkeyConfig(bindings={"typo": 65})


if __name__ == "__main__":
    unittest.main()
