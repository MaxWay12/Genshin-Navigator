from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from datetime import timedelta
from email.message import Message
from io import BytesIO
from pathlib import Path

from PIL import Image

from genshin_navigator.data_store import SqliteDataProvider
from genshin_navigator.poi import MapSpaceMetric, PointOfInterest
from genshin_navigator.poi_guidance import (
    HintState,
    HoyoLabPoiHintProvider,
    PoiHint,
    PoiHintService,
    SqlitePoiHintRepository,
    official_point_id,
    parse_point_info,
    plain_text,
)
from genshin_navigator.position import CoordinateSpace


class FakeProvider:
    def __init__(self, hints: dict[str, PoiHint], delay: float = 0.0):
        self.hints = hints
        self.delay = delay
        self.fetches: list[str] = []

    def fetch(self, poi_id: str) -> PoiHint:
        self.fetches.append(poi_id)
        if self.delay:
            time.sleep(self.delay)
        value = self.hints[poi_id]
        if isinstance(value, Exception):
            raise value
        return value

    def download_image(self, url: str):
        raise OSError("offline image")


class FakeResponse:
    def __init__(self, body: bytes, content_type: str):
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(body))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=None):
        return self.body if limit is None else self.body[:limit]


class PoiGuidanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "navigator.db"
        provider = SqliteDataProvider(self.database)
        metric = MapSpaceMetric(
            "fontaine", "surface", CoordinateSpace.SURFACE_ATLAS,
            ((1.0, 0.0), (0.0, 1.0)),
        )
        self.pois = [
            PointOfInterest("hoyolab:10", "chest", "A", "fontaine", "surface", CoordinateSpace.SURFACE_ATLAS, 1, 2),
            PointOfInterest("hoyolab:11", "chest", "B", "fontaine", "surface", CoordinateSpace.SURFACE_ATLAS, 3, 4),
        ]
        provider.replace_content("fontaine", self.pois, [metric], content_version="test")
        self.repository = SqlitePoiHintRepository(self.database, self.root / "cache")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_parses_and_sanitizes_official_point_info(self) -> None:
        hint = parse_point_info(
            {"retcode": 0, "data": {"info": {
                "id": 10, "content": "<p>Под <b>мостом</b><br>рядом</p>",
                "img": "https://example.test/a.png",
                "url_list": [{"url": "https://example.test/guide"}, "javascript:bad"],
                "video": {"url": "https://example.test/video"},
            }, "last_update_time": "2026-01-01 10:00:00"}},
            "hoyolab:10",
        )
        self.assertEqual(hint.content, "Под мостом\nрядом")
        self.assertEqual(hint.links, ("https://example.test/guide",))
        self.assertEqual(hint.video_url, "https://example.test/video")
        self.assertNotIn("<", hint.content)

    def test_provider_uses_public_point_info_contract_and_validates_image(self) -> None:
        seen_urls: list[str] = []
        payload = b'{"retcode":0,"data":{"info":{"id":10,"content":"hint"}}}'
        image_buffer = BytesIO()
        Image.new("RGB", (3, 2), "red").save(image_buffer, format="PNG")

        def opener(request, timeout):
            seen_urls.append(request.full_url)
            if "point/info" in request.full_url:
                return FakeResponse(payload, "application/json")
            return FakeResponse(image_buffer.getvalue(), "image/png")

        provider = HoyoLabPoiHintProvider(opener=opener)
        hint = provider.fetch("hoyolab:10")
        body, mime, suffix = provider.download_image("https://example.test/a.png")

        self.assertEqual(hint.content, "hint")
        self.assertIn("point_id=10", seen_urls[0])
        self.assertIn("app_sn=ys_obc", seen_urls[0])
        self.assertEqual((mime, suffix), ("image/png", ".png"))
        self.assertEqual(body, image_buffer.getvalue())

    def test_rejects_bad_payload_and_mismatched_id(self) -> None:
        with self.assertRaises(ValueError):
            parse_point_info({"retcode": 1, "message": "bad"}, "hoyolab:10")
        with self.assertRaises(ValueError):
            parse_point_info({"retcode": 0, "data": {"info": {"id": 99}}}, "hoyolab:10")
        self.assertIsNone(official_point_id("custom:10"))
        self.assertEqual(plain_text("<script>x</script>safe"), "safe")

    def test_repository_round_trip_and_cache_hit_avoids_network(self) -> None:
        cached = self.repository.put(PoiHint("hoyolab:10", content="cached"))
        self.assertEqual(cached.hint.content, "cached")
        remote = FakeProvider({"hoyolab:10": PoiHint("hoyolab:10", content="remote")})
        service = PoiHintService(remote, self.repository)
        try:
            snapshot = service.request(self.pois[0])
            self.assertEqual(snapshot.state, HintState.CACHED)
            self.assertEqual(snapshot.hint.content, "cached")
            self.assertEqual(remote.fetches, [])
        finally:
            service.close()

    def test_image_survives_reopen_and_lru_prune_only_removes_known_asset(self) -> None:
        image_buffer = BytesIO()
        Image.new("RGB", (3, 2), "blue").save(image_buffer, format="PNG")
        self.repository.put(
            PoiHint("hoyolab:10", image_url="https://example.test/a.png"),
            (image_buffer.getvalue(), "image/png", ".png"),
        )
        cached = SqlitePoiHintRepository(
            self.database, self.root / "cache"
        ).get("hoyolab:10")

        self.assertIsNotNone(cached.image_path)
        self.assertTrue(cached.image_path.exists())
        self.repository.prune(0)
        self.assertFalse(cached.image_path.exists())
        self.assertIsNone(self.repository.get("hoyolab:10").image_path)

    def test_stale_cache_is_visible_while_refreshing(self) -> None:
        self.repository.put(PoiHint("hoyolab:10", content="old"))
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("UPDATE poi_hints SET fetched_at='2000-01-01T00:00:00+00:00'")
            connection.commit()
        finally:
            connection.close()
        remote = FakeProvider({"hoyolab:10": PoiHint("hoyolab:10", content="new")})
        service = PoiHintService(remote, self.repository, refresh_after=timedelta(days=1))
        try:
            initial = service.request(self.pois[0])
            self.assertEqual(initial.hint.content, "old")
            self.assertEqual(initial.state, HintState.CACHED)
            for _ in range(100):
                result = service.poll()
                if result.state is HintState.READY:
                    break
                time.sleep(0.01)
            self.assertEqual(result.hint.content, "new")
        finally:
            service.close()

    def test_result_for_previous_target_is_not_applied(self) -> None:
        remote = FakeProvider({
            "hoyolab:10": PoiHint("hoyolab:10", content="first"),
            "hoyolab:11": PoiHint("hoyolab:11", content="second"),
        }, delay=0.03)
        service = PoiHintService(remote, self.repository)
        try:
            service.request(self.pois[0])
            service.request(self.pois[1])
            for _ in range(100):
                result = service.poll()
                if result.state is HintState.READY:
                    break
                time.sleep(0.01)
            self.assertEqual(result.poi_id, "hoyolab:11")
            self.assertEqual(result.hint.content, "second")
        finally:
            service.close()

    def test_slow_network_request_returns_immediately(self) -> None:
        remote = FakeProvider(
            {"hoyolab:10": PoiHint("hoyolab:10", content="later")}, delay=0.1
        )
        service = PoiHintService(remote, self.repository)
        try:
            started = time.perf_counter()
            result = service.request(self.pois[0])
            elapsed = time.perf_counter() - started
            self.assertEqual(result.state, HintState.LOADING)
            self.assertLess(elapsed, 0.05)
            for _ in range(100):
                if service.poll().state is HintState.READY:
                    break
                time.sleep(0.01)
        finally:
            service.close()

    def test_network_failure_does_not_replace_stale_cache(self) -> None:
        self.repository.put(PoiHint("hoyolab:10", content="offline copy"))
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("UPDATE poi_hints SET fetched_at='2000-01-01T00:00:00+00:00'")
            connection.commit()
        finally:
            connection.close()
        remote = FakeProvider({"hoyolab:10": OSError("offline")})  # type: ignore[dict-item]
        service = PoiHintService(remote, self.repository, refresh_after=timedelta(seconds=1))
        try:
            service.request(self.pois[0])
            for _ in range(100):
                result = service.poll()
                if result.state is HintState.OFFLINE:
                    break
                time.sleep(0.01)
            self.assertEqual(result.state, HintState.OFFLINE)
            self.assertEqual(result.hint.content, "offline copy")
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
