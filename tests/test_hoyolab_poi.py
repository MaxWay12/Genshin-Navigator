from __future__ import annotations

import unittest

from genshin_navigator.hoyolab_poi import (
    build_catalog,
    build_space_metrics,
    content_version_for,
)
from genshin_navigator.position import CoordinateSpace


class HoyolabPoiTests(unittest.TestCase):
    def test_content_version_is_stable_and_changes_with_upstream_data(self) -> None:
        labels = [{"id": 17, "name": "Chest"}]
        first = content_version_for(labels, [{"id": 2}, {"id": 1}])
        reordered = content_version_for(labels, [{"id": 1}, {"id": 2}])
        changed = content_version_for(labels, [{"id": 1}, {"id": 3}])

        self.assertEqual(first, reordered)
        self.assertNotEqual(first, changed)
        self.assertEqual(
            content_version_for(labels, [], explicit_version="fixture"), "fixture"
        )

    def test_rejects_obsolete_vertical_surface_transform(self) -> None:
        surface = {
            "region_id": "fontaine",
            "atlas_size": [100, 100],
            "world_to_atlas": [[1, 0, 0], [0, -1, 50], [0, 0, 1]],
        }

        with self.assertRaisesRegex(ValueError, "vertically flipped"):
            build_catalog([], [], surface, {"groups": []})

    def test_build_catalog_converts_surface_and_floor_coordinates(self) -> None:
        points = [
            {"id": 1, "label_id": 17, "area_id": 8, "x_pos": 10, "y_pos": 20, "point_group": None},
            {"id": 2, "label_id": 508, "area_id": 8, "x_pos": 15, "y_pos": 27, "point_group": {"group_id": 1, "floor_id": 2}},
            {"id": 3, "label_id": 17, "area_id": 8, "x_pos": 1, "y_pos": 1, "point_group": {"group_id": 9, "floor_id": 9}},
            {"id": 4, "label_id": 17, "area_id": 1, "x_pos": 10, "y_pos": 20, "point_group": None},
        ]
        labels = [
            {"id": 17, "name": "Обычный сундук", "icon": "chest.png"},
            {"id": 508, "name": "Гидрокул", "icon": "hydro.png"},
        ]
        surface = {
            "region_id": "fontaine",
            "atlas_size": [100, 100],
            "world_to_atlas": [[2, 0, 1], [0, 3, 2], [0, 0, 1]],
        }
        underground = {
            "groups": [
                {
                    "group_id": 1,
                    "floors": [
                        {
                            "floor_id": 2,
                            "layer_id": "floor-a",
                            "image_size": [50, 50],
                            "local_to_world": [[1, 0, 10], [0, 1, 20], [0, 0, 1]],
                        }
                    ],
                }
            ]
        }

        pois, stats = build_catalog(points, labels, surface, underground)

        self.assertEqual(len(pois), 2)
        by_id = {poi.id: poi for poi in pois}
        self.assertEqual((by_id["hoyolab:1"].x, by_id["hoyolab:1"].y), (21.0, 62.0))
        self.assertIs(by_id["hoyolab:1"].coordinate_space, CoordinateSpace.SURFACE_ATLAS)
        self.assertEqual((by_id["hoyolab:2"].x, by_id["hoyolab:2"].y), (5.0, 7.0))
        self.assertEqual(by_id["hoyolab:2"].layer_id, "floor-a")
        self.assertEqual(stats["skipped_unknown_floor"], 1)

        metrics = build_space_metrics(surface, underground)
        by_layer = {metric.layer_id: metric for metric in metrics}
        self.assertEqual(by_layer["surface"].local_to_world, ((0.5, 0.0), (0.0, 1 / 3)))
        self.assertEqual(by_layer["floor-a"].local_to_world, ((1.0, 0.0), (0.0, 1.0)))


if __name__ == "__main__":
    unittest.main()
