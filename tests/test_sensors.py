import unittest
from struct import pack
from types import SimpleNamespace

from mouse_control import sensors
from mouse_control.hardware import HardwareError, UnsupportedFeature


class Sensor:
    def __init__(self, replies, product=0):
        self.node = SimpleNamespace(product=product, address=1)
        self.replies = dict(replies)
        self.log = []

    def query(self, feature, fn, payload=b""):
        self.log.append((feature, fn, payload))
        if (feature, fn) not in self.replies:
            raise UnsupportedFeature(str(feature))
        return self.replies[feature, fn]


class SensorTests(unittest.TestCase):
    def test_odd_length_range_pages_preserve_split_words(self):
        stream = pack(">9H", 100, 200, 300, 400, 500, 600, 0xe064, 900, 0)
        class Paged(Sensor):
            def query(self, feature, fn, payload=b""):
                start = 13 * payload[2]
                return payload + stream[start:start+13].ljust(13, b"\0")
        self.assertEqual(sensors._ranges(Paged({}), 0), tuple(range(100, 901, 100)))

    def test_extended_sensitivity_preserves_liftoff_and_checks_both_axes(self):
        class Dual(Sensor):
            def query(self, feature, fn, payload=b""):
                if fn == 2 and feature == 0x2202:
                    return payload + pack(">4H", 100, 0xe064, 1200, 0) if payload[1] == 0 else payload + pack(">4H", 200, 0xe0c8, 1200, 0)
                return super().query(feature, fn, payload)

        mouse = Dual({(0x2202, 1): bytes([0, 0, 3]),
                      (0x2202, 5): pack(">B4HB", 0, 800, 400, 800, 400, 2),
                      (0x2202, 6): b""})
        self.assertEqual(sensors.read_dpi(mouse), 800)
        self.assertEqual(sensors.dpi_values(mouse), tuple(range(200, 1201, 200)))
        sensors.set_dpi(mouse, 1000)
        self.assertEqual(mouse.log[-1], (0x2202, 6, pack(">BHHB", 0, 1000, 1000, 2)))
        with self.assertRaises(ValueError):
            sensors.set_dpi(mouse, 1100)

    def test_fixed_sensor_read_and_firmware_default(self):
        self.assertEqual(sensors.read_dpi(Sensor({(0x2200, 0): pack(">HB", 1000, 0)})), 1000)
        mouse = Sensor({(0x2201, 2): pack(">BHH", 0, 0, 1600)})
        self.assertEqual(sensors.read_dpi(mouse), 1600)
        self.assertEqual(sensors.read_dpi(mouse, default_if_zero=False), 0)

    def test_rate_masks_and_wireless_transport(self):
        mouse = Sensor({(0x8060, 0): b"\x8b", (0x8060, 1): b"\x02"})
        self.assertEqual(sensors.report_rates(mouse), (500, (125, 250, 500, 1000)))
        ext = Sensor({(0x8061, 0): b"\x00\x1f", (0x8061, 1): b"\x00\x1f",
                      (0x8061, 2): b"\x04"})
        self.assertEqual(sensors.report_rates(ext), (2000, (125, 250, 500, 1000, 2000)))
        self.assertEqual(ext.log[-1], (0x8061, 2, b"\x01"))

    def test_extended_rate_writes_use_current_link_mask_and_verify(self):
        class ExtendedRate(Sensor):
            def query(self, feature, fn, payload=b""):
                if (feature, fn) == (0x8061, 3):
                    self.replies[0x8061, 2] = payload
                return super().query(feature, fn, payload)
        # Reserved high-byte data and unknown bit 7 must never add a selectable rate.
        dev = ExtendedRate({(0x8061, 1): b"\xff\x8b", (0x8061, 2): b"\x01", (0x8061, 3): b""})
        self.assertEqual(sensors.report_rates(dev), (250, (125, 250, 1000)))
        with self.assertRaises(ValueError):
            sensors.set_report_rate(dev, 8000)
        sensors.set_report_rate(dev, 1000)
        self.assertIn((0x8061, 3, b"\x03"), dev.log)
        self.assertTrue(all(fn != 0 for _, fn, _ in dev.log))
        self.assertEqual(dev.log[-1], (0x8061, 2, b"\x01"))
        dev.node.address = 0xff
        self.assertEqual(sensors.report_rates(dev)[0], 1000)
        self.assertEqual(dev.log[-1], (0x8061, 2, b"\0"))
        dev.node.product = 0xb03c
        self.assertEqual(sensors.report_rates(dev), (None, ()))

    def test_legacy_rate_register_and_model_specific_intervals(self):
        class RegisterMouse(Sensor):
            interval = 2
            accept = True
            def exchange_raw(self, sub, reg, payload=b""):
                self.log.append((sub, reg, payload))
                assert reg == 0x64 and sub in (0x80, 0x81)
                if sub == 0x80 and self.accept:
                    self.interval = payload[0]
                return bytes((self.interval, 0, 0))
        for product in (0xc049, 0xc068, 0xc24e, 0xc07c):
            with self.subTest(product=product):
                dev = RegisterMouse({}, product)
                self.assertEqual(sensors.report_rates(dev), (500, (125, 200, 250, 333, 500, 1000)))
                sensors.set_report_rate(dev, 333)
                self.assertEqual(dev.log[-2:], [(0x80, 0x64, b"\x03"), (0x81, 0x64, b"")])
                dev.accept = False
                with self.assertRaisesRegex(HardwareError, "333 Hz"):
                    sensors.set_report_rate(dev, 125)
        original = RegisterMouse({}, 0xc041)
        self.assertEqual(sensors.report_rates(original)[1][-1], 500)
        unverified = RegisterMouse({}, 0xc048)
        self.assertEqual(sensors.report_rates(unverified), (500, ()))
        with self.assertRaises(ValueError):
            sensors.set_report_rate(unverified, 125)
        self.assertTrue(all(sub == 0x81 for sub, _, _ in unverified.log))

    def test_g9_register_is_exact_and_model_specific(self):
        mouse = Sensor({}, 0xc048)
        writes = []
        def register(sub, address, data=b"", report_id=0x10):
            writes.append((sub, address, data))
            return b"\x84\x00\x00"
        mouse.exchange_raw = register
        self.assertEqual(sensors.read_dpi(mouse), 800)
        sensors.set_dpi(mouse, 1000)
        self.assertEqual(writes, [(0x81, 0x63, b""), (0x80, 0x63, b"\x85")])

    def test_g5_short_sensor_and_g500_maximum_code(self):
        class Register(Sensor):
            def exchange_raw(self, sub, reg, payload=b"", report_id=0x10):
                self.log.append((sub, reg, payload))
                return b"\x81\xff\x00" if sub == 0x81 else b"\xf3\x00\xf3\x00"
        for pid in (0xc041, 0xc049):
            mouse = Register({}, pid)
            self.assertEqual(sensors.read_dpi(mouse), 800)
            sensors.set_dpi(mouse, 2000)
            self.assertEqual(mouse.log[-1], (0x80, 0x63, b"\x83"))
        self.assertEqual(sensors.read_dpi(Register({}, 0xc068)), 5700)
