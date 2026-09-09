from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Callable, Protocol

from PIL import Image

from .poi import PointOfInterest


POINT_INFO_URL = (
    "https://sg-public-api-static.hoyolab.com/common/map_user/ys_obc/"
    "v1/map/point/info"
)
ALLOWED_IMAGE_FORMATS = {"PNG": ("image/png", ".png"),
                         "JPEG": ("image/jpeg", ".jpg"),
                         "WEBP": ("image/webp", ".webp")}


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style"}:
            self._ignored_depth += 1
            return
        if tag.lower() in {"br", "p", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if tag.lower() in {"p", "div", "li"}:
            self.parts.append("\n")


def plain_text(value: object) -> str:
    parser = _PlainTextParser()
    parser.feed(str(value or ""))
    text = html.unescape("".join(parser.parts)).replace("\r", "")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line.strip() for line in text.split("\n"))).strip()


def official_point_id(poi_id: str) -> int | None:
    match = re.fullmatch(r"hoyolab:(\d+)", poi_id)
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class PoiHint:
    poi_id: str
    content: str = ""
    image_url: str | None = None
    links: tuple[str, ...] = ()
    video_url: str | None = None
    source_updated_at: str | None = None
    source: str = "HoYoLAB Interactive Map"
    image_urls: tuple[str, ...] = ()

    @property
    def images(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(((self.image_url,) if self.image_url else ()) + self.image_urls))

    @property
    def empty(self) -> bool:
        return not (self.content or self.images or self.links or self.video_url)


@dataclass(frozen=True)
class CachedPoiHint:
    hint: PoiHint
    fetched_at: datetime
    image_path: Path | None = None


class HintState(str, Enum):
    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    CACHED = "cached"
    OFFLINE = "offline"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True)
class PoiHintSnapshot:
    poi_id: str | None
    state: HintState
    hint: PoiHint | None = None
    image_path: Path | None = None
    message: str = ""
    image_index: int = 0
    fetched_at: datetime | None = None


class PoiHintProvider(Protocol):
    def fetch(self, poi_id: str) -> PoiHint: ...
    def download_image(self, url: str) -> tuple[bytes, str, str]: ...


class PoiHintRepository(Protocol):
    def get(self, poi_id: str) -> CachedPoiHint | None: ...
    def put(
        self, hint: PoiHint, image: tuple[bytes, str, str] | None = None
    ) -> CachedPoiHint: ...
    def prune(self, max_bytes: int, protected_poi_id: str | None = None) -> None: ...


def parse_point_info(payload: object, expected_poi_id: str) -> PoiHint:
    if not isinstance(payload, dict) or payload.get("retcode") != 0:
        message = payload.get("message") if isinstance(payload, dict) else None
        raise ValueError(str(message or "Invalid HoYoLAB point response"))
    data = payload.get("data")
    info = data.get("info") if isinstance(data, dict) else None
    if not isinstance(info, dict):
        raise ValueError("HoYoLAB point response has no info")
    numeric_id = official_point_id(expected_poi_id)
    if numeric_id is None or int(info.get("id", -1)) != numeric_id:
        raise ValueError("HoYoLAB point response id mismatch")

    links: list[str] = []
    for item in info.get("url_list") or []:
        candidate = item if isinstance(item, str) else item.get("url") if isinstance(item, dict) else None
        if candidate and urllib.parse.urlparse(str(candidate)).scheme == "https":
            links.append(str(candidate))
    video = info.get("video")
    video_url = None
    if isinstance(video, str):
        video_url = video
    elif isinstance(video, dict):
        video_url = video.get("url") or video.get("video_url")
    if video_url and urllib.parse.urlparse(str(video_url)).scheme != "https":
        video_url = None
    image_url = info.get("img") if isinstance(info.get("img"), str) else None
    if image_url and urllib.parse.urlparse(image_url).scheme != "https":
        image_url = None
    images = [image_url] if image_url else []
    # Only explicit image fields in the public response; no discussions scraping.
    for field in ("img_list", "image_list", "images", "img"):
        items = info.get(field)
        if isinstance(items, list):
            for item in items[:32]:
                url = item if isinstance(item, str) else item.get("url") if isinstance(item, dict) else None
                if isinstance(url, str) and urllib.parse.urlparse(url).scheme == "https":
                    images.append(url)
    images = list(dict.fromkeys(images))[:32]
    return PoiHint(
        poi_id=expected_poi_id,
        content=plain_text(info.get("content")),
        image_url=images[0] if images else None,
        image_urls=tuple(images),
        links=tuple(dict.fromkeys(links)),
        video_url=str(video_url) if video_url else None,
        source_updated_at=(
            str(data.get("last_update_time")) if data.get("last_update_time") else None
        ),
    )


