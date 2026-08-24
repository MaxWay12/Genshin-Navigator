from __future__ import annotations

import unittest

from genshin_navigator.hoyolab_underground import select_point_groups


class SelectPointGroupsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.groups = [
            {
                "id": 10,
                "underground_entrance_x_pos": 10,
                "underground_entrance_y_pos": 20,
                "floors": [{"overlay": {"url": "a"}}, {"overlay": {"url": "b"}}],
            },
            {
                "id": 11,
                "underground_entrance_x_pos": 100,
                "underground_entrance_y_pos": 200,
                "floors": [{"overlay": {"url": "c"}}],
            },
        ]

    def test_selects_explicit_group(self) -> None:
        result = select_point_groups(self.groups, group_ids={11})
        self.assertEqual([group["id"] for group in result], [11])

    def test_filters_by_radius_and_floor_count(self) -> None:
        result = select_point_groups(
            self.groups, near=(12, 19), radius=5, min_floors=2
        )
        self.assertEqual([group["id"] for group in result], [10])


if __name__ == "__main__":
    unittest.main()
