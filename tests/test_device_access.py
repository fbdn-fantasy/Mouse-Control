import runpy
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mouse_control.catalog import CATALOG
from mouse_control.legacy_reports import G600
from test_legacy_reports import descriptor


class EndpointAccessTests(unittest.TestCase):
    def setUp(self):
        helpers = runpy.run_path(str(Path(__file__).resolve().parents[1] / "packaging/mouse-control-access"))
        self.allow = helpers["allow"]
        self.protocol = helpers["protocol_endpoint"]
        ids = {pid for group in CATALOG.values() for text in group.values() for pid in text.split()} | {"c53f"}
        self.patch = patch.dict(self.allow.__globals__, PRODUCTS=ids)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_feature_and_vendor_report_guards(self):
        self.assertTrue(self.protocol(descriptor(G600), "c24a"))
        self.assertFalse(self.protocol(descriptor(G600), "c246"))
        feature = bytes((0x85,0x8e,0x75,8,0x95,1,0xb1,2))
        self.assertTrue(self.protocol(feature, "c245"))
        self.assertFalse(self.protocol(feature, "c246"))
        hidpp = bytes((6,0,255,0x85,17,0x75,8,0x95,19,0x91,0))
        self.assertTrue(self.protocol(hidpp, "c08b"))
        self.assertFalse(self.protocol(hidpp.replace(bytes((6,0,255)),bytes((6,1,0))), "c08b"))

    def test_pointer_only_no_keyboard_and_known_product(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            dev=root/"input/event4/device"
            (dev/"id").mkdir(parents=True)
            (dev/"capabilities").mkdir()
            (dev/"id/vendor").write_text("046d")
            (dev/"id/product").write_text("4074")
            (dev/"capabilities/rel").write_text("3")
            def keys(mask):
                size = 8 * struct.calcsize("L")
                words = []
                while mask:
                    words.append(f"{mask & ((1 << size)-1):x}")
                    mask >>= size
                (dev/"capabilities/key").write_text(" ".join(reversed(words)))
            keys(7 << 0x110)
            self.assertTrue(self.allow("event4",root))
            keys(7 << 0x110 | 1 << 30)
            self.assertFalse(self.allow("event4",root))
            keys(7 << 0x110)
            (dev/"id/product").write_text("c34a")
            self.assertFalse(self.allow("event4",root))
            self.assertFalse(self.allow("../event4",root))
