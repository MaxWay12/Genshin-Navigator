"""Manual public release checks and side-by-side portable updates."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .portable_transfer import PortableTransfer, _linked

REPOSITORY = "MaxWay12/Genshin-Navigator"
API = f"https://api.github.com/repos/{REPOSITORY}/releases"
MAX_ARCHIVE = 2 * 1024 ** 3
MAX_UNPACKED = 5 * 1024 ** 3


def version_key(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:(?:-)?(alpha|beta|rc|a|b)(?:[.-]?(\d+))?)?", value)
    if not match:
        raise ValueError("Неизвестный формат версии")
    major, minor, patch, stage, number = match.groups()
    return int(major), int(minor), int(patch), {None: 3, "rc": 2, "beta": 1, "b": 1, "alpha": 0, "a": 0}[stage], int(number or 1)


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    notes: str
    archive_name: str
    archive_url: str
    checksum_url: str
    size: int


def _download_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or not parsed.path.startswith(f"/{REPOSITORY}/releases/download/"):
        raise ValueError("Ссылка загрузки не принадлежит официальному репозиторию")
    return url


class ReleaseProvider:
    def __init__(self, *, opener=urlopen, timeout=8):
        self.opener, self.timeout = opener, timeout

    def newer_release(self, current: str) -> ReleaseInfo | None:
        releases = []
        for page in range(1, 11):
            request = Request(f"{API}?per_page=100&page={page}", headers={"User-Agent": "GenshinNavigator", "Accept": "application/vnd.github+json"})
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("Ответ списка версий слишком большой")
            entries = json.loads(raw)
            if not isinstance(entries, list):
                raise ValueError("Некорректный ответ списка версий")
            releases.extend(entries)
            if len(entries) < 100:
                break
        candidates = []
        for item in releases:
            if not isinstance(item, dict) or item.get("draft") or not item.get("published_at"):
                continue
            tag = item.get("tag_name", "")
            try:
                key = version_key(tag)
            except (ValueError, TypeError):
                continue
            if key <= version_key(current):
                continue
            assets = {a.get("name"): a for a in item.get("assets", []) if isinstance(a, dict) and a.get("state", "uploaded") == "uploaded"}
            name = f"GenshinNavigator-{tag}-windows-x64.zip"
            if name not in assets:
                continue
            archive, checksum = assets[name], assets.get(name + ".sha256")
            size = archive.get("size", 0)
            if not isinstance(size, int) or not 0 < size <= MAX_ARCHIVE:
                raise ValueError("Недопустимый размер portable ZIP")
            candidates.append((key, ReleaseInfo(tag, str(item.get("body") or "")[:40000], name,
                              _download_url(archive["browser_download_url"]),
                              _download_url(checksum["browser_download_url"]) if checksum else "", size)))
        return max(candidates, key=lambda entry: entry[0])[1] if candidates else None


def safe_members(archive: zipfile.ZipFile):
    entries, seen, size = [], set(), 0
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        path = PurePosixPath(name)
        parts = path.parts
        mode = info.external_attr >> 16
        if (not parts or path.is_absolute() or any(p in (".", "..") or ":" in p or p.endswith((".", " "))
                or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p) for p in parts)
                or stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
            raise ValueError("Небезопасный путь или ссылка в ZIP")
        key = str(path).casefold()
        if key in seen:
            raise ValueError("Повторяющийся путь в ZIP")
        seen.add(key)
        size += info.file_size
        if size > MAX_UNPACKED or len(seen) > 20000:
            raise ValueError("Распакованный архив слишком большой")
        entries.append((info, path))
    return entries, size


class UpdateService:
    def __init__(self, source: Path, *, opener=urlopen):
        self.source = source.resolve()
        self.opener = opener
        self.cancelled = threading.Event()

    def _destination(self, destination):
        raw = Path(destination).absolute()
        if any(p.exists() and _linked(p) for p in (raw, *raw.parents)):
            raise ValueError("Папка обновления не должна проходить через ссылки/junction")
        dest = raw.resolve()
        if dest == self.source or dest.is_relative_to(self.source) or self.source.is_relative_to(dest):
            raise ValueError("Выберите новую отдельную папку, не текущую установку")
        if not dest.parent.is_dir() or (dest.exists() and (not dest.is_dir() or any(dest.iterdir()))):
            raise ValueError("Нужна новая или пустая папка с существующим родителем")
        return dest

    def preview(self, release: ReleaseInfo, destination: Path):
        dest = self._destination(destination)
        if not release.checksum_url:
            raise ValueError("В релизе отсутствует SHA-256; установка запрещена")
        with tempfile.TemporaryDirectory(prefix="navigator-preview-") as empty:
            transfer = PortableTransfer(empty).preview(self.source)
        if transfer["external_paths"]:
            raise ValueError("Перенос содержит внешние пути: " + ", ".join(transfer["external_paths"]))
        required = release.size * 2 + transfer["bytes"] * 2 + 64 * 1024 ** 2
        if shutil.disk_usage(dest.parent).free < required:
            raise ValueError("Недостаточно свободного места")
        return {**transfer, "version": release.version, "download_bytes": release.size, "destination": str(dest)}

    def _check_cancel(self):
        if self.cancelled.is_set():
            raise InterruptedError("Обновление отменено; старая установка сохранена")

    def _download(self, url, target, limit):
        request = Request(_download_url(url), headers={"User-Agent": "GenshinNavigator"})
        digest, count = hashlib.sha256(), 0
        with self.opener(request, timeout=8) as response, target.open("wb") as stream:
            while True:
                self._check_cancel()
                block = response.read(1024 * 1024)
                if not block:
                    break
                count += len(block)
                if count > limit:
                    raise ValueError("Загрузка превышает заявленный размер")
                stream.write(block)
                digest.update(block)
        return digest.hexdigest(), count

    def apply(self, release: ReleaseInfo, destination: Path, *, stopped=False, progress=lambda stage: None):
        if stopped is not True:
            raise ValueError("Завершите GPS и подтвердите перенос")
        report = self.preview(release, destination)
        dest = self._destination(destination)
        self._check_cancel()
        with tempfile.TemporaryDirectory(prefix=".navigator-update-", dir=dest.parent) as temp:
            stage = Path(temp)
            progress("Загрузка ZIP…")
            archive_path = stage / "release.zip"
            actual, size = self._download(release.archive_url, archive_path, release.size)
            if size != release.size:
                raise ValueError("ZIP загружен не полностью")
            checksum = stage / "checksum.txt"
            self._download(release.checksum_url, checksum, 4096)
            match = re.fullmatch(r"\s*([a-fA-F0-9]{64})\s+\*?([^\r\n]+)\s*", checksum.read_text(encoding="utf-8-sig"))
            if not match or match[2].strip() != release.archive_name or actual != match[1].lower():
                raise ValueError("SHA-256 не совпадает; установка остановлена")
            progress("Проверка и распаковка…")
            unpacked = stage / "unpacked"
            unpacked.mkdir()
            with zipfile.ZipFile(archive_path) as archive:
                entries, unpack_size = safe_members(archive)
                if shutil.disk_usage(dest.parent).free < unpack_size + 2 * report["bytes"] + 64 * 1024 ** 2:
                    raise ValueError("Недостаточно места для распаковки и переноса")
                for info, path in entries:
                    self._check_cancel()
                    target = unpacked.joinpath(*path.parts)
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(info) as src, target.open("xb") as dst:
                            while block := src.read(1024 * 1024):
                                self._check_cancel()
                                dst.write(block)
            roots = [p.parent for p in unpacked.rglob("GenshinNavigator.exe")]
            if len(roots) != 1:
                raise ValueError("В архиве не найдена единственная portable-установка")
            package = roots[0]
            if not (package / "regions.json").is_file() or not (package / "config.example.json").is_file():
                raise ValueError("Неполная portable-установка")
            from .region_manifest import load_region_manifest
            load_region_manifest(package / "regions.json")
            progress("Перенос настроек, карт и прогресса…")
            PortableTransfer(package, installed_destination=dest).apply(
                self.source, stopped=True, cancelled=self._check_cancel)
            self._check_cancel()
            self._destination(dest)
            was_empty = dest.exists()
            if was_empty:
                dest.rmdir()
            try:
                os.replace(package, dest)
            except BaseException:
                if was_empty:
                    dest.mkdir(exist_ok=True)
                raise
        return {**report, "ready": True}
