"""Explicit, offline transfer into an empty portable installation."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from .config import load_config


CONFIGS = ("config.json", "config.sumeru.json")
DATA_DIRS = ("data", "poi", "calibration", "ui", "references", "cache")


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".navigator-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


class PortableTransfer:
    def __init__(self, destination: str | Path):
        self.destination = Path(destination).resolve()

    def _empty(self) -> None:
        for name in CONFIGS:
            if (self.destination / name).exists():
                raise ValueError("В новой папке уже есть настройки. Выберите чистую распакованную установку.")
        local = self.destination / "datasets/local"
        if local.exists() and any(local.iterdir()):
            raise ValueError("В новой установке уже есть пользовательские данные; объединение не поддерживается.")

    def _files(self, source: Path) -> list[Path]:
        selected = []
        for name in CONFIGS:
            path = source / name
            if path.exists():
                selected.append(path)
        local = source / "datasets/local"
        if local.exists() and (_linked(source / "datasets") or _linked(local)):
            raise ValueError("Перенос через ссылки/junction не поддерживается.")
        for name in DATA_DIRS:
            base = local / name
            if not base.exists():
                continue
            for directory, dirs, files in os.walk(base, followlinks=False):
                current = Path(directory)
                if _linked(current) or any(_linked(current / item) for item in dirs + files):
                    raise ValueError("Перенос через ссылки/junction не поддерживается.")
                selected.extend(current / item for item in files if not item.endswith(("-wal", "-shm", "-journal")))
        if any(_linked(p) or not p.resolve().is_relative_to(source) for p in selected):
            raise ValueError("Обнаружен путь за пределами исходной установки.")
        return selected

    def preview(self, source: str | Path) -> dict:
        self._empty()
        source = Path(source).resolve()
        if not source.is_dir() or source == self.destination or self.destination.is_relative_to(source) or source.is_relative_to(self.destination):
            raise ValueError("Выберите отдельную папку предыдущей установки.")
        if not any((source / name).is_file() for name in CONFIGS):
            raise ValueError("В выбранной папке не найден config.json или config.sumeru.json.")
        files = self._files(source)
        external = []
        for path in files:
            if path.suffix.lower() == ".json":
                raw = json.loads(path.read_text(encoding="utf-8"))
                self._rewrite(raw, source, external, base=path.parent)
        size = sum(p.stat().st_size for p in files)
        if shutil.disk_usage(self.destination).free < size + 16 * 1024 * 1024:
            raise ValueError("Недостаточно свободного места для переноса.")
        return {"file_count": len(files), "bytes": size, "external_paths": sorted(set(external)),
                "requires_stopped_gps": True, "login_transferred": False}

    def _rewrite(self, value, source: Path, external: list[str], *, base: Path | None = None):
        if isinstance(value, dict):
            return {key: self._rewrite(item, source, external, base=base) for key, item in value.items()}
        if isinstance(value, list):
            return [self._rewrite(item, source, external, base=base) for item in value]
        if isinstance(value, str) and Path(value).is_absolute():
            path = Path(value).resolve()
            if path.is_relative_to(source):
                return str(self.destination / path.relative_to(source))
            external.append(value)
        elif isinstance(value, str) and ".." in Path(value).parts:
            if not ((base or source) / value).resolve().is_relative_to(source):
                external.append(value)
        return value

    def apply(self, source: str | Path, *, stopped: bool = False) -> dict:
        if not stopped:
            raise ValueError("Сначала завершите GPS в старой установке и подтвердите это.")
        report = self.preview(source)
        source = Path(source).resolve()
        if report["external_paths"]:
            raise ValueError("Конфигурация содержит внешние пути. Перенос остановлен; исходные файлы сохранены.")
        stage = Path(tempfile.mkdtemp(prefix=".navigator-transfer-", dir=self.destination))
        installed: list[Path] = []
        try:
            for path in self._files(source):
                target = stage / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as src:
                        with closing(sqlite3.connect(target)) as dst:
                            src.backup(dst)
                            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                                raise ValueError("Проверка SQLite не пройдена.")
                else:
                    shutil.copy2(path, target)
                if target.suffix.lower() == ".json":
                    raw = json.loads(target.read_text(encoding="utf-8"))
                    atomic_json(target, self._rewrite(raw, source, [], base=path.parent))
            for name in CONFIGS:
                if (stage / name).exists():
                    # Validate the real configuration, and separately resolve all asset
                    # paths against staging before publishing any destination file.
                    load_config(stage / name)
                    raw = json.loads((stage / name).read_text(encoding="utf-8"))
                    for key in ("map_path", "pyramid_path", "debug_map_path"):
                        if raw.get(key):
                            asset = Path(raw[key])
                            relative = asset.relative_to(self.destination) if asset.is_absolute() else asset
                            if not (stage / relative).resolve().is_relative_to(stage):
                                raise ValueError("Asset выходит за пределы установки")
                            if not (stage / relative).is_file():
                                raise ValueError(f"В переносе отсутствует обязательный asset: {relative}")
            self._empty()
            for child in list(stage.iterdir()):
                if child.name == "datasets":
                    target = self.destination / "datasets/local"
                    target.parent.mkdir(exist_ok=True)
                    if target.exists():
                        target.rmdir()  # Verified empty by _empty.
                    os.replace(child / "local", target)
                else:
                    target = self.destination / child.name
                    os.replace(child, target)
                installed.append(target)
            return report
        except BaseException:
            for target in reversed(installed):
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(stage)
