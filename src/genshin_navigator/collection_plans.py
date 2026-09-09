"""Transactional local collection plans. Missing POIs remain visible as IDs."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .position import CoordinateSpace


@dataclass(frozen=True)
class CollectionPlan:
    plan_id: str
    name: str
    region_id: str
    layer_id: str
    coordinate_space: CoordinateSpace
    poi_ids: tuple[str, ...]
    current_step: int = 0

    def __post_init__(self):
        if not self.plan_id or not self.name.strip() or len(self.name) > 120:
            raise ValueError("Plan needs an ID and a name of 1–120 characters")
        if len(self.poi_ids) > 10000 or len(set(self.poi_ids)) != len(self.poi_ids):
            raise ValueError("Duplicate POIs or oversized plan")
        if type(self.current_step) is not int or not 0 <= self.current_step <= len(self.poi_ids):
            raise ValueError("Invalid current step")

    @property
    def space_key(self):
        return self.region_id, self.layer_id, self.coordinate_space.value

    def to_dict(self):
        return dict(format_version=1, plan_id=self.plan_id, name=self.name,
                    region_id=self.region_id, layer_id=self.layer_id,
                    coordinate_space=self.coordinate_space.value,
                    poi_ids=list(self.poi_ids), current_step=self.current_step)

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, dict) or raw.get("format_version") != 1:
            raise ValueError("Unsupported collection plan")
        ids = raw.get("poi_ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
            raise ValueError("Invalid POI IDs")
        for key in ("plan_id", "name", "region_id", "layer_id"):
            if not isinstance(raw.get(key), str) or not raw[key].strip():
                raise ValueError("Invalid plan field: " + key)
        return cls(raw["plan_id"], raw["name"], raw["region_id"], raw["layer_id"],
                   CoordinateSpace(raw["coordinate_space"]), tuple(ids), raw.get("current_step", 0))


class CollectionPlanRepository:
    def __init__(self, database: str | Path):
        self.database = Path(database)

    def _connect(self):
        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 4:
            connection.close()
            raise ValueError("Collection plans require initialized SQLite schema v4")
        return connection

    def save(self, plan: CollectionPlan, *, allow_missing: bool = False):
        with closing(self._connect()) as db, db:
            for poi_id in plan.poi_ids:
                row = db.execute("SELECT region_id,layer_id,coordinate_space FROM pois WHERE poi_id=?", (poi_id,)).fetchone()
                if row is None and not allow_missing:
                    raise ValueError("Unknown POI: " + poi_id)
                if row is not None and row != plan.space_key:
                    raise ValueError("A collection plan cannot cross map spaces")
            db.execute("INSERT INTO collection_plans VALUES (?,?,?,?,?,?) ON CONFLICT(plan_id) DO UPDATE SET "
                       "name=excluded.name,region_id=excluded.region_id,layer_id=excluded.layer_id,"
                       "coordinate_space=excluded.coordinate_space,current_step=excluded.current_step",
                       (plan.plan_id, plan.name, *plan.space_key, plan.current_step))
            db.execute("DELETE FROM collection_plan_items WHERE plan_id=?", (plan.plan_id,))
            db.executemany("INSERT INTO collection_plan_items VALUES (?,?,?)",
                           [(plan.plan_id, index, poi) for index, poi in enumerate(plan.poi_ids)])

    def get(self, plan_id: str) -> CollectionPlan:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM collection_plans WHERE plan_id=?", (plan_id,)).fetchone()
            if row is None:
                raise KeyError(plan_id)
            ids = tuple(r[0] for r in db.execute("SELECT poi_id FROM collection_plan_items WHERE plan_id=? ORDER BY ordinal", (plan_id,)))
        return CollectionPlan(*row[:4], CoordinateSpace(row[4]), ids, row[5])

    def list(self, region_id: str) -> tuple[CollectionPlan, ...]:
        with closing(self._connect()) as db:
            ids = [r[0] for r in db.execute("SELECT plan_id FROM collection_plans WHERE region_id=? ORDER BY name,plan_id", (region_id,))]
        return tuple(self.get(i) for i in ids)

    def activate(self, region_id: str, plan_id: str | None):
        if plan_id is not None and self.get(plan_id).region_id != region_id:
            raise ValueError("Plan belongs to another region")
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM active_collection_plans WHERE region_id=?", (region_id,))
            if plan_id is not None:
                db.execute("INSERT INTO active_collection_plans VALUES (?,?)", (region_id,plan_id))

    def active(self, region_id):
        with closing(self._connect()) as db:
            row = db.execute("SELECT plan_id FROM active_collection_plans WHERE region_id=?", (region_id,)).fetchone()
        return self.get(row[0]) if row else None

    def import_preview(self, path: str | Path):
        path = Path(path)
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Plan exceeds 2 MiB")
        plan = CollectionPlan.from_dict(json.loads(path.read_text(encoding="utf-8")))
        with closing(self._connect()) as db:
            missing = []
            for poi_id in plan.poi_ids:
                row = db.execute("SELECT region_id,layer_id,coordinate_space FROM pois WHERE poi_id=?", (poi_id,)).fetchone()
                if row is None:
                    missing.append(poi_id)
                elif row != plan.space_key:
                    raise ValueError("Imported plan crosses map spaces")
        # Imported documents never overwrite an existing local plan by ID.
        from dataclasses import replace
        return replace(plan, plan_id=uuid4().hex), tuple(missing)

    def export(self, plan_id: str, path: str | Path):
        from .benchmark_suite import write_report_atomic
        write_report_atomic(self.get(plan_id).to_dict(), path)
