from __future__ import annotations

import sqlite3


SCHEMA_VERSION = 3
SUPPORTED_SCHEMA_VERSIONS = (0, 1, 2, SCHEMA_VERSION)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS regions(region_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS map_spaces(
    region_id TEXT NOT NULL, layer_id TEXT NOT NULL,
    coordinate_space TEXT NOT NULL, matrix_json TEXT NOT NULL,
    PRIMARY KEY(region_id, layer_id, coordinate_space),
    FOREIGN KEY(region_id) REFERENCES regions(region_id)
);
CREATE TABLE IF NOT EXISTS pois(
    poi_id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
    region_id TEXT NOT NULL, layer_id TEXT NOT NULL,
    coordinate_space TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL,
    label_id INTEGER, icon_url TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    FOREIGN KEY(region_id, layer_id, coordinate_space)
        REFERENCES map_spaces(region_id, layer_id, coordinate_space)
);
CREATE INDEX IF NOT EXISTS pois_space_active
    ON pois(region_id, layer_id, coordinate_space, active);
CREATE TABLE IF NOT EXISTS progress(
    poi_id TEXT PRIMARY KEY,
    collected INTEGER NOT NULL CHECK(collected IN (0, 1)),
    sync_state TEXT NOT NULL DEFAULT 'local', updated_at TEXT NOT NULL,
    remote_ignored INTEGER NOT NULL DEFAULT 0 CHECK(remote_ignored IN (0, 1))
);
CREATE TABLE IF NOT EXISTS content_snapshots(
    region_id TEXT PRIMARY KEY, source TEXT NOT NULL, source_url TEXT NOT NULL,
    content_version TEXT NOT NULL, fetched_at TEXT NOT NULL,
    poi_count INTEGER NOT NULL, space_count INTEGER NOT NULL,
    FOREIGN KEY(region_id) REFERENCES regions(region_id)
);
CREATE TABLE IF NOT EXISTS assets(
    region_id TEXT NOT NULL, layer_id TEXT NOT NULL, kind TEXT NOT NULL,
    path TEXT NOT NULL, available INTEGER NOT NULL CHECK(available IN (0, 1)),
    PRIMARY KEY(region_id, layer_id, kind, path),
    FOREIGN KEY(region_id) REFERENCES regions(region_id)
);
CREATE TABLE IF NOT EXISTS poi_hints(
    poi_id TEXT PRIMARY KEY, content TEXT NOT NULL DEFAULT '', image_url TEXT,
    links_json TEXT NOT NULL DEFAULT '[]', video_url TEXT, source_updated_at TEXT,
    source TEXT NOT NULL, fetched_at TEXT NOT NULL,
    is_empty INTEGER NOT NULL DEFAULT 0 CHECK(is_empty IN (0, 1)),
    FOREIGN KEY(poi_id) REFERENCES pois(poi_id)
);
CREATE TABLE IF NOT EXISTS poi_hint_assets(
    poi_id TEXT PRIMARY KEY, relative_path TEXT NOT NULL, mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, fetched_at TEXT NOT NULL,
    last_accessed_at TEXT NOT NULL, FOREIGN KEY(poi_id) REFERENCES pois(poi_id)
);
CREATE INDEX IF NOT EXISTS poi_hint_assets_lru ON poi_hint_assets(last_accessed_at);
CREATE TABLE IF NOT EXISTS progress_sync_runs(
    sync_id INTEGER PRIMARY KEY AUTOINCREMENT, region_id TEXT NOT NULL,
    started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL,
    pull_count INTEGER NOT NULL DEFAULT 0, push_count INTEGER NOT NULL DEFAULT 0,
    unknown_count INTEGER NOT NULL DEFAULT 0, error_count INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(region_id) REFERENCES regions(region_id)
);
CREATE TABLE IF NOT EXISTS remote_progress_unknown(
    region_id TEXT NOT NULL, point_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    PRIMARY KEY(region_id, point_id), FOREIGN KEY(region_id) REFERENCES regions(region_id)
);
"""


def initialize_schema(connection: sqlite3.Connection) -> int:
    """Create or migrate the schema and return its previous version."""
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(
            f"Unsupported data schema version {version}; expected {SCHEMA_VERSION}"
        )
    connection.executescript(SCHEMA_SQL)
    progress_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(progress)")
    }
    if "remote_ignored" not in progress_columns:
        connection.execute(
            "ALTER TABLE progress ADD COLUMN remote_ignored "
            "INTEGER NOT NULL DEFAULT 0 CHECK(remote_ignored IN (0, 1))"
        )
    if version in (1, 2):
        connection.execute(
            "UPDATE progress SET sync_state = 'pending_push' "
            "WHERE collected = 1 AND sync_state = 'local'"
        )
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return version