class HoyoLabPoiHintProvider:
    def __init__(
        self,
        *,
        map_id: int = 2,
        lang: str = "ru-ru",
        timeout_seconds: float = 8.0,
        attempts: int = 2,
        opener: Callable[..., object] = urllib.request.urlopen,
    ):
        self.map_id = map_id
        self.lang = lang
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self._opener = opener

    def _read(self, url: str, *, max_bytes: int | None = None) -> tuple[bytes, str]:
        error: Exception | None = None
        for attempt in range(self.attempts):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "GenshinNavigator/0.1"})
                response = self._opener(request, timeout=self.timeout_seconds)
                with response:  # type: ignore[attr-defined]
                    declared = int(response.headers.get("Content-Length", "0") or 0)  # type: ignore[attr-defined]
                    if max_bytes is not None and declared > max_bytes:
                        raise ValueError("HoYoLAB image is larger than the configured limit")
                    body = response.read((max_bytes + 1) if max_bytes else None)  # type: ignore[attr-defined]
                    content_type = str(response.headers.get_content_type())  # type: ignore[attr-defined]
                if max_bytes is not None and len(body) > max_bytes:
                    raise ValueError("HoYoLAB image is larger than the configured limit")
                return body, content_type
            except (OSError, ValueError) as caught:
                error = caught
                if attempt + 1 < self.attempts:
                    time.sleep(0.25)
        assert error is not None
        raise error

    def fetch(self, poi_id: str) -> PoiHint:
        point_id = official_point_id(poi_id)
        if point_id is None:
            raise ValueError("Only official HoYoLAB POIs have guidance")
        query = urllib.parse.urlencode(
            {"point_id": point_id, "map_id": self.map_id, "app_sn": "ys_obc", "lang": self.lang}
        )
        body, _ = self._read(f"{POINT_INFO_URL}?{query}")
        return parse_point_info(json.loads(body.decode("utf-8")), poi_id)

    def download_image(self, url: str) -> tuple[bytes, str, str]:
        if urllib.parse.urlparse(url).scheme != "https":
            raise ValueError("Only HTTPS hint images are accepted")
        body, response_type = self._read(url, max_bytes=8 * 1024 * 1024)
        with Image.open(BytesIO(body)) as image:
            image.verify()
            image_format = str(image.format or "").upper()
        if image_format not in ALLOWED_IMAGE_FORMATS:
            raise ValueError("Unsupported HoYoLAB hint image format")
        mime, suffix = ALLOWED_IMAGE_FORMATS[image_format]
        if response_type and response_type not in {mime, "application/octet-stream"}:
            raise ValueError("HoYoLAB image MIME type does not match its contents")
        return body, mime, suffix


