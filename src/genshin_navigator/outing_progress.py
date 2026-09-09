"""Renewable collections never enter permanent progress or remote sync."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class OutingProgress:
    def __init__(self, database: str | Path):
        self.database = Path(database)
        self.collected_ids = self._load_ids()

    def _connect(self):
        db = sqlite3.connect(self.database)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _load_ids(self):
        with closing(self._connect()) as db:
            return {r[0] for r in db.execute("SELECT p.poi_id FROM outing_progress p JOIN outings o USING(outing_id) WHERE o.active=1")}

    def mark_collected(self, poi_id):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT i.region_id FROM pois i JOIN poi_categories c USING(kind) "
                             "WHERE i.poi_id=? AND c.progress_kind='outing' AND i.active=1", (poi_id,)).fetchone()
            if row is None:
                raise ValueError("POI is not an active renewable resource")
            region = row[0]
            active = db.execute("SELECT outing_id FROM outings WHERE region_id=? AND active=1", (region,)).fetchone()
            identifier = active[0] if active else uuid4().hex
            if active is None:
                db.execute("INSERT INTO outings VALUES (?,?,1,?)", (identifier, region, datetime.now(timezone.utc).isoformat()))
            db.execute("INSERT OR IGNORE INTO outing_progress VALUES (?,?)", (identifier, poi_id))
        self.collected_ids.add(poi_id)

    def unmark_collected(self, poi_id):
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM outing_progress WHERE poi_id=? AND outing_id IN "
                       "(SELECT outing_id FROM outings WHERE active=1)", (poi_id,))
        self.collected_ids.discard(poi_id)

    def reset(self, region_id):
        with closing(self._connect()) as db, db:
            db.execute("UPDATE outings SET active=0 WHERE region_id=? AND active=1", (region_id,))
        self.collected_ids = self._load_ids()


def catalog_statistics(database: str | Path, region_id: str):
    with closing(sqlite3.connect(database)) as db:
        rows = db.execute("SELECT i.layer_id,i.kind,c.progress_kind,COUNT(*),"
                          "SUM(CASE WHEN p.collected=1 THEN 1 ELSE 0 END) "
                          "FROM pois i JOIN poi_categories c USING(kind) LEFT JOIN progress p USING(poi_id) "
                          "WHERE i.region_id=? AND i.active=1 GROUP BY i.layer_id,i.kind,c.progress_kind", (region_id,))
        return [{"layer_id": layer, "kind": kind, "progress_kind": semantics,
                 "total": total, "collected": collected if semantics == "permanent" else None,
                 "remaining": total - collected if semantics == "permanent" else None}
                for layer, kind, semantics, total, collected in rows]
