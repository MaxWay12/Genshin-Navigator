from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol


API_ROOT = "https://sg-public-api.hoyolab.com/common/map_user/ys_obc/v1/map/point"


class ProgressSyncError(RuntimeError):
    pass


class ProgressAuthError(ProgressSyncError):
    pass


class RemoteProgressProvider(Protocol):
    def marked_point_ids(self) -> set[str]: ...
    def add_mark(self, point_id: str) -> None: ...


@dataclass(frozen=True)
class SyncPlan:
    region_id: str
    pull_ids: tuple[str, ...]
    push_ids: tuple[str, ...]
    already_remote_ids: tuple[str, ...]
    unknown_remote_ids: tuple[str, ...]
    ignored_remote_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "region_id": self.region_id,
            "pull_count": len(self.pull_ids),
            "push_count": len(self.push_ids),
            "already_synced_count": len(self.already_remote_ids),
            "unknown_remote_count": len(self.unknown_remote_ids),
            "ignored_remote_count": len(self.ignored_remote_ids),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class SyncResult:
    region_id: str
    pulled_ids: tuple[str, ...]
    pushed_ids: tuple[str, ...]
    failed_push_ids: tuple[str, ...]
    unknown_remote_ids: tuple[str, ...]
    completed_at: str

    @property
    def status(self) -> str:
        return "partial" if self.failed_push_ids else "success"

    def to_dict(self) -> dict[str, object]:
        return {
            "region_id": self.region_id,
            "status": self.status,
            "pulled_count": len(self.pulled_ids),
            "pushed_count": len(self.pushed_ids),
            "failed_push_count": len(self.failed_push_ids),
            "unknown_remote_count": len(self.unknown_remote_ids),
            "failed_push_ids": list(self.failed_push_ids),
            "completed_at": self.completed_at,
        }


class HoyoLabRemoteProgressProvider:
    def __init__(
        self,
        cookie_header: str,
        *,
        map_id: int = 2,
        lang: str = "ru-ru",
        timeout_seconds: float = 8.0,
        retry_count: int = 1,
        min_write_interval_seconds: float = 0.15,
        opener: Callable[..., object] = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if not cookie_header:
            raise ProgressAuthError("HoYoLAB session has no authentication cookies")
        self.cookie_header = cookie_header
        self.map_id = map_id
        self.lang = lang
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self.min_write_interval_seconds = min_write_interval_seconds
        self.opener = opener
        self.sleeper = sleeper
        self._last_write = 0.0

    def _request(self, path: str, *, body: dict[str, object] | None = None) -> dict:
        params = urllib.parse.urlencode(
            {"map_id": self.map_id, "app_sn": "ys_obc", "lang": self.lang}
        )
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"{API_ROOT}/{path}?{params}",
            data=data,
            method="POST" if body is not None else "GET",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Cookie": self.cookie_header,
                "Referer": "https://act.hoyolab.com/",
                "User-Agent": "GenshinNavigator/0.1",
            },
        )
        for attempt in range(self.retry_count + 1):
            try:
                response = self.opener(request, timeout=self.timeout_seconds)
                raw = response.read()
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ProgressSyncError("HoYoLAB returned an invalid progress response")
                retcode = int(payload.get("retcode", -1))
                if retcode != 0:
                    message = str(payload.get("message") or "HoYoLAB request failed")
                    if retcode in (-100, -101, -1071, 10001):
                        raise ProgressAuthError(
                            "HoYoLAB session expired; run hoyolab-login again"
                        )
                    raise ProgressSyncError(f"HoYoLAB error {retcode}: {message}")
                return payload
            except urllib.error.HTTPError as error:
                if error.code in (401, 403):
                    raise ProgressAuthError(
                        "HoYoLAB session expired; run hoyolab-login again"
                    ) from error
                if error.code < 500 or attempt >= self.retry_count:
                    raise ProgressSyncError(f"HoYoLAB HTTP error {error.code}") from error
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                if attempt >= self.retry_count:
                    raise ProgressSyncError("HoYoLAB progress request failed") from error
            self.sleeper(0.35 * (attempt + 1))
        raise AssertionError("unreachable")

    def marked_point_ids(self) -> set[str]:
        payload = self._request("mark_map_point_list")
        data = payload.get("data")
        rows = data.get("list") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ProgressSyncError("HoYoLAB progress response has no point list")
        result: set[str] = set()
        for row in rows:
            point_id = row.get("point_id") if isinstance(row, dict) else row
            if point_id is not None and str(point_id).strip():
                result.add(str(point_id))
        return result

    def add_mark(self, point_id: str) -> None:
        elapsed = time.monotonic() - self._last_write
        if elapsed < self.min_write_interval_seconds:
            self.sleeper(self.min_write_interval_seconds - elapsed)
        self._request(
            "add_mark_map_point",
            body={
                "map_id": self.map_id,
                "point_id": int(point_id) if point_id.isdigit() else point_id,
                "app_sn": "ys_obc",
                "lang": self.lang,
            },
        )
        self._last_write = time.monotonic()