class SqlitePoiHintRepository:
    def __init__(self, database: str | Path, cache_dir: str | Path):
        self.database = Path(database)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def get(self, poi_id: str) -> CachedPoiHint | None:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT * FROM poi_hints WHERE poi_id = ?", (poi_id,)).fetchone()
            if row is None:
                return None
            asset = connection.execute("SELECT * FROM poi_hint_assets WHERE poi_id = ?", (poi_id,)).fetchone()
            gallery = connection.execute("SELECT image_url FROM poi_hint_gallery WHERE poi_id=? ORDER BY ordinal", (poi_id,)).fetchall()
            now = datetime.now(timezone.utc).isoformat()
            if asset is not None:
                connection.execute("UPDATE poi_hint_assets SET last_accessed_at = ? WHERE poi_id = ?", (now, poi_id))
        image_path = None
        if asset is not None:
            stored = Path(str(asset["relative_path"]))
            candidate = stored if stored.is_absolute() else self.database.parent / stored
            if candidate.exists():
                image_path = candidate.resolve()
        return CachedPoiHint(
            PoiHint(
                poi_id=poi_id,
                content=str(row["content"] or ""),
                image_url=str(row["image_url"]) if row["image_url"] else None,
                links=tuple(json.loads(row["links_json"] or "[]")),
                video_url=str(row["video_url"]) if row["video_url"] else None,
                source_updated_at=str(row["source_updated_at"]) if row["source_updated_at"] else None,
                source=str(row["source"]),
                image_urls=tuple(str(item[0]) for item in gallery),
            ),
            self._datetime(str(row["fetched_at"])),
            image_path,
        )

    def put(self, hint: PoiHint, image: tuple[bytes, str, str] | None = None) -> CachedPoiHint:
        now = datetime.now(timezone.utc).isoformat()
        asset_values = None
        image_path = None
        obsolete_gallery = []
        if image is not None:
            body, mime, suffix = image
            digest = hashlib.sha256(body).hexdigest()
            safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", hint.poi_id)
            image_path = self.cache_dir / f"{safe_id}-{digest[:12]}{suffix}"
            descriptor, temporary = tempfile.mkstemp(prefix=".hint-", suffix=".tmp", dir=self.cache_dir)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, image_path)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
            relative = os.path.relpath(image_path.resolve(), self.database.parent.resolve())
            asset_values = (hint.poi_id, relative, mime, len(body), digest, now, now)
        with self._lock, closing(self._connect()) as connection, connection:
            previous_hint = connection.execute("SELECT image_url FROM poi_hints WHERE poi_id=?", (hint.poi_id,)).fetchone()
            changed_image = bool(previous_hint and previous_hint[0] != hint.image_url)
            obsolete_asset = connection.execute(
                "SELECT relative_path FROM poi_hint_assets WHERE poi_id = ?", (hint.poi_id,)
            ).fetchone()
            for index, url in enumerate(hint.images):
                connection.execute(
                    "INSERT INTO poi_hint_gallery(poi_id,ordinal,image_url) VALUES (?,?,?) "
                    "ON CONFLICT(poi_id,ordinal) DO UPDATE SET image_url=excluded.image_url "
                    "WHERE poi_hint_gallery.image_url=excluded.image_url", (hint.poi_id,index,url))
                old = connection.execute("SELECT image_url,relative_path FROM poi_hint_gallery WHERE poi_id=? AND ordinal=?", (hint.poi_id,index)).fetchone()
                if old[0] != url:
                    if old[1]: obsolete_gallery.append(old[1])
                    connection.execute("DELETE FROM poi_hint_gallery WHERE poi_id=? AND ordinal=?", (hint.poi_id,index))
                    connection.execute("INSERT INTO poi_hint_gallery(poi_id,ordinal,image_url) VALUES (?,?,?)", (hint.poi_id,index,url))
            for old in connection.execute("SELECT relative_path FROM poi_hint_gallery WHERE poi_id=? AND ordinal>=?", (hint.poi_id,len(hint.images))).fetchall():
                if old[0]: obsolete_gallery.append(old[0])
            connection.execute("DELETE FROM poi_hint_gallery WHERE poi_id=? AND ordinal>=?", (hint.poi_id,len(hint.images)))
            connection.execute(
                """
                INSERT INTO poi_hints VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(poi_id) DO UPDATE SET content=excluded.content,
                    image_url=excluded.image_url, links_json=excluded.links_json,
                    video_url=excluded.video_url, source_updated_at=excluded.source_updated_at,
                    source=excluded.source, fetched_at=excluded.fetched_at,
                    is_empty=excluded.is_empty
                """,
                (hint.poi_id, hint.content, hint.image_url, json.dumps(hint.links), hint.video_url,
                 hint.source_updated_at, hint.source, now, int(hint.empty)),
            )
            if asset_values is not None:
                connection.execute(
                    """
                    INSERT INTO poi_hint_assets VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(poi_id) DO UPDATE SET relative_path=excluded.relative_path,
                        mime_type=excluded.mime_type, size_bytes=excluded.size_bytes,
                        sha256=excluded.sha256, fetched_at=excluded.fetched_at,
                        last_accessed_at=excluded.last_accessed_at
                    """, asset_values,
                )
            elif hint.image_url is None or changed_image:
                connection.execute("DELETE FROM poi_hint_assets WHERE poi_id = ?", (hint.poi_id,))
        for stored in obsolete_gallery: self._remove_asset(stored)
        if obsolete_asset is not None and (image is not None or hint.image_url is None or changed_image):
            stored = Path(str(obsolete_asset["relative_path"]))
            old_path = stored if stored.is_absolute() else self.database.parent / stored
            if image_path is None or old_path.resolve() != image_path.resolve():
                try:
                    self._remove_asset(str(old_path))
                except FileNotFoundError:
                    pass
        return self.get(hint.poi_id)

    def _asset_path(self, stored):
        path = (self.database.parent / stored).resolve()
        if not path.is_relative_to(self.cache_dir.resolve()):
            raise ValueError("Hint asset is outside cache directory")
        return path

    def _remove_asset(self, stored):
        self._asset_path(stored).unlink(missing_ok=True)

    def gallery_image(self, poi_id, index, url, download):
        if index == 0:
            cached = self.get(poi_id)
            if cached and cached.image_path: return cached.image_path
        with self._lock, closing(self._connect()) as db, db:
            row = db.execute("SELECT relative_path FROM poi_hint_gallery WHERE poi_id=? AND ordinal=? AND image_url=?", (poi_id,index,url)).fetchone()
            if row and row[0] and self._asset_path(row[0]).is_file():
                db.execute("UPDATE poi_hint_gallery SET last_accessed_at=? WHERE poi_id=? AND ordinal=?", (datetime.now(timezone.utc).isoformat(),poi_id,index))
                return self._asset_path(row[0])
        body,mime,suffix = download(url)
        digest = hashlib.sha256(body).hexdigest()
        path = self.cache_dir / (re.sub(r"[^A-Za-z0-9_.-]", "_", poi_id) + f"-g{index}-{digest[:12]}{suffix}")
        fd, temporary = tempfile.mkstemp(dir=self.cache_dir, prefix=".gallery-")
        try:
            with os.fdopen(fd,"wb") as stream:
                stream.write(body); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary,path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, closing(self._connect()) as db, db:
            db.execute("UPDATE poi_hint_gallery SET relative_path=?,mime_type=?,size_bytes=?,sha256=?,fetched_at=?,last_accessed_at=? WHERE poi_id=? AND ordinal=? AND image_url=?",
                       (os.path.relpath(path,self.database.parent),mime,len(body),digest,now,now,poi_id,index,url))
        return path

    def prune(self, max_bytes: int, protected_poi_id: str | None = None) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT *, -1 AS ordinal FROM poi_hint_assets UNION ALL "
                "SELECT poi_id,relative_path,mime_type,size_bytes,sha256,fetched_at,last_accessed_at,ordinal "
                "FROM poi_hint_gallery WHERE relative_path IS NOT NULL ORDER BY last_accessed_at ASC"
            ).fetchall()
            total = sum(int(row["size_bytes"]) for row in rows)
            for row in rows:
                if total <= max_bytes:
                    break
                poi_id = str(row["poi_id"])
                if poi_id == protected_poi_id:
                    continue
                stored = Path(str(row["relative_path"]))
                path = stored if stored.is_absolute() else self.database.parent / stored
                try:
                    self._remove_asset(str(path))
                except FileNotFoundError:
                    pass
                if row["ordinal"] == -1:
                    connection.execute("DELETE FROM poi_hint_assets WHERE poi_id = ?", (poi_id,))
                else:
                    connection.execute("UPDATE poi_hint_gallery SET relative_path=NULL,size_bytes=NULL WHERE poi_id=? AND ordinal=?", (poi_id,row["ordinal"]))
                total -= int(row["size_bytes"])


