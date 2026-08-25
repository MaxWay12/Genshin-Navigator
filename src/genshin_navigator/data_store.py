from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .poi import MapSpaceMetric, PoiCatalog, PoiProgress, PointOfInterest
from .position import CoordinateSpace
from .storage_schema import SCHEMA_VERSION, initialize_schema


SOURCE_NAME = "HoYoLAB Interactive Map"
SOURCE_URL = "https://act.hoyolab.com/ys/app/interactive-map/index.html#/map/2"


@dataclass(frozen=True)
class AssetRecord:
    region_id: str
    layer_id: str
    kind: str
    path: Path


@dataclass(frozen=True)
class DataBundle:
    catalog: PoiCatalog
    progress: PoiProgress | SqlitePoiProgress  # type: ignore[name-defined]
    backend: str
    provider: SqliteDataProvider | None = None  # type: ignore[name-defined]


class SqlitePoiProgress:
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


class SqliteDataProvider:
    """Offline-first normalized data store. Network access never happens here."""

    def __init__(self, database: str | Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            initialize_schema(connection)

    def is_empty(self, region_id: str | None = None) -> bool:
        query = "SELECT COUNT(*) FROM pois"
        params: tuple[object, ...] = ()
        if region_id is not None:
            query += " WHERE region_id = ?"
            params = (region_id,)
        with closing(self._connect()) as connection, connection:
            return int(connection.execute(query, params).fetchone()[0]) == 0

    def catalog(self, region_id: str = "fontaine") -> PoiCatalog:
        with closing(self._connect()) as connection, connection:
            poi_rows = connection.execute(
                "SELECT * FROM pois WHERE region_id = ? AND active = 1 ORDER BY poi_id",
                (region_id,),
            ).fetchall()
            metric_rows = connection.execute(
                "SELECT * FROM map_spaces WHERE region_id = ? ORDER BY layer_id",
                (region_id,),
            ).fetchall()
        pois = [
            PointOfInterest(
                id=str(row["poi_id"]), kind=str(row["kind"]), name=str(row["name"]),
                region_id=str(row["region_id"]), layer_id=str(row["layer_id"]),
                coordinate_space=CoordinateSpace(str(row["coordinate_space"])),
                x=float(row["x"]), y=float(row["y"]),
                label_id=int(row["label_id"]) if row["label_id"] is not None else None,
                icon_url=str(row["icon_url"]) if row["icon_url"] else None,
            )
            for row in poi_rows
        ]
        metrics = []
        for row in metric_rows:
            matrix = json.loads(row["matrix_json"])
            if matrix is None:
                continue
            metrics.append(
                MapSpaceMetric.from_dict(
                    {
                        "region_id": row["region_id"], "layer_id": row["layer_id"],
                        "coordinate_space": row["coordinate_space"],
                        "local_to_world": matrix,
                    }
                )
            )
        return PoiCatalog(pois, metrics)

    def progress(self) -> SqlitePoiProgress:
        return SqlitePoiProgress(self.database)

    def import_legacy(
        self,
        catalog_path: str | Path,
        progress_path: str | Path | None = None,
        *,
        region_id: str = "fontaine",
    ) -> None:
        path = Path(catalog_path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        catalog = PoiCatalog.load(path)
        progress = PoiProgress.load(progress_path) if progress_path is not None else None
        version = str(raw.get("map_version") or raw.get("content_version") or "legacy-v1")
        self.replace_content(
            region_id, catalog.pois, catalog.metrics, content_version=version,
            source=str(raw.get("source") or SOURCE_NAME),
            source_url=str(raw.get("source_url") or SOURCE_URL),
            allow_unmeasured_spaces=True,
        )
        if progress is not None:
            sqlite_progress = self.progress()
            for poi_id in progress.collected_ids:
                sqlite_progress.mark_collected(poi_id)

    def replace_content(
        self,
        region_id: str,
        pois: Iterable[PointOfInterest],
        metrics: Iterable[MapSpaceMetric],
        *,
        content_version: str,
        source: str = SOURCE_NAME,
        source_url: str = SOURCE_URL,
        assets: Iterable[AssetRecord] = (),
        fetched_at: str | None = None,
        allow_unmeasured_spaces: bool = False,
    ) -> None:
        poi_items, metric_items, asset_items = tuple(pois), tuple(metrics), tuple(assets)
        metric_keys = {metric.key for metric in metric_items}
        poi_keys = {
            (poi.region_id, poi.layer_id, poi.coordinate_space) for poi in poi_items
        }
        keys = metric_keys | (poi_keys if allow_unmeasured_spaces else set())
        if not keys:
            raise ValueError("Content update contains no map spaces")
        for metric in metric_items:
            if metric.region_id != region_id:
                raise ValueError("Map-space region does not match sync region")
        for poi in poi_items:
            key = (poi.region_id, poi.layer_id, poi.coordinate_space)
            if poi.region_id != region_id or key not in keys:
                raise ValueError(f"POI {poi.id} references an unknown map space")
        timestamp = fetched_at or datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT OR IGNORE INTO regions VALUES (?)", (region_id,))
            connection.executemany(
                """
                INSERT INTO map_spaces VALUES (?, ?, ?, ?)
                ON CONFLICT(region_id, layer_id, coordinate_space) DO UPDATE SET
                    matrix_json=excluded.matrix_json
                """,
                [
                    (metric.region_id, metric.layer_id, metric.coordinate_space.value,
                     json.dumps([list(row) for row in metric.local_to_world]))
                    for metric in metric_items
                ],
            )
            missing_metrics = keys - metric_keys
            connection.executemany(
                """
                INSERT INTO map_spaces VALUES (?, ?, ?, 'null')
                ON CONFLICT(region_id, layer_id, coordinate_space) DO NOTHING
                """,
                [
                    (key[0], key[1], key[2].value)
                    for key in sorted(missing_metrics, key=lambda item: (item[0], item[1], item[2].value))
                ],
            )
            connection.execute("UPDATE pois SET active = 0 WHERE region_id = ?", (region_id,))
            connection.executemany(
                """
                INSERT INTO pois VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(poi_id) DO UPDATE SET
                    kind=excluded.kind, name=excluded.name, region_id=excluded.region_id,
                    layer_id=excluded.layer_id, coordinate_space=excluded.coordinate_space,
                    x=excluded.x, y=excluded.y, label_id=excluded.label_id,
                    icon_url=excluded.icon_url, active=1
                """,
                [
                    (poi.id, poi.kind, poi.name, poi.region_id, poi.layer_id,
                     poi.coordinate_space.value, poi.x, poi.y, poi.label_id, poi.icon_url)
                    for poi in poi_items
                ],
            )
            connection.execute("DELETE FROM assets WHERE region_id = ?", (region_id,))
            connection.executemany(
                "INSERT INTO assets VALUES (?, ?, ?, ?, ?)",
                [
                    (asset.region_id, asset.layer_id, asset.kind,
                     os.path.relpath(asset.path.resolve(), self.database.parent.resolve()),
                     int(asset.path.exists()))
                    for asset in asset_items
                ],
            )
            connection.execute(
                """
                INSERT INTO content_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(region_id) DO UPDATE SET
                    source=excluded.source, source_url=excluded.source_url,
                    content_version=excluded.content_version, fetched_at=excluded.fetched_at,
                    poi_count=excluded.poi_count, space_count=excluded.space_count
                """,
                (region_id, source, source_url, content_version, timestamp,
                 len(poi_items), len(keys)),
            )

    def status(
        self, region_id: str = "fontaine", *, hint_refresh_after_days: float = 7.0
    ) -> dict[str, object]:
        with closing(self._connect()) as connection, connection:
            snapshot = connection.execute(
                "SELECT * FROM content_snapshots WHERE region_id = ?", (region_id,)
            ).fetchone()
            active = int(connection.execute(
                "SELECT COUNT(*) FROM pois WHERE region_id = ? AND active = 1", (region_id,)
            ).fetchone()[0])
            inactive = int(connection.execute(
                "SELECT COUNT(*) FROM pois WHERE region_id = ? AND active = 0", (region_id,)
            ).fetchone()[0])
            collected = int(connection.execute(
                "SELECT COUNT(*) FROM progress WHERE collected = 1"
            ).fetchone()[0])
            pending_sync = int(connection.execute(
                "SELECT COUNT(*) FROM progress WHERE collected = 1 "
                "AND sync_state IN ('local', 'pending_push', 'sync_error')"
            ).fetchone()[0])
            sync_errors = int(connection.execute(
                "SELECT COUNT(*) FROM progress WHERE sync_state = 'sync_error'"
            ).fetchone()[0])
            remote_ignored = int(connection.execute(
                "SELECT COUNT(*) FROM progress WHERE remote_ignored = 1"
            ).fetchone()[0])
            last_sync = connection.execute(
                "SELECT completed_at, status, pull_count, push_count, unknown_count, "
                "error_count FROM progress_sync_runs WHERE region_id = ? "
                "ORDER BY sync_id DESC LIMIT 1", (region_id,)
            ).fetchone()
            asset_rows = connection.execute(
                "SELECT path FROM assets WHERE region_id = ?", (region_id,)
            ).fetchall()
            hint_count = int(connection.execute(
                "SELECT COUNT(*) FROM poi_hints h JOIN pois p ON p.poi_id=h.poi_id WHERE p.region_id = ?",
                (region_id,),
            ).fetchone()[0])
            hint_image_count = int(connection.execute(
                "SELECT COUNT(*) FROM poi_hint_assets a JOIN pois p ON p.poi_id=a.poi_id WHERE p.region_id = ?",
                (region_id,),
            ).fetchone()[0])
            hint_cache_bytes = int(connection.execute(
                "SELECT COALESCE(SUM(a.size_bytes), 0) FROM poi_hint_assets a JOIN pois p ON p.poi_id=a.poi_id WHERE p.region_id = ?",
                (region_id,),
            ).fetchone()[0])
            stale_before = (
                datetime.now(timezone.utc) - timedelta(days=hint_refresh_after_days)
            ).isoformat()
            stale_hint_count = int(connection.execute(
                "SELECT COUNT(*) FROM poi_hints h JOIN pois p ON p.poi_id=h.poi_id WHERE p.region_id = ? AND h.fetched_at < ?",
                (region_id, stale_before),
            ).fetchone()[0])
        asset_paths = [str(row["path"]) for row in asset_rows]
        missing_assets = []
        for stored_path in asset_paths:
            path = Path(stored_path)
            resolved = path if path.is_absolute() else self.database.parent / path
            if not resolved.exists():
                missing_assets.append(stored_path)
        return {
            "schema_version": SCHEMA_VERSION,
            "region_id": region_id,
            "content_version": snapshot["content_version"] if snapshot else None,
            "fetched_at": snapshot["fetched_at"] if snapshot else None,
            "poi_count": active,
            "inactive_poi_count": inactive,
            "space_count": int(snapshot["space_count"]) if snapshot else 0,
            "collected_count": collected,
            "pending_sync_count": pending_sync,
            "sync_error_count": sync_errors,
            "remote_ignored_count": remote_ignored,
            "last_progress_sync": dict(last_sync) if last_sync else None,
            "asset_count": len(asset_paths),
            "missing_asset_count": len(missing_assets),
            "missing_assets": missing_assets,
            "cached_hint_count": hint_count,
            "cached_hint_image_count": hint_image_count,
            "hint_cache_bytes": hint_cache_bytes,
            "stale_hint_count": stale_hint_count,
        }


def open_data_bundle(
    *,
    backend: str,
    database_path: str | Path,
    catalog_path: str | Path | None,
    progress_path: str | Path,
    region_id: str = "fontaine",
) -> DataBundle:
    """Open runtime data without performing network I/O."""
    if backend == "json":
        if catalog_path is None:
            raise ValueError("JSON backend requires poi.catalog_path")
        return DataBundle(
            PoiCatalog.load(catalog_path), PoiProgress.load(progress_path), "json"
        )
    provider = SqliteDataProvider(database_path)
    if provider.is_empty(region_id):
        if backend == "sqlite":
            raise ValueError(
                "SQLite data store is empty; run sync-data or use storage_backend=auto"
            )
        if catalog_path is None or not Path(catalog_path).exists():
            raise ValueError(
                "No SQLite content or legacy POI catalog; run sync-data first"
            )
        provider.import_legacy(catalog_path, progress_path, region_id=region_id)
    return DataBundle(provider.catalog(region_id), provider.progress(), "sqlite", provider)


def collect_assets(
    region_id: str,
    surface_metadata_path: str | Path,
    underground_metadata_path: str | Path,
    *,
    pyramid_path: str | Path | None = None,
) -> list[AssetRecord]:
    surface_path = Path(surface_metadata_path)
    underground_path = Path(underground_metadata_path)
    underground = json.loads(underground_path.read_text(encoding="utf-8"))
    assets = [
        AssetRecord(region_id, "surface", "atlas", surface_path.parent / "atlas.png"),
        AssetRecord(region_id, "surface", "metadata", surface_path),
        AssetRecord(region_id, "surface", "underground_metadata", underground_path),
    ]
    if pyramid_path is not None:
        assets.append(AssetRecord(region_id, "surface", "pyramid", Path(pyramid_path)))
    for group in underground.get("groups", []):
        for floor in group.get("floors", []):
            assets.append(
                AssetRecord(
                    region_id,
                    str(floor["layer_id"]),
                    "layer_map",
                    underground_path.parent / str(floor["path"]),
                )
            )
    return assets
