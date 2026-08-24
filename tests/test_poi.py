from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from genshin_navigator.poi import PoiCatalog, PoiProgress, PointOfInterest
from genshin_navigator.position import CoordinateSpace, MapPosition, PositionState


def position(layer: str, space: CoordinateSpace, x: float, y: float) -> MapPosition:
    return MapPosition(
        region_id="fontaine",
        layer_id=layer,
        coordinate_space=space,
        x=x,
        y=y,
        confidence=1.0,
        state=PositionState.TRACKING,
        timestamp=1.0,
    )


class PoiCatalogTests(unittest.TestCase):
    def test_nearest_never_crosses_map_layers(self) -> None:
        catalog = PoiCatalog(
            [
                PointOfInterest("surface", "chest", "Surface", "fontaine", "surface", CoordinateSpace.SURFACE_ATLAS, 11, 10),
                PointOfInterest("floor-a", "chest", "Floor A", "fontaine", "floor-a", CoordinateSpace.LAYER_LOCAL, 10, 10),
                PointOfInterest("floor-b", "chest", "Floor B", "fontaine", "floor-b", CoordinateSpace.LAYER_LOCAL, 10, 10),
            ]
        )

        nearest = catalog.nearest(
            position("floor-a", CoordinateSpace.LAYER_LOCAL, 12, 10), limit=3
        )

        self.assertEqual([item.id for item, _ in nearest], ["floor-a"])
        self.assertEqual(nearest[0][1], 2.0)

    def test_nearest_filters_kinds(self) -> None:
        catalog = PoiCatalog(
            [
                PointOfInterest("hydro", "hydroculus", "Hydroculus", "fontaine", "surface", CoordinateSpace.SURFACE_ATLAS, 1, 0),
                PointOfInterest("chest", "chest", "Chest", "fontaine", "surface", CoordinateSpace.SURFACE_ATLAS, 3, 0),
            ]
        )

        nearest = catalog.nearest(
            position("surface", CoordinateSpace.SURFACE_ATLAS, 0, 0),
            kinds={"chest"},
        )

        self.assertEqual(nearest[0][0].id, "chest")

    def test_progress_persists_and_excludes_collected_poi(self) -> None:
        catalog = PoiCatalog(
            [
                PointOfInterest("first", "chest", "First", "fontaine", "surface", CoordinateSpace.SURFACE_ATLAS, 1, 0),
                PointOfInterest("second", "chest", "Second", "fontaine", "surface", CoordinateSpace.SURFACE_ATLAS, 2, 0),
            ]
        )
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "progress.json"
            progress = PoiProgress.load(path)
            progress.mark_collected("first")
            restored = PoiProgress.load(path)
            nearest = catalog.nearest(
                position("surface", CoordinateSpace.SURFACE_ATLAS, 0, 0),
                exclude_ids=restored.collected_ids,
            )

        self.assertEqual(restored.collected_ids, {"first"})
        self.assertEqual(nearest[0][0].id, "second")
