"""Local WebView launcher; its process exits before tracking starts."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from . import __version__
from .config import load_config
from .portable_transfer import PortableTransfer, atomic_json
from .region_manifest import load_region_manifest


def installation_root() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()


def spawn_command(root: Path, args: list[str]):
    command = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "genshin_navigator"]
    return subprocess.Popen(command + args, cwd=root, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class LauncherService:
    def __init__(self, root: Path):
        self.root = root.resolve()
        manifest = self.root / "regions.json"
        if not manifest.exists():
            manifest = self.root / "release/regions.portable.json"
        entries = load_region_manifest(manifest).entries
        self.regions = {entry.id: entry for entry in entries}
        self.paths = {entry.id: self.root / entry.config_path.name.replace(".example.json", ".json") for entry in entries}
        self.state_path = self.root / "datasets/local/ui/launcher_state.json"

    def config_path(self, region: str) -> Path:
        if region == "sumeru_desert" and "sumeru" in self.paths:
            region = "sumeru"
        if region not in self.paths:
            raise ValueError("Неизвестный регион")
        return self.paths[region]

    def _example(self, region: str) -> Path:
        if region == "sumeru_desert" and "sumeru" in self.paths:
            region = "sumeru"
        name = {"fontaine": "config.example.json", "sumeru": "config.sumeru-full.example.json"}.get(
            region, "config.sumeru.example.json")
        return self.root / name

    def _initial_config(self, region: str) -> dict:
        if region == "sumeru_desert" and "sumeru" in self.paths:
            region = "sumeru"
        raw = json.loads(self._example(region).read_text(encoding="utf-8"))
        old = self.root / "config.sumeru.json"
        if region == "sumeru" and old.is_file():
            from .sumeru_upgrade import upgrade_config
            raw = upgrade_config(json.loads(old.read_text(encoding="utf-8")), raw)
        return raw

    def ensure_config(self, region: str) -> Path:
        path = self.config_path(region)
        if not path.exists():
            atomic_json(path, self._initial_config(region))
        return path

    def read(self, region: str) -> dict:
        path = self.config_path(region)
        if path.exists():
            config = load_config(path)
        else:
            fd, name = tempfile.mkstemp(suffix=".json", dir=self.root)
            os.close(fd)
            temporary = Path(name)
            try:
                atomic_json(temporary, self._initial_config(region))
                config = load_config(temporary)
            finally:
                temporary.unlink(missing_ok=True)
        from .asset_setup import region_asset_status
        from .roi_setup import check_config_roi
        try:
            assets = region_asset_status(config, region)
            roi = check_config_roi(config).to_dict()
        except Exception as error:
            assets, roi = {"error": str(error)}, {}
        return {"mode": config.performance.mode, "numpad": config.navigation.numpad_enabled,
                "alternative": config.navigation.alternative_hotkeys.enabled,
                "width": config.navigation.hud_width, "height": config.navigation.hud_height,
                "tray": config.navigation.tray_enabled, "assets": assets, "roi": roi,
                "bindings": {key: f"Ctrl+Alt+{chr(vk)}" for key, vk in config.navigation.alternative_hotkeys.bindings.items()}}

    def save(self, region: str, values: dict) -> None:
        path = self.config_path(region)
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else self._initial_config(region)
        for key in ("numpad", "alternative", "tray"):
            if not isinstance(values.get(key), bool):
                raise ValueError("Некорректный переключатель настройки")
        raw.setdefault("performance", {})["mode"] = values["mode"]
        navigation = raw.setdefault("navigation", {})
        navigation.update(numpad_enabled=values["numpad"], tray_enabled=values["tray"],
                          global_hotkeys=values["numpad"] or values["alternative"],
                          hud_width=int(values["width"]), hud_height=int(values["height"]))
        navigation.setdefault("alternative_hotkeys", {})["enabled"] = values["alternative"]
        fd, name = tempfile.mkstemp(suffix=".json", dir=self.root)
        os.close(fd)
        temporary = Path(name)
        try:
            atomic_json(temporary, raw)
            load_config(temporary)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        atomic_json(self.state_path, {"format_version": 1, "region": region})


class LauncherBridge:
    def __init__(self, service: LauncherService):
        self._service = service
        self._window = None
        self._launch_args: list[str] | None = None
        self._operation = {"busy": False, "message": ""}
        self._lock = threading.Lock()
        self._source: str | None = None

    def initial(self):
        region = next(iter(self._service.regions))
        try:
            saved = json.loads(self._service.state_path.read_text(encoding="utf-8"))
            if saved.get("region") == "sumeru_desert" and "sumeru" in self._service.regions:
                saved["region"] = "sumeru"
            if saved.get("region") in self._service.regions:
                region = saved["region"]
        except (OSError, ValueError):
            pass
        return {"version": __version__, "region": region, "regions": [
            {"id": entry.id, "name": entry.display_name, "experimental": entry.support == "experimental"}
            for entry in self._service.regions.values()]}

    def read(self, region):
        return self._service.read(region)

    def save(self, region, values):
        if self._operation["busy"]:
            raise ValueError("Дождитесь завершения операции")
        self._service.save(region, values)
        return "Настройки сохранены. Они применятся при запуске GPS."

    def poll(self):
        return dict(self._operation)

    def _background(self, callback):
        with self._lock:
            if self._operation["busy"]:
                raise ValueError("Дождитесь завершения операции")
            self._operation = {"busy": True, "message": "Подготовка…"}
        def work():
            try:
                callback()
                self._operation = {"busy": False, "message": "Готово"}
            except Exception as error:
                self._operation = {"busy": False, "message": str(error), "error": True}
        threading.Thread(target=work, daemon=False).start()
        return True

    def prepare(self, region):
        from .asset_setup import setup_region
        path = self._service.ensure_config(region)
        return self._background(lambda: setup_region(load_config(path), region, progress=lambda stage: self._operation.update(message=str(stage))))

    def configure_roi(self, region):
        path = self._service.ensure_config(region)
        def configure():
            child = spawn_command(self._service.root, ["configure-roi", "--config", str(path)])
            if child.wait() != 0:
                raise ValueError("Настройка миникарты не завершена")
        return self._background(configure)

    def start(self, region, values):
        self.save(region, values)
        from .asset_setup import region_asset_status
        from .roi_setup import check_config_roi
        path = self._service.config_path(region)
        config = load_config(path)
        status = region_asset_status(config, region)
        if not status.get("ready"):
            raise ValueError("Сначала подготовьте данные региона")
        if not check_config_roi(config).valid:
            raise ValueError("Настройте область миникарты")
        self._launch_args = ["track", "--config", str(path)]
        self._window.destroy()
        return True

    def select_source(self):
        if self._operation["busy"]:
            raise ValueError("Дождитесь завершения операции")
        import webview
        paths = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not paths:
            return None
        self._source = str(paths[0])
        return PortableTransfer(self._service.root).preview(self._source)

    def transfer(self, stopped):
        if self._source is None or stopped is not True:
            raise ValueError("Выберите исходную папку и подтвердите завершение старого GPS")
        return self._background(lambda: PortableTransfer(self._service.root).apply(self._source, stopped=True))


def run_launcher(root: str | Path | None = None) -> int:
    try:
        import webview
        service = LauncherService(Path(root) if root else installation_root())
        bridge = LauncherBridge(service)
        html = (Path(__file__).parent / "launcher_ui/index.html").read_text(encoding="utf-8")
        bridge._window = webview.create_window("Genshin Navigator", html=html, js_api=bridge,
                                             width=820, height=620, min_size=(720, 560), background_color="#11171b")
        # A running transfer/setup must finish before its process can close.
        bridge._window.events.closing += lambda: False if bridge._operation["busy"] else None
        webview.start(gui="edgechromium", private_mode=True)
        if bridge._launch_args:
            spawn_command(service.root, bridge._launch_args)
        return 0
    except Exception as error:
        message = (f"Не удалось открыть окно Navigator: {error}\n"
                   "Для окна нужен Microsoft Edge WebView2 Runtime:\n"
                   "https://developer.microsoft.com/microsoft-edge/webview2/\n"
                   "Можно запустить GPS через Start-Fontaine.cmd.")
        print(message, file=sys.stderr)
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Genshin Navigator", 0x10)
        return 1
