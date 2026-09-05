import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from genshin_navigator.launcher import LauncherService, LauncherBridge, spawn_command, run_launcher
from genshin_navigator.portable_transfer import PortableTransfer

ROOT = Path(__file__).resolve().parents[1]


def installation(root):
    for name in ("config.example.json", "config.sumeru.example.json"):
        shutil.copy2(ROOT / name, root / name)
    shutil.copy2(ROOT / "release/regions.portable.json", root / "regions.json")


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        installation(self.root)
        self.service = LauncherService(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def values(self):
        return dict(mode="low_cpu", numpad=False, alternative=True, width=420, height=200, tray=True)

    def test_read_does_not_create_user_state(self):
        with patch("genshin_navigator.roi_setup.check_config_roi", return_value=Mock(to_dict=lambda: {"valid": True})):
            self.service.read("fontaine")
        self.assertFalse((self.root / "config.json").exists())
        self.assertFalse((self.root / "datasets").exists())

    def test_save_preserves_unknown_fields_and_invalid_save_is_atomic(self):
        path = self.service.ensure_config("fontaine")
        raw = json.loads(path.read_text()); raw["future_extension"] = {"keep": 3}
        path.write_text(json.dumps(raw))
        self.service.save("fontaine", self.values())
        saved = path.read_bytes()
        self.assertEqual(json.loads(saved)["future_extension"], {"keep": 3})
        self.assertFalse(json.loads(saved)["navigation"]["numpad_enabled"])
        with self.assertRaises(ValueError):
            self.service.save("fontaine", dict(self.values(), mode="invalid"))
        self.assertEqual(saved, path.read_bytes())

    def test_start_closes_window_before_spawn(self):
        bridge = LauncherBridge(self.service); bridge._window = Mock()
        with patch("genshin_navigator.asset_setup.region_asset_status", return_value={"ready": True}), patch("genshin_navigator.roi_setup.check_config_roi", return_value=Mock(valid=True)), patch("genshin_navigator.launcher.spawn_command") as spawn:
            bridge.start("sumeru_desert", self.values())
            bridge._window.destroy.assert_called_once()
            spawn.assert_not_called()
        self.assertEqual(bridge._launch_args[-1], str(self.root / "config.sumeru.json"))

    def test_spawn_uses_arguments_without_shell(self):
        with patch("genshin_navigator.launcher.subprocess.Popen") as popen:
            spawn_command(self.root, ["track", "--config", "a b.json"])
        self.assertNotIn("shell", popen.call_args.kwargs)
        self.assertEqual(popen.call_args.args[0][-1], "a b.json")

    def test_bridge_exposes_only_methods(self):
        bridge = LauncherBridge(self.service)
        bridge._window = Mock()
        self.assertFalse([key for key in vars(bridge) if not key.startswith("_")])

    def test_webview_failure_is_clear(self):
        with patch.dict("sys.modules", {"webview": None}), patch("sys.platform", "linux"):
            self.assertEqual(run_launcher(self.root), 1)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.source = base / "old"; self.dest = base / "new"
        self.source.mkdir(); self.dest.mkdir()
        installation(self.source); installation(self.dest)
        raw = json.loads((self.source / "config.example.json").read_text())
        for key in ("map_path", "debug_map_path", "pyramid_path"):
            path = self.source / raw[key]; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}" if path.suffix == ".json" else "test-image")
        raw["map_path"] = str(self.source / raw["map_path"])
        (self.source / "config.json").write_text(json.dumps(raw))
        self.db = self.source / "datasets/local/data/progress.db"; self.db.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript("CREATE TABLE progress (id TEXT, collected INTEGER, remote_ignore INTEGER); INSERT INTO progress VALUES ('hoyolab:1',1,0); INSERT INTO progress VALUES ('hoyolab:2',0,1);")
        for folder in ("auth", "backups", "scenarios"):
            path = self.source / "datasets/local" / folder; path.mkdir(); (path / "private.txt").write_text("private")
        self.transfer = PortableTransfer(self.dest)

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_no_write_and_transfer_keeps_progress_and_source(self):
        before = self.db.read_bytes()
        self.transfer.preview(self.source)
        self.assertFalse((self.dest / "datasets").exists())
        self.transfer.apply(self.source, stopped=True)
        with closing(sqlite3.connect(self.dest / "datasets/local/data/progress.db")) as db:
            self.assertEqual(db.execute("SELECT * FROM progress").fetchall(), [("hoyolab:1",1,0),("hoyolab:2",0,1)])
        self.assertEqual(self.db.read_bytes(), before)
        self.assertFalse((self.dest / "datasets/local/auth").exists())
        self.assertEqual(json.loads((self.dest / "config.json").read_text())["map_path"], str(self.dest / "datasets/local/references/hoyolab_fontaine_full_n1/atlas.png"))

    def test_confirmation_and_existing_data(self):
        with self.assertRaises(ValueError): self.transfer.apply(self.source)
        (self.dest / "config.json").write_text("{}")
        with self.assertRaises(ValueError): self.transfer.preview(self.source)

    def test_corrupt_database_and_interrupted_copy_leave_destination_empty(self):
        with patch("genshin_navigator.portable_transfer.shutil.copy2", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError): self.transfer.apply(self.source, stopped=True)
        self.assertFalse((self.dest / "config.json").exists())
        self.db.write_text("corrupt")
        with self.assertRaises(sqlite3.DatabaseError): self.transfer.apply(self.source, stopped=True)
        self.assertFalse((self.dest / "datasets").exists())

    def test_disk_space_and_external_path(self):
        with patch("genshin_navigator.portable_transfer.shutil.disk_usage", return_value=Mock(free=0)):
            with self.assertRaises(ValueError): self.transfer.preview(self.source)
        raw = json.loads((self.source / "config.json").read_text()); raw["map_path"] = str(self.source.parent / "external.png")
        (self.source / "config.json").write_text(json.dumps(raw))
        self.assertTrue(self.transfer.preview(self.source)["external_paths"])
        with self.assertRaises(ValueError): self.transfer.apply(self.source, stopped=True)

    def test_publish_failure_rolls_back(self):
        import os
        replace = os.replace
        def fail_config(src, dst):
            if Path(dst) == self.dest / "config.json": raise OSError("publish interrupted")
            return replace(src, dst)
        with patch("genshin_navigator.portable_transfer.os.replace", side_effect=fail_config):
            with self.assertRaises(OSError): self.transfer.apply(self.source, stopped=True)
        self.assertFalse((self.dest / "datasets/local/data").exists())
        self.assertFalse((self.dest / "config.json").exists())

    def test_relative_escape_is_reported(self):
        raw = json.loads((self.source / "config.json").read_text())
        raw["data"]["database_path"] = "../outside.db"
        (self.source / "config.json").write_text(json.dumps(raw))
        self.assertIn("../outside.db", self.transfer.preview(self.source)["external_paths"])
