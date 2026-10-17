import json
import math
import tempfile
import unittest
from pathlib import Path

from palette_core import (
    CATEGORIES, Settings, SettingsStore, WorkArea,
    dock_position, fraction_for_top, handle_position, is_inward_pull, nearest_side,
)


class PaletteTests(unittest.TestCase):
    def test_eight_categories_and_order(self):
        self.assertEqual([c.color_name for c in CATEGORIES],
                         ["黄色", "红色", "绿色", "蓝色", "紫色", "粉色", "橙色", "灰色"])
        self.assertEqual(len({c.title for c in CATEGORIES}), 8)
        self.assertEqual(len({c.color for c in CATEGORIES}), 8)

    def test_approved_category_meanings(self):
        self.assertEqual([c.title for c in CATEGORIES], ["核心主张", "局限与疑问", "对我们的启发",
                         "架构与数据流", "数学与约束", "实验证据", "实现与复现", "背景与术语"])


class DockTests(unittest.TestCase):
    def test_edges_and_negative_monitor_coordinates(self):
        for area in (WorkArea(0, 32, 1920, 1048), WorkArea(-1920, 32, 1920, 1048),
                     WorkArea(1920, -1080, 1920, 1080), WorkArea(0, 0, 800, 600)):
            for side in ("left", "right"):
                for fraction in (0, 0.2, 0.5, 1):
                    with self.subTest(area=area, side=side, fraction=fraction):
                        x, y = dock_position(area, (354, 560), side, fraction)
                        self.assertGreaterEqual(x, area.x)
                        self.assertLessEqual(x + 354, area.right)
                        self.assertGreaterEqual(y, area.y)
                        self.assertLessEqual(y + 560, area.y + area.height)

    def test_fraction_is_clamped(self):
        a = WorkArea(0, 32, 1920, 1048)
        self.assertEqual(fraction_for_top(a, -10000, 600), 0)
        self.assertEqual(fraction_for_top(a, 10000, 600), 1)
        self.assertEqual(fraction_for_top(a, 256, 600), 0.5)

    def test_tab_stays_at_edge(self):
        a = WorkArea(-1600, 32, 1600, 868)
        for side, expected_x in (("left", -1600), ("right", -28)):
            for f in (0, 0.5, 1):
                p = handle_position(a, (354, 560), (28, 116), Settings(side=side, y_fraction=f))
                self.assertEqual(p[0], expected_x)
                self.assertGreaterEqual(p[1], a.y)
                self.assertLessEqual(p[1] + 116, a.y + a.height)

    def test_nearest_edge_midpoint_tie(self):
        a = WorkArea(-1920, 0, 1920, 1080)
        self.assertEqual(nearest_side(a, -1600), "left")
        self.assertEqual(nearest_side(a, -960), "right")
        self.assertEqual(nearest_side(a, -1), "right")

    def test_inward_pull_uses_original_edge(self):
        self.assertTrue(is_inward_pull("right", -20))
        self.assertTrue(is_inward_pull("right", -1800))
        self.assertTrue(is_inward_pull("left", 1800))
        self.assertFalse(is_inward_pull("left", -1800))
        self.assertFalse(is_inward_pull("right", 12))
        self.assertFalse(is_inward_pull("right", -12))


class SettingsTests(unittest.TestCase):
    def test_missing_corrupt_or_wrong_root_returns_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory))
            self.assertEqual(store.load(), Settings())
            for text in ("", "{broken", "null", "[]", "7"):
                store.path.write_text(text, encoding="utf-8")
                self.assertEqual(store.load(), Settings())

    def test_round_trip_and_atomic_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "new_directory")
            original = Settings(side="left", y_fraction=0.75, monitor_x=-400,
                                monitor_y=100, collapsed=True, auto_hide=True)
            store.save(original)
            self.assertEqual(store.load(), original)
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
            store.save(Settings())
            self.assertEqual(store.load(), Settings())
            self.assertEqual(list(store.directory.glob("*.tmp")), [])

    def test_malformed_fields_never_become_true_implicitly(self):
        value = Settings.from_dict({"side": "bottom", "collapsed": "false", "auto_hide": 1,
                                    "monitor_x": True, "y_fraction": math.nan})
        self.assertEqual(value, Settings())
        self.assertEqual(Settings.from_dict({"y_fraction": 7}).y_fraction, 1)
        self.assertEqual(Settings.from_dict({"y_fraction": -3}).y_fraction, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
