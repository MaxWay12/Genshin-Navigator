from __future__ import annotations

import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from genshin_navigator import asset_setup


class AssetSetupTests(unittest.TestCase):
    def _config(self, root: Path):
        references = root / "datasets" / "local" / "references"
        poi = root / "datasets" / "local" / "poi"
        return SimpleNamespace(
            data=SimpleNamespace(
                surface_metadata_path=references / "placeholder" / "metadata.json",
                lang="ru-ru",
            ),
            poi=SimpleNamespace(catalog_path=poi / "sumeru-desert.json"),
        )

    @staticmethod
    def _download_surface(output: Path, preset) -> dict[str, object]:
        output.mkdir(parents=True, exist_ok=True)
        (output / "atlas.png").write_bytes(b"synthetic atlas")
        (output / "surface_pyramid.json").write_text(
            json.dumps({"levels": []}), encoding="utf-8"
        )
        metadata = {
            "atlas_size": [100, 100],
            "world_to_atlas": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "tile_count": 3,
        }
        (output / "metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )
        return metadata

    @staticmethod
    def _write_anchors(output: Path, icons: Path, *args, **kwargs) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        icons.mkdir(parents=True, exist_ok=True)
        output.write_text('{"anchors": []}', encoding="utf-8")

    @staticmethod
    def _write_catalog(path: Path, *args, **kwargs) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"pois": []}', encoding="utf-8")

    def _setup_patches(self):
        return (
            patch.object(asset_setup, "_download_surface", self._download_surface),
            patch.object(asset_setup, "fetch_labels", return_value=[]),
            patch.object(asset_setup, "fetch_points", return_value=[]),
            patch.object(asset_setup, "_write_anchors", self._write_anchors),
            patch.object(asset_setup, "build_catalog", return_value=([], {})),
            patch.object(asset_setup, "build_space_metrics", return_value=[]),
            patch.object(asset_setup, "content_version_for", return_value="test-v1"),
            patch.object(asset_setup, "write_catalog", self._write_catalog),
        )

    def test_first_run_installs_and_second_run_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self._config(root)
            patches = self._setup_patches()
            for item in patches:
                item.start()
            try:
                first = asset_setup.setup_region(config, "sumeru_desert")
                second = asset_setup.setup_region(config, "sumeru_desert")
            finally:
                for item in reversed(patches):
                    item.stop()

            self.assertEqual(first["status"], "installed")
            self.assertEqual(second["status"], "already_ready")
            references = root / "datasets" / "local" / "references"
            self.assertTrue(
                (references / "hoyolab_sumeru_desert_n1" / "atlas.png").is_file()
            )
            self.assertTrue(
                (references / "sumeru_semantic_anchors" / "anchors.json").is_file()
            )

    def test_failed_download_preserves_previous_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self._config(root)
            surface = (
                root
                / "datasets"
                / "local"
                / "references"
                / "hoyolab_sumeru_desert_n1"
            )
            surface.mkdir(parents=True)
            (surface / "atlas.png").write_bytes(b"known-good")

            with patch.object(
                asset_setup, "_download_surface", side_effect=OSError("network down")
            ):
                with self.assertRaisesRegex(OSError, "network down"):
                    asset_setup.setup_region(config, "sumeru_desert", force=True)

            self.assertEqual((surface / "atlas.png").read_bytes(), b"known-good")

    def test_atomic_promotion_rolls_back_all_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_staged = root / "staged-first"
            second_staged = root / "staged-second"
            first_destination = root / "first"
            second_destination = root / "second"
            first_staged.write_text("new-first", encoding="utf-8")
            second_staged.write_text("new-second", encoding="utf-8")
            first_destination.write_text("old-first", encoding="utf-8")
            second_destination.write_text("old-second", encoding="utf-8")
            original_replace = os.replace

            def fail_second(source, destination):
                if Path(source) == second_staged and Path(destination) == second_destination:
                    raise OSError("promotion interrupted")
                return original_replace(source, destination)

            with patch.object(asset_setup.os, "replace", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "promotion interrupted"):
                    asset_setup._promote_assets(
                        [
                            (first_staged, first_destination),
                            (second_staged, second_destination),
                        ]
                    )

            self.assertEqual(first_destination.read_text(encoding="utf-8"), "old-first")
            self.assertEqual(second_destination.read_text(encoding="utf-8"), "old-second")

    def test_verified_tile_cache_resumes_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "local"
            first_output = root / ".setup-a" / "references" / "surface"
            second_output = root / ".setup-b" / "references" / "surface"
            preset = asset_setup.RegionPreset(
                "test", 1, range(1, 2), range(2, 3), "surface", "poi.json", "level"
            )
            encoded = BytesIO()
            Image.new("RGB", (256, 256), (20, 30, 40)).save(encoded, format="WEBP")
            with patch.object(asset_setup, "_request_bytes", return_value=encoded.getvalue()) as request:
                asset_setup._download_surface(first_output, preset)
            self.assertEqual(request.call_count, 1)
            with patch.object(asset_setup, "_request_bytes", side_effect=OSError("offline")) as request:
                asset_setup._download_surface(second_output, preset)
            self.assertEqual(request.call_count, 0)
            self.assertTrue((second_output / "atlas.png").is_file())


if __name__ == "__main__":
    unittest.main()
