from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from genshin_navigator.data_store import SqliteDataProvider
from genshin_navigator.poi import MapSpaceMetric, PointOfInterest
from genshin_navigator.position import CoordinateSpace
from genshin_navigator.progress_sync import (
    HoyoLabRemoteProgressProvider,
    ProgressAuthError,
    ProgressSyncError,
    ProgressSyncService,
    SqliteProgressSyncStore,
)


def poi(identifier: str) -> PointOfInterest:
    return PointOfInterest(
        identifier, "chest", identifier, "fontaine", "surface",
        CoordinateSpace.SURFACE_ATLAS, 10.0, 20.0,
    )


def metric() -> MapSpaceMetric:
    return MapSpaceMetric(
        "fontaine", "surface", CoordinateSpace.SURFACE_ATLAS,
        ((1.0, 0.0), (0.0, 1.0)),
    )


class FakeRemote:
    def __init__(self, marked=(), fail=()):
        self.marked = set(marked)
        self.fail = set(fail)
        self.added = []

    def marked_point_ids(self):
        return set(self.marked)

    def add_mark(self, point_id):
        if point_id in self.fail:
            raise ProgressSyncError("fixture failure")
        self.added.append(point_id)
        self.marked.add(point_id)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()


class ProgressSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.database = Path(self.temporary.name) / "data.db"
        self.provider = SqliteDataProvider(self.database)
        self.provider.replace_content(
            "fontaine",
            [poi("hoyolab:1"), poi("hoyolab:2"), poi("local-only")],
            [metric()],
            content_version="fixture",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def service(self, remote):
        return ProgressSyncService(SqliteProgressSyncStore(self.database), remote)

    def test_remote_provider_parses_marked_point_list(self):
        payload = {
            "retcode": 0,
            "data": {"list": [{"point_id": 1}, {"point_id": "2"}]},
        }
        provider = HoyoLabRemoteProgressProvider(
            "ltoken_v2=secret", opener=lambda *args, **kwargs: Response(payload),
            sleeper=lambda _: None,
        )

        self.assertEqual(provider.marked_point_ids(), {"1", "2"})

    def test_remote_provider_reports_expired_auth_without_leaking_cookie(self):
        provider = HoyoLabRemoteProgressProvider(
            "ltoken_v2=do-not-print",
            opener=lambda *args, **kwargs: Response(
                {"retcode": -100, "message": "Please login", "data": None}
            ),
            sleeper=lambda _: None,
        )

        with self.assertRaisesRegex(ProgressAuthError, "session expired") as raised:
            provider.marked_point_ids()
        self.assertNotIn("do-not-print", str(raised.exception))

    def test_preview_then_additive_sync_is_idempotent(self):
        self.provider.progress().mark_collected("hoyolab:2")
        remote = FakeRemote({"1", "999"})
        service = self.service(remote)

        plan = service.preview()
        before = self.provider.progress().collected_ids
        result = service.apply(plan)
        repeated = service.preview()

        self.assertEqual(before, {"hoyolab:2"})
        self.assertEqual(plan.pull_ids, ("hoyolab:1",))
        self.assertEqual(plan.push_ids, ("hoyolab:2",))
        self.assertEqual(plan.unknown_remote_ids, ("999",))
        self.assertEqual(result.status, "success")
        self.assertEqual(self.provider.progress().collected_ids, {
            "hoyolab:1", "hoyolab:2",
        })
        self.assertEqual(repeated.pull_ids, ())
        self.assertEqual(repeated.push_ids, ())

    def test_failed_push_remains_retryable(self):
        self.provider.progress().mark_collected("hoyolab:2")
        remote = FakeRemote(fail={"2"})
        service = self.service(remote)

        result = service.apply(service.preview())
        retry = service.preview()

        self.assertEqual(result.failed_push_ids, ("hoyolab:2",))
        self.assertEqual(retry.push_ids, ("hoyolab:2",))
        self.assertEqual(self.provider.status()["sync_error_count"], 1)

    def test_undo_after_sync_is_not_reimported_or_removed_remotely(self):
        remote = FakeRemote({"1"})
        service = self.service(remote)
        service.apply(service.preview())

        self.provider.progress().unmark_collected("hoyolab:1")
        plan = service.preview()

        self.assertEqual(plan.pull_ids, ())
        self.assertEqual(plan.ignored_remote_ids, ("hoyolab:1",))
        self.assertNotIn("hoyolab:1", self.provider.progress().collected_ids)

    def test_inactive_known_poi_keeps_remote_progress(self):
        self.provider.replace_content(
            "fontaine", [poi("hoyolab:2")], [metric()], content_version="next"
        )

        plan = self.service(FakeRemote({"1"})).preview()

        self.assertEqual(plan.pull_ids, ("hoyolab:1",))
        self.assertEqual(plan.unknown_remote_ids, ())

    def test_corrupt_remote_response_does_not_mutate_local_progress(self):
        remote = HoyoLabRemoteProgressProvider(
            "ltoken_v2=secret",
            opener=lambda *args, **kwargs: Response({"retcode": 0, "data": {}}),
            sleeper=lambda _: None,
        )

        with self.assertRaises(ProgressSyncError):
            self.service(remote).preview()
        self.assertEqual(self.provider.progress().collected_ids, set())
        with closing(sqlite3.connect(self.database)) as connection:
            runs = connection.execute(
                "SELECT COUNT(*) FROM progress_sync_runs"
            ).fetchone()[0]
        self.assertEqual(runs, 0)


if __name__ == "__main__":
    unittest.main()