class SqliteProgressSyncStore:
    def __init__(self, database: str | Path):
        self.database = Path(database)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def snapshot(
        self, region_id: str
    ) -> tuple[set[str], dict[str, tuple[bool, str, bool]]]:
        with closing(self._connect()) as connection:
            known = {
                str(row[0])
                for row in connection.execute(
                    "SELECT poi_id FROM pois WHERE region_id = ?", (region_id,)
                )
            }
            progress = {
                str(row["poi_id"]): (
                    bool(row["collected"]),
                    str(row["sync_state"]),
                    bool(row["remote_ignored"]),
                )
                for row in connection.execute(
                    "SELECT p.* FROM progress p JOIN pois i ON i.poi_id=p.poi_id "
                    "WHERE i.region_id = ?", (region_id,)
                )
            }
        return known, progress

    def apply_remote_state(self, plan: SyncPlan, *, started_at: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            for poi_id in plan.pull_ids:
                connection.execute(
                    """
                    INSERT INTO progress(
                        poi_id, collected, sync_state, updated_at, remote_ignored
                    ) VALUES (?, 1, 'synced', ?, 0)
                    ON CONFLICT(poi_id) DO UPDATE SET
                        collected=1, sync_state='synced', updated_at=excluded.updated_at,
                        remote_ignored=0
                    """,
                    (poi_id, now),
                )
            for poi_id in plan.already_remote_ids:
                connection.execute(
                    "UPDATE progress SET sync_state='synced' "
                    "WHERE poi_id=? AND collected=1", (poi_id,)
                )
            for point_id in plan.unknown_remote_ids:
                connection.execute(
                    """
                    INSERT INTO remote_progress_unknown VALUES (?, ?, ?, ?)
                    ON CONFLICT(region_id, point_id) DO UPDATE SET
                        last_seen_at=excluded.last_seen_at
                    """,
                    (plan.region_id, point_id, now, now),
                )
            cursor = connection.execute(
                """
                INSERT INTO progress_sync_runs(
                    region_id, started_at, status, pull_count, push_count,
                    unknown_count, error_count, summary_json
                ) VALUES (?, ?, 'running', ?, 0, ?, 0, '{}')
                """,
                (plan.region_id, started_at, len(plan.pull_ids), len(plan.unknown_remote_ids)),
            )
            return int(cursor.lastrowid)

    def record_push(self, poi_id: str, *, success: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE progress SET sync_state=?, updated_at=? WHERE poi_id=?",
                ("synced" if success else "sync_error", now, poi_id),
            )

    def finish_run(self, sync_id: int, result: SyncResult) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE progress_sync_runs SET completed_at=?, status=?, push_count=?,
                    error_count=?, summary_json=? WHERE sync_id=?
                """,
                (
                    result.completed_at,
                    result.status,
                    len(result.pushed_ids),
                    len(result.failed_push_ids),
                    json.dumps(result.to_dict(), ensure_ascii=False),
                    sync_id,
                ),
            )


class ProgressSyncService:
    def __init__(
        self, store: SqliteProgressSyncStore, remote: RemoteProgressProvider
    ):
        self.store = store
        self.remote = remote

    def preview(self, region_id: str = "fontaine") -> SyncPlan:
        known, progress = self.store.snapshot(region_id)
        remote_raw = self.remote.marked_point_ids()
        remote = {f"hoyolab:{point_id}" for point_id in remote_raw}
        known_remote = remote & known
        ignored = {
            poi_id for poi_id in known_remote
            if progress.get(poi_id, (False, "local", False))[2]
        }
        collected = {
            poi_id for poi_id, (value, _, _) in progress.items() if value
        }
        pull = known_remote - collected - ignored
        already = known_remote & collected
        push = {
            poi_id for poi_id in collected - remote
            if poi_id.startswith("hoyolab:")
            and progress[poi_id][1] in {"local", "pending_push", "sync_error"}
        }
        unknown = remote_raw - {
            poi_id.removeprefix("hoyolab:") for poi_id in known_remote
        }
        unpushable = collected - {item for item in collected if item.startswith("hoyolab:")}
        warnings = (
            (f"{len(unpushable)} local POI have no HoYoLAB point id",)
            if unpushable else ()
        )
        return SyncPlan(
            region_id,
            tuple(sorted(pull)),
            tuple(sorted(push)),
            tuple(sorted(already)),
            tuple(sorted(unknown)),
            tuple(sorted(ignored)),
            warnings,
        )

    def apply(self, plan: SyncPlan) -> SyncResult:
        started_at = datetime.now(timezone.utc).isoformat()
        sync_id = self.store.apply_remote_state(plan, started_at=started_at)
        pushed: list[str] = []
        failed: list[str] = []
        for poi_id in plan.push_ids:
            try:
                self.remote.add_mark(poi_id.removeprefix("hoyolab:"))
            except ProgressSyncError:
                failed.append(poi_id)
                self.store.record_push(poi_id, success=False)
            else:
                pushed.append(poi_id)
                self.store.record_push(poi_id, success=True)
        result = SyncResult(
            plan.region_id,
            plan.pull_ids,
            tuple(pushed),
            tuple(failed),
            plan.unknown_remote_ids,
            datetime.now(timezone.utc).isoformat(),
        )
        self.store.finish_run(sync_id, result)
        return result
