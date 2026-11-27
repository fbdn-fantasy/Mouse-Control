import unittest
from mouse_control.catalog import CATALOG, identify


class CatalogTests(unittest.TestCase):
    def test_every_requested_retail_name_with_usb_and_name_lookup(self):
        entries = {name: ids for group in CATALOG.values() for name, ids in group.items()}
        self.assertEqual(len(entries), 71)
        for name, ids in entries.items():
            for product in [0] + [int(raw, 16) for raw in ids.split()]:
                with self.subTest(name=name, product=product):
                    self.assertEqual(identify(product, "Logitech " + name), name)

    def test_ambiguous_family_and_model_numbers_are_bounded(self):
        self.assertEqual(identify(0xc08b, "Logitech Gaming Mouse"), "G502 HERO")
        self.assertEqual(identify(0x4074, "G304"), "G304 LIGHTSPEED")
        self.assertIsNone(identify(0, "G599 Unknown"))
        self.assertIsNone(identify(0, "Logitech G500 Keyboard"))
