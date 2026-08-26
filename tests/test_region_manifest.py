from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from genshin_navigator.region_manifest import load_region_manifest


class RegionManifestTests(unittest.TestCase):
    def test_loads_relative_region_configs_and_support_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "regions.json"
            path.write_text(
                json.dumps(
                    {
                        "format_version": 1,
                        "regions": [
                            {
                                "id": "fontaine",
                                "display_name": "Fontaine",
                                "support": "supported",
                                "config": "config.fontaine.json",
                            },
                            {
                                "id": "sumeru_desert",
                                "display_name": "Sumeru (Experimental)",
                                "support": "experimental",
                                "config": "config.sumeru.json",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            manifest = load_region_manifest(path)

            self.assertEqual(manifest.get("fontaine").support, "supported")
            self.assertEqual(
                manifest.get("sumeru_desert").config_path,
                (root / "config.sumeru.json").resolve(),
            )

    def test_rejects_unknown_product_region(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "regions.json"
            path.write_text(
                json.dumps(
                    {
                        "format_version": 1,
                        "regions": [
                            {
                                "id": "fontaine",
                                "support": "supported",
                                "config": "fontaine.json",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Unknown product region"):
                load_region_manifest(path).get("natlan")


if __name__ == "__main__":
    unittest.main()
