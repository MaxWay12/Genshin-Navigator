from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from genshin_navigator.poi import MapSpaceMetric, PoiCatalog, PoiProgress, PointOfInterest
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
    def test_legacy_catalog_loads_without_metrics(self) -> None:
        item = PointOfInterest(
            "legacy", "chest", "Legacy", "fontaine", "surface",
            CoordinateSpace.SURFACE_ATLAS, 3, 4,
        )
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "legacy.json"
            path.write_text(
                json.dumps({"format_version": 1, "pois": [item.to_dict()]}),
                encoding="utf-8",
            )
            catalog = PoiCatalog.load(path)

        self.assertEqual(catalog.pois[0].id, "legacy")
        self.assertEqual(catalog.metrics, ())

    def test_surface_metric_converts_atlas_delta_to_world_units(self) -> None:
        metric = MapSpaceMetric(
            "fontaine", "surface", CoordinateSpace.SURFACE_ATLAS,
            ((2.0, 0.0), (0.0, 2.0)),
        )
        target = PointOfInterest(
            "target", "chest", "Target", "fontaine", "surface",
            CoordinateSpace.SURFACE_ATLAS, 3, 4,
        )
        catalog = PoiCatalog([target], [metric])

        distance = catalog.world_distance(
            position("surface", CoordinateSpace.SURFACE_ATLAS, 0, 0), target
        )

        self.assertEqual(distance, 10.0)

    def test_non_uniform_floor_metric_uses_both_axes(self) -> None:
        metric = MapSpaceMetric(
            "fontaine", "floor78", CoordinateSpace.LAYER_LOCAL,
            ((2.0, 0.0), (0.0, 3.0)),
        )
        target = PointOfInterest(
            "target", "chest", "Target", "fontaine", "floor78",
            CoordinateSpace.LAYER_LOCAL, 3, 4,
        )
        catalog = PoiCatalog([target], [metric])

        distance = catalog.world_distance(
            position("floor78", CoordinateSpace.LAYER_LOCAL, 0, 0), target
        )

        self.assertAlmostEqual(distance or 0.0, (6**2 + 12**2) ** 0.5)

    def test_missing_metric_returns_none_instead_of_map_pixels(self) -> None:
        target = PointOfInterest(
            "target", "chest", "Target", "fontaine", "surface",
            CoordinateSpace.SURFACE_ATLAS, 3, 4,
        )
        catalog = PoiCatalog([target])

        self.assertIsNone(
            catalog.world_distance(
                position("surface", CoordinateSpace.SURFACE_ATLAS, 0, 0), target
            )
        )

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

    def test_progress_can_undo_collected_atomically(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "progress.json"
            progress = PoiProgress.load(path)
            progress.mark_collected("first")
            progress.unmark_collected("first")
            restored = PoiProgress.load(path)

        self.assertEqual(restored.collected_ids, set())
