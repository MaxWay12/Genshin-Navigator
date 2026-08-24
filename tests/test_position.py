from __future__ import annotations

import unittest

from genshin_navigator.position import CoordinateSpace, MapPosition, PositionState


class MapPositionTests(unittest.TestCase):
    def test_serializes_public_position_contract(self) -> None:
        position = MapPosition(
            region_id="fontaine",
            layer_id="underground:map2:group90:floor78",
            coordinate_space=CoordinateSpace.LAYER_LOCAL,
            x=732.19,
            y=2511.86,
            confidence=0.93,
            state=PositionState.TRACKING,
            timestamp=12.5,
            reference_id="fontaine_ug_g90_f78",
        )

        self.assertEqual(
            position.to_dict(),
            {
                "region_id": "fontaine",
                "layer_id": "underground:map2:group90:floor78",
                "coordinate_space": "layer_local",
                "x": 732.19,
                "y": 2511.86,
                "confidence": 0.93,
                "state": "TRACKING",
                "timestamp": 12.5,
                "reference_id": "fontaine_ug_g90_f78",
            },
        )

    def test_positions_on_different_floors_are_not_same_space(self) -> None:
        base = dict(
            region_id="fontaine",
            coordinate_space=CoordinateSpace.LAYER_LOCAL,
            x=10,
            y=20,
            confidence=0.9,
            state=PositionState.TRACKING,
            timestamp=1.0,
        )
        floor_a = MapPosition(layer_id="floor_a", **base)
        floor_b = MapPosition(layer_id="floor_b", **base)

        self.assertFalse(floor_a.same_space(floor_b))
