import struct
import unittest

from mouse_control.remap import ControlError, read_controls, set_control


class Mouse:
    def __init__(self):
        # Left and right share group 1; Back can target group 1 or 2.
        self.inventory = ((0x50, 0x11, 1, 1), (0x51, 0x11, 1, 1),
                          (0x53, 0x31, 2, 3), (0xE0, 0x80, 3, 0))
        self.mapping = {}
        self.flags = 4
        self.writes = []

    def query(self, feature, fn, payload=b""):
        assert feature == 0x1B04
        if fn == 0:
            return bytes((len(self.inventory),))
        if fn == 1:
            cid, flags, group, mask = self.inventory[payload[0]]
            return struct.pack(">HHBBBBB", cid, 0, flags, 0, group, mask, 0)
        cid = int.from_bytes(payload[:2], "big")
        if fn == 2:
            return struct.pack(">HBHB", cid, self.flags, self.mapping.get(cid, 0), 0)
        assert fn == 3
        self.writes.append(payload)
        self.mapping[cid] = int.from_bytes(payload[3:5], "big")
        return bytes(16)


class RemapTests(unittest.TestCase):
    def test_device_groups_readback_and_default(self):
        dev = Mouse()
        controls = read_controls(dev)
        self.assertEqual(len(controls), 3)
        self.assertEqual(list(dict(controls[0].choices)), [0x50, 0x51])
        self.assertEqual(list(dict(controls[-1].choices)), [0x50, 0x51, 0x53])
        self.assertEqual(set_control(dev, 0x53, 0x51).target, 0x51)
        self.assertEqual(dev.writes, [bytes.fromhex("0053 00 0051")])
        self.assertEqual(set_control(dev, 0x53, 0x53).target, 0x53)
        self.assertEqual(dev.flags, 4)

    def test_invalid_stale_diverted_and_last_primary(self):
        dev = Mouse()
        for source, target in ((0x50, 0x51), (0x51, 0x53), (0xE0, 0x53), (0x53, 0)):
            with self.assertRaises(ControlError):
                set_control(dev, source, target)
        with self.assertRaisesRegex(ControlError, "changed"):
            set_control(dev, 0x53, 0x51, expected_target=0x50)
        dev.flags |= 1
        with self.assertRaisesRegex(ControlError, "diverts"):
            set_control(dev, 0x53, 0x51)
        self.assertFalse(dev.writes)
