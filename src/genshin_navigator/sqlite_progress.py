from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


class SqlitePoiProgress:
    """SQLite-backed local progress repository used by Navigation."""

    def __init__(self, database: Path):
        self.database = database
        self.collected_ids = self._load()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _load(self) -> set[str]:
        with closing(self._connect()) as connection, connection:
            return {
                str(row[0])
                for row in connection.execute(
                    "SELECT poi_id FROM progress WHERE collected = 1"
                )
            }

    def mark_collected(self, poi_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO progress(poi_id, collected, sync_state, updated_at)
                VALUES (?, 1, 'pending_push', ?)
                ON CONFLICT(poi_id) DO UPDATE SET
                    collected = 1, sync_state = 'pending_push',
                    updated_at = excluded.updated_at, remote_ignored = 0
                """,
                (poi_id, now),
            )
        self.collected_ids.add(poi_id)

    def unmark_collected(self, poi_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT sync_state FROM progress WHERE poi_id = ?", (poi_id,)
            ).fetchone()
            remote_ignored = int(bool(row and str(row[0]) == "synced"))
            connection.execute(
                """
                INSERT INTO progress(
                    poi_id, collected, sync_state, updated_at, remote_ignored
                )
                VALUES (?, 0, 'local', ?, ?)
                ON CONFLICT(poi_id) DO UPDATE SET
                    collected = 0, sync_state = 'local',
                    updated_at = excluded.updated_at,
                    remote_ignored = excluded.remote_ignored
                """,
                (poi_id, now, remote_ignored),
            )
        self.collected_ids.discard(poi_id)
