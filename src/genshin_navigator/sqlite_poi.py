from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .poi import MapSpaceMetric, PoiCatalog, PointOfInterest
from .position import CoordinateSpace


def load_poi_catalog(database: Path, region_id: str) -> PoiCatalog:
    """Read a normalized POI snapshot without exposing SQLite to Navigation."""
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    with closing(connection):
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
    metrics: list[MapSpaceMetric] = []
    for row in metric_rows:
        matrix = json.loads(row["matrix_json"])
        if matrix is not None:
            metrics.append(
                MapSpaceMetric.from_dict(
                    {
                        "region_id": row["region_id"],
                        "layer_id": row["layer_id"],
                        "coordinate_space": row["coordinate_space"],
                        "local_to_world": matrix,
                    }
                )
            )
    return PoiCatalog(pois, metrics)
