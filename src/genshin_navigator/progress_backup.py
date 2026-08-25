from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ProgressImportPlan:
    region_id: str
    collected_ids: tuple[str, ...]
    ignored_ids: tuple[str, ...]
    unknown_ids: tuple[str, ...]
    replace: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "region_id": self.region_id,
            "mode": "replace" if self.replace else "merge",
            "collected_count": len(self.collected_ids),
            "remote_ignored_count": len(self.ignored_ids),
            "unknown_count": len(self.unknown_ids),
            "unknown_ids": list(self.unknown_ids),
        }


class DatabaseBackupManager:
    def __init__(self, database: str | Path, backup_dir: str | Path, retention: int = 5):
        self.database = Path(database)
        self.backup_dir = Path(backup_dir)
        self.retention = retention
        if retention < 1:
            raise ValueError("Backup retention must be positive")

    def create(self, reason: str) -> Path:
        if not self.database.exists():
            raise FileNotFoundError(self.database)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        safe_reason = "".join(c if c.isalnum() or c in "-_" else "_" for c in reason)
        target = self.backup_dir / f"navigator_{stamp}_{safe_reason}.db"
        temporary = target.with_suffix(".db.tmp")
        with closing(sqlite3.connect(self.database)) as source, closing(
            sqlite3.connect(temporary)
        ) as destination:
            source.backup(destination)
        os.replace(temporary, target)
        backups = sorted(
            self.backup_dir.glob("navigator_*.db"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for stale in backups[self.retention :]:
            stale.unlink(missing_ok=True)
        return target


class ProgressTransferService:
    def __init__(
        self,
        database: str | Path,
        *,
        backup_dir: str | Path,
        backup_retention: int = 5,
    ):
        self.database = Path(database)
        self.backups = DatabaseBackupManager(
            self.database, backup_dir, backup_retention
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def export(self, output: str | Path, region_id: str) -> Path:
        with closing(self._connect()) as connection:
            snapshot = connection.execute(
                "SELECT content_version FROM content_snapshots WHERE region_id=?",
                (region_id,),
            ).fetchone()
            rows = connection.execute(
                "SELECT p.poi_id, p.collected, p.remote_ignored FROM progress p "
                "JOIN pois i ON i.poi_id=p.poi_id WHERE i.region_id=? "
                "AND (p.collected=1 OR p.remote_ignored=1) ORDER BY p.poi_id",
                (region_id,),
            ).fetchall()
        payload = {
            "format_version": 1,
            "region_id": region_id,
            "content_version": snapshot[0] if snapshot else None,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "collected_ids": [str(row[0]) for row in rows if bool(row[1])],
            "remote_ignored_ids": [str(row[0]) for row in rows if bool(row[2])],
        }
        destination = Path(output).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, destination)
        except Exception:
            Path(temporary_name).unlink(missing_ok=True)
            raise
        return destination

    def preview_import(
        self, source: str | Path, region_id: str, *, replace: bool = False
    ) -> ProgressImportPlan:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("format_version") != 1:
            raise ValueError("Unsupported progress export format")
        if str(payload.get("region_id")) != region_id:
            raise ValueError("Progress export region does not match requested region")
        collected = self._id_set(payload.get("collected_ids"), "collected_ids")
        ignored = self._id_set(
            payload.get("remote_ignored_ids"), "remote_ignored_ids"
        )
        if collected & ignored:
            raise ValueError("A POI cannot be both collected and remote-ignored")
        with closing(self._connect()) as connection:
            known = {
                str(row[0]) for row in connection.execute(
                    "SELECT poi_id FROM pois WHERE region_id=?", (region_id,)
                )
            }
        requested = collected | ignored
        return ProgressImportPlan(
            region_id,
            tuple(sorted(collected & known)),
            tuple(sorted(ignored & known)),
            tuple(sorted(requested - known)),
            replace,
        )

    @staticmethod
    def _id_set(value: object, name: str) -> set[str]:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"Progress export {name} must be a string list")
        return {item for item in value if item}

    def apply_import(self, plan: ProgressImportPlan) -> None:
        if plan.replace:
            self.backups.create("before_progress_replace")
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if plan.replace:
                connection.execute(
                    "DELETE FROM progress WHERE poi_id IN "
                    "(SELECT poi_id FROM pois WHERE region_id=?)",
                    (plan.region_id,),
                )
            for poi_id in plan.collected_ids:
                connection.execute(
                    "INSERT INTO progress(poi_id,collected,sync_state,updated_at,remote_ignored) "
                    "VALUES (?,1,'pending_push',?,0) ON CONFLICT(poi_id) DO UPDATE SET "
                    "collected=1,sync_state='pending_push',updated_at=excluded.updated_at,"
                    "remote_ignored=0",
                    (poi_id, now),
                )
            for poi_id in plan.ignored_ids:
                connection.execute(
                    "INSERT INTO progress(poi_id,collected,sync_state,updated_at,remote_ignored) "
                    "VALUES (?,0,'local',?,1) ON CONFLICT(poi_id) DO UPDATE SET "
                    "collected=0,sync_state='local',updated_at=excluded.updated_at,"
                    "remote_ignored=1",
                    (poi_id, now),
                )
