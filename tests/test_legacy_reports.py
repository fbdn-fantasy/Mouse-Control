from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mouse_control.hardware import HardwareError, MouseInfo
from mouse_control import legacy_reports as legacy
from mouse_control.profiles import ProfileError


def descriptor(product):
    width = 34 if product == legacy.G300 else 153
    report = lambda ident, count: bytes((0x85, ident, 0x95, count, 0xB1, 2))
    return bytes((0x75, 8)) + b"".join(report(i, n) for i, n in ((0xF0, 3),(0xF3, width),(0xF4, width),(0xF5, width)))


class FeatureDevice:
    def __init__(self, product):
        self.model = "G300" if product == legacy.G300 else "G600 MMO"
        info = MouseInfo("test@usb", self.model, self.model)
        self.node = SimpleNamespace(info=info, product=product, identity="test-port")
        self._fd = 7
        self.slot, self.stage = 1, 2
        self.writes = []
        self.images = {}
        for i in range(3):
            raw = bytearray(b"\xAB" * legacy._LENGTH[product])
            raw[0] = 0xF3 + i
            if product == legacy.G300:
                raw[2:7] = bytes((2, 0x81,2,4,8))
                bound = 8
            else:
                raw[11:18] = bytes((3, 12, 1, 4, 8, 12, 16))
                bound = 31
            raw[bound:bound+3] = bytes((1,0,0))
            self.images[raw[0]] = bytes(raw)

    def ioctl(self, fd, request, buf, mutable):
        length = (request >> 16) & 0x3fff
        assert length == len(buf)
        if request & 0xffff == 0x4807:
            buf[:] = (bytes((0xf0,(self.slot-1)<<4 | self.stage << 1,0,0))
                      if buf[0] == 0xf0 else self.images[buf[0]])
        else:
            self.writes.append(bytes(buf))
            if buf[0] != 0xf0:
                self.images[buf[0]] = bytes(buf)
            elif buf[1] & 0x80:
                self.slot = ((buf[1] >> 4) & 3) + 1
                self.stage = 0
            else:
                self.stage = (buf[1] >> 1) & 3
        return length


class LegacyProtocolTests(unittest.TestCase):
    def test_profiles_and_physical_dpi_reads_for_both_layouts(self):
        for product in legacy.PRODUCTS:
            d=FeatureDevice(product)
            self.assertTrue(legacy.supports_descriptor(product, descriptor(product)))
            self.assertFalse(legacy.supports_descriptor(product, descriptor(product)[:-3]))
            self.assertFalse(legacy.supports_descriptor(product, descriptor(legacy.G600 if product==legacy.G300 else legacy.G300)))
            with patch.object(legacy.fcntl, "ioctl", side_effect=d.ioctl):
                state = legacy.read_state(d, d.node.info)
                self.assertEqual(state.active_stage, 2)
                self.assertEqual(state.dpi, 1000 if product == legacy.G300 else 600)
                d.stage = 3
                self.assertEqual(legacy.read_state(d, d.node.info).dpi, state.stages[3])
                self.assertEqual(legacy.set_dpi(d, state.stages[1]).active_stage, 1)
                self.assertEqual(legacy.activate_profile(d, 2).slot, 2)

    def test_patch_preserves_opaque_bytes_and_g_shift_and_backs_up(self):
        for product in legacy.PRODUCTS:
            d = FeatureDevice(product)
            with TemporaryDirectory() as backup, patch.object(legacy.fcntl, "ioctl", side_effect=d.ioctl):
                snap = legacy.read_profile(d)
                previous = snap._original
                new = list(snap.dpi_steps)
                new[1] = 750 if product == legacy.G300 else 950
                saved = legacy.save_profile(d, snap, dpi_steps=new, buttons={2: "copy"}, report_rate=500, backup_dir=backup)
                self.assertEqual(saved.dpi_steps, tuple(new))
                self.assertEqual(saved.report_rate, 500)
                self.assertEqual(saved.buttons[2], "copy")
                self.assertEqual(next(Path(backup).glob("*.bin")).read_bytes(), previous)
                off, buttons, _, rate = legacy._layout(product)
                touched = {off+1, rate, buttons+6, buttons+7, buttons+8}
                self.assertTrue(all(a==b for i,(a,b) in enumerate(zip(previous,saved._original)) if i not in touched))
                self.assertEqual(len(d.writes),1)
                with self.assertRaises(ProfileError):
                    legacy.save_profile(d, snap, buttons={1:"primary"}, backup_dir=backup)
                self.assertEqual(len(d.writes),1)

    def test_invalid_reports_are_never_changed(self):
        d=FeatureDevice(legacy.G300)
        with patch.object(legacy.fcntl, "ioctl", return_value=2):
            with self.assertRaises(HardwareError):
                legacy.probe(d)
        with patch.object(legacy.fcntl, "ioctl", side_effect=d.ioctl):
            raw=bytearray(d.images[0xf3])
            raw[3] = 0xff
            d.images[0xf3] = bytes(raw)
            with self.assertRaises(HardwareError):
                legacy.read_profile(d)
        self.assertFalse(d.writes)
