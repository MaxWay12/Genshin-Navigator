import unittest
from types import SimpleNamespace
from PIL import ImageFont

from genshin_navigator.target_panel import marker_hits, render_panel


class TargetPanelTests(unittest.TestCase):
    def rows(self, count):
        return tuple(SimpleNamespace(poi=SimpleNamespace(id=str(i), name="Chest", kind="chest", x=10, y=20),
                                     selected=False, selectable=True, distance_m=12) for i in range(count))

    def test_twenty_rows_per_page_and_clamp(self):
        panel, hits, page = render_panel(self.rows(45), "available", 9, 600, ImageFont.load_default())
        self.assertEqual(page, 2)
        self.assertEqual(len([hit for hit in hits if hit.action == "select"]), 5)
        self.assertEqual(panel.shape, (600, 360, 3))

    def test_overlapping_markers_return_all_choices(self):
        self.assertEqual(marker_hits(self.rows(2), 5, 10, 0.5), ("0", "1"))
        self.assertEqual(marker_hits(self.rows(2), 500, 100, 0.5), ())