class PoiHintService:
    def __init__(
        self,
        provider: PoiHintProvider,
        repository: PoiHintRepository | None,
        *,
        refresh_after: timedelta = timedelta(days=7),
        negative_after: timedelta = timedelta(hours=24),
        max_cache_bytes: int = 256 * 1024 * 1024,
    ):
        self.provider = provider
        self.repository = repository
        self.refresh_after = refresh_after
        self.negative_after = negative_after
        self.max_cache_bytes = max_cache_bytes
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="poi-guidance")
        self._future: Future[CachedPoiHint] | None = None
        self._future_poi_id: str | None = None
        self._snapshot = PoiHintSnapshot(None, HintState.IDLE)
        self._memory: dict[str, CachedPoiHint] = {}
        self._image_future = None
        self._image_request = None

    def select_image(self, index: int):
        hint = self._snapshot.hint
        if hint is None or not 0 <= index < len(hint.images): return
        if self._image_future: self._image_future.cancel()
        self._snapshot = replace(self._snapshot, image_index=index, image_path=None, message="Загрузка изображения…")
        self._image_request = (hint.poi_id,index,hint.images[index])
        if self.repository is None:
            self._snapshot = replace(self._snapshot,message="Галерея требует SQLite-кэша")
            return
        self._image_future = self._executor.submit(self._load_image,*self._image_request)

    def _load_image(self, poi_id, index, url):
        path = self.repository.gallery_image(poi_id,index,url,self.provider.download_image)
        self.repository.prune(self.max_cache_bytes,poi_id)
        return path

    @property
    def snapshot(self) -> PoiHintSnapshot:
        self.poll()
        return self._snapshot

    def _cached(self, poi_id: str) -> CachedPoiHint | None:
        return self.repository.get(poi_id) if self.repository is not None else self._memory.get(poi_id)

    def request(self, poi: PointOfInterest | None) -> PoiHintSnapshot:
        poi_id = poi.id if poi is not None else None
        if poi_id == self._snapshot.poi_id:
            return self.snapshot
        if self._future: self._future.cancel()
        if self._image_future: self._image_future.cancel()
        self._image_future = None
        if poi_id is None:
            self._snapshot = PoiHintSnapshot(None, HintState.IDLE)
            return self._snapshot
        if official_point_id(poi_id) is None:
            self._snapshot = PoiHintSnapshot(poi_id, HintState.UNAVAILABLE, message="Официальная подсказка недоступна")
            return self._snapshot
        cached = self._cached(poi_id)
        now = datetime.now(timezone.utc)
        max_age = self.negative_after if cached and cached.hint.empty else self.refresh_after
        if cached is not None:
            state = HintState.CACHED
            missing_image = bool(cached.hint.image_url and cached.image_path is None)
            fresh = now - cached.fetched_at <= max_age and not missing_image
            message = "Кэш" if fresh else "Кэш · обновление…"
            age = max(0, int((now-cached.fetched_at).total_seconds()/86400))
            self._snapshot = PoiHintSnapshot(poi_id, state, cached.hint, cached.image_path, message + f" · {age} дн.", fetched_at=cached.fetched_at)
            if fresh:
                return self._snapshot
        else:
            self._snapshot = PoiHintSnapshot(poi_id, HintState.LOADING, message="Загрузка подсказки…")
        self._future_poi_id = poi_id
        self._future = self._executor.submit(self._load, poi_id)
        return self._snapshot

    def _load(self, poi_id: str) -> CachedPoiHint:
        hint = self.provider.fetch(poi_id)
        image = None
        if hint.image_url:
            try:
                image = self.provider.download_image(hint.image_url)
            except (OSError, ValueError):
                image = None
        if self.repository is not None:
            cached = self.repository.put(hint, image)
            self.repository.prune(self.max_cache_bytes, poi_id)
            return cached
        cached = CachedPoiHint(hint, datetime.now(timezone.utc))
        self._memory[poi_id] = cached
        return cached

    def poll(self) -> PoiHintSnapshot:
        if self._image_future is not None and self._image_future.done():
            future, request = self._image_future,self._image_request
            self._image_future = None
            if request[:2] == (self._snapshot.poi_id,self._snapshot.image_index):
                try:
                    self._snapshot = replace(self._snapshot,image_path=future.result(),message="Изображение · офлайн-кэш")
                except (OSError,ValueError,RuntimeError):
                    self._snapshot = replace(self._snapshot,message="Изображение недоступно офлайн / ошибка загрузки")
        future = self._future
        if future is None or not future.done():
            return self._snapshot
        poi_id = self._future_poi_id
        self._future = None
        self._future_poi_id = None
        try:
            cached = future.result()
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
            if poi_id == self._snapshot.poi_id:
                if self._snapshot.hint is not None:
                    self._snapshot = PoiHintSnapshot(
                        poi_id, HintState.OFFLINE, self._snapshot.hint,
                        self._snapshot.image_path, "Офлайн · показан кэш",
                    )
                else:
                    self._snapshot = PoiHintSnapshot(poi_id, HintState.ERROR, message=f"Подсказка недоступна: {error}")
            return self._snapshot
        if poi_id == self._snapshot.poi_id:
            if cached.hint.empty:
                message = "У HoYoLAB нет подсказки"
            elif self.repository is None:
                message = "Онлайн · JSON-режим без постоянного кэша"
            else:
                message = "HoYoLAB · сохранено офлайн"
            selected_index=self._snapshot.image_index
            self._snapshot = PoiHintSnapshot(poi_id, HintState.READY, cached.hint, cached.image_path, message, fetched_at=cached.fetched_at)
            if selected_index: self.select_image(min(selected_index,len(cached.hint.images)-1))
        return self._snapshot

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
