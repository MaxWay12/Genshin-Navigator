import hashlib
import io
import json
import shutil
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from genshin_navigator.updates import ReleaseInfo, ReleaseProvider, UpdateService, safe_members, version_key

ROOT = Path(__file__).resolve().parents[1]


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old = self.root / "Старая версия"
        self.old.mkdir()
        config = json.loads((ROOT / "config.example.json").read_text())
        for key in ("map_path", "debug_map_path", "pyramid_path"):
            config[key] = None
        atlas = self.old / "datasets/local/references/atlas.png"
        atlas.parent.mkdir(parents=True)
        from PIL import Image
        Image.new("RGB", (32, 32)).save(atlas)
        config["map_path"] = str(atlas)
        (self.old / "config.json").write_text(json.dumps(config))
        self.dest = self.root / "Новая версия"
        self.dest.mkdir()
        self.buffer = io.BytesIO()
        with zipfile.ZipFile(self.buffer, "w") as archive:
            archive.writestr("package/GenshinNavigator.exe", b"MZ-test")
            archive.write(ROOT / "release/regions.portable.json", "package/regions.json")
            for name in ("config.example.json", "config.sumeru.example.json", "config.sumeru-full.example.json"):
                archive.write(ROOT / name, "package/" + name)
        self.data = self.buffer.getvalue()
        base = "https://github.com/MaxWay12/Genshin-Navigator/releases/download/v0.1.5-alpha/"
        self.release = ReleaseInfo("v0.1.5-alpha", "Notes", "GenshinNavigator-v0.1.5-alpha-windows-x64.zip", base + "package.zip", base + "package.zip.sha256", len(self.data))
        self.checksum = (hashlib.sha256(self.data).hexdigest() + "  " + self.release.archive_name + "\n").encode()
        self.service = UpdateService(self.old, opener=self.open)

    def tearDown(self):
        self.temp.cleanup()

    def open(self, request, **kwargs):
        return io.BytesIO(self.checksum if request.full_url.endswith(".sha256") else self.data)

    def test_atomic_side_by_side_transfer(self):
        original = (self.old / "config.json").read_bytes()
        self.assertFalse(self.service.preview(self.release, self.dest)["login_transferred"])
        self.assertFalse(any(self.dest.iterdir()))
        result = self.service.apply(self.release, self.dest, stopped=True)
        self.assertTrue(result["ready"])
        expected = json.loads(original)
        expected["map_path"] = str(self.dest / "datasets/local/references/atlas.png")
        self.assertEqual(json.loads((self.dest / "config.json").read_text(encoding="utf-8")), expected)
        self.assertEqual((self.old / "config.json").read_bytes(), original)
        self.assertTrue((self.dest / "GenshinNavigator.exe").is_file())

    def test_wrong_checksum_and_cancel_leave_destination_empty(self):
        self.checksum = b"0" * 64 + b"  " + self.release.archive_name.encode()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.service.apply(self.release, self.dest, stopped=True)
        self.assertFalse(any(self.dest.iterdir()))
        self.service.cancelled.set()
        with self.assertRaises(InterruptedError):
            self.service.apply(self.release, self.dest, stopped=True)
        self.assertFalse(any(self.dest.iterdir()))

    def test_conflict_disk_space_network_and_interrupted_transfer(self):
        (self.dest / "keep").write_text("keep")
        with self.assertRaises(ValueError):
            self.service.preview(self.release, self.dest)
        (self.dest / "keep").unlink()
        with patch("genshin_navigator.updates.shutil.disk_usage", return_value=Mock(free=0)):
            with self.assertRaises(ValueError):
                self.service.preview(self.release, self.dest)
        self.service.opener = Mock(side_effect=TimeoutError("offline"))
        with self.assertRaises(TimeoutError):
            self.service.apply(self.release, self.dest, stopped=True)
        self.assertFalse(any(self.dest.iterdir()))
        self.service.opener = self.open
        with patch("genshin_navigator.updates.PortableTransfer.apply", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.service.apply(self.release, self.dest, stopped=True)
        self.assertFalse(any(self.dest.iterdir()))

    def test_reject_zip_escape_ads_and_symlink(self):
        for name in ("../escape", "/root", "C:/escape", "a/../../escape", "a:stream", "NUL.txt", "a. "):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as archive:
                archive.writestr(name, b"bad")
            with zipfile.ZipFile(buf) as archive, self.assertRaises(ValueError):
                safe_members(archive)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "../outside")
        with zipfile.ZipFile(buf) as archive, self.assertRaises(ValueError):
            safe_members(archive)

    def test_release_check_keeps_alpha_excludes_draft_and_no_credentials(self):
        item = dict(tag_name=self.release.version, published_at="2026-09-05", draft=False, body="notes", assets=[
            dict(name=self.release.archive_name, size=len(self.data), browser_download_url=self.release.archive_url),
            dict(name=self.release.archive_name + ".sha256", browser_download_url=self.release.checksum_url)])
        opener = Mock(side_effect=lambda *a, **k: io.BytesIO(json.dumps([item, dict(item, draft=True, tag_name="v9.0.0")]).encode()))
        found = ReleaseProvider(opener=opener).newer_release("0.1.4a1")
        self.assertEqual(found.version, "v0.1.5-alpha")
        self.assertNotIn("Authorization", opener.call_args.args[0].headers)
        self.assertEqual(version_key("0.1.4a1"), version_key("v0.1.4-alpha"))
        self.assertGreater(version_key("0.1.4"), version_key("0.1.4a1"))
