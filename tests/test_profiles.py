"""Exercise profile reads and flash transactions on simulated mice only."""

import binascii
import json
from pathlib import Path
import struct
import tempfile
import unittest
from types import SimpleNamespace

from mouse_control.profiles import (
    ProfileError, activate_profile, create_profile, current_stages, read_profile, read_roster, save_profile,
)
from mouse_control.hardware import HardwareError, ProtocolError


def with_crc(data):
    data[-2:] = binascii.crc_hqx(data[:-2], 0xFFFF).to_bytes(2, "big")
    return bytes(data)


class ProfileDevice:
    def __init__(self, model):
        self.model = model
        self.active = 1
        self.stage = 1
        self.unit = b"Unit"
        self.writes = []
        self.corrupt_write = False
        self.rate_mask = 0x8B
        self.commits = []
        self.fail_next_page = None
        self.mode = 1
        if model in ("G305", "G203 Prodigy", "G203 LIGHTSYNC"):
            self.description = bytes.fromhex("01 03 01 01 01 06 06 00 ff 0a 02")
            if model == "G203 LIGHTSYNC":
                self.description = self.description[:1] + bytes([4]) + self.description[2:]
        elif model == "G PRO Wireless":
            self.description = bytes.fromhex("01 03 01 05 01 08 10 00 ff 0a 04")
        elif model == "G903 HERO":
            self.description = bytes.fromhex("01 04 01 05 01 0c 10 00 ff 0a 04")
        elif model == "G604":
            self.description = bytes.fromhex("01 03 01 05 01 10 10 00 ff 0a 04")
        else:
            self.description = bytes.fromhex("01 02 01 05 05 0b 10 00 ff 0a 01")
        directory = bytearray([255]) * 255
        for index in range(self.description[3]):
            struct.pack_into(">HBB", directory, 4 * index, index + 1, 1, 0)
        self.pages = {0: with_crc(directory)}
        raw = bytearray(range(255))
        raw[:3] = bytes((1, 1, 0))
        struct.pack_into("<5H", raw, 3, 400, 800, 1600, 3200, 0xFFFF)
        raw[32:36] = bytes.fromhex("80 01 00 01")
        raw[36:40] = bytes.fromhex("90 03 ff ff")
        for sector in range(1, self.description[3] + 1):
            self.pages[sector] = with_crc(raw)

    def dpi_values(self):
        return range(200, 12001, 50)

    def query(self, feature, fn, payload=b""):
        if feature == 3 and fn == 0:
            return bytes(1) + self.unit + bytes(11)
        if feature == 0x8060 and fn == 0:
            return bytes([self.rate_mask])
        assert feature == 0x8100
        if fn == 0:
            return self.description
        if fn == 2:
            return bytes([self.mode])
        if fn == 4:
            return self.active.to_bytes(2, "big")
        if fn == 3:
            self.active = int.from_bytes(payload, "big")
            self.stage = self.pages[self.active][1]
            return bytes(16)
        if fn == 5:
            sector, offset = struct.unpack(">HH", payload)
            if sector == 0 and self.pages[0] == b"":
                raise ProtocolError(1, 0x50, 2)
            assert 0 <= offset <= len(self.pages[sector]) - 16
            return self.pages[sector][offset:offset + 16]
        if fn == 11:
            return bytes([self.stage])
        if fn == 12:
            self.stage = payload[0]
            return bytes(16)
        self.writes.append(fn)
        if fn == 6:
            self.destination, offset, self.size = struct.unpack(">HHH", payload)
            assert offset == 0 and self.destination in range(self.description[3] + 1)
            if self.destination == self.fail_next_page:
                self.fail_next_page = None
                raise HardwareError("Simulated mouse disconnect")
            self.buffer = bytearray()
        elif fn == 7:
            assert len(payload) == 16
            self.buffer.extend(payload)
        elif fn == 8:
            if self.corrupt_write:
                self.buffer[85] ^= 1
            self.pages[self.destination] = bytes(self.buffer[:self.size])
            self.commits.append(self.destination)
        else:
            raise AssertionError(fn)
        return bytes(16)


class ExtendedDevice(ProfileDevice):
    def __init__(self, model, layout=7, *, wireless=True):
        super().__init__(model)
        self.node = SimpleNamespace(product=0x40A9 if wireless else 0xC09B, address=1 if wireless else 255)
        self.axes = model != "G309 LIGHTSPEED"
        self.description = bytes((1, layout, 1, 5, 1, 5, 16, 0, 255, 0x0A, 4))
        raw = bytearray(self.pages[1])
        raw[:4] = bytes((5, 2, 1, 0))
        for i, dpi in enumerate((400, 800, 1600, 3200, 0)):
            struct.pack_into("<HHB", raw, 4 + i * 5, dpi, dpi + 50 if dpi and self.axes else 0, i % 3 + 1)
        raw[48:52] = bytes.fromhex("80 01 00 01")
        for i in range(1, 6):
            self.pages[i] = with_crc(raw)

    def query(self, feature, fn, payload=b""):
        if feature == 0x2202:
            if fn == 1:
                return bytes((0, 0, 3 if self.axes else 0)) + bytes(13)
            if fn == 2:
                assert payload[0] == payload[2] == 0
                return payload + struct.pack(">HHHH", 100, 0xE032, 32000 if payload[1] == 0 else 16000, 0) + bytes(5)
        if feature == 0x8061:
            assert fn == 0 and payload == bytes((self.node.address == 1,))
            return (0x7F if self.node.address == 1 else 0x0F).to_bytes(2, "big") + bytes(14)
        return super().query(feature, fn, payload)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.backups = Path(directory.name)

    def test_patch_specific_bytes_and_verify_both_models(self):
        for model in ("G305", "G502 HERO"):
            with self.subTest(model=model):
                dev = ProfileDevice(model)
                before = dev.pages[1]
                snap = read_profile(dev)
                self.assertEqual(snap.buttons[:2], ("primary", "dpi-up"))
                self.assertFalse(snap.buttons[2])
                self.assertEqual(snap.dpi_steps, (400, 800, 1600, 3200, 0))
                self.assertEqual("profile-cycle" in snap.actions, model == "G502 HERO")
                updated = save_profile(dev, snap, dpi_steps=(400, 900, 1600, 3200, 0),
                                       buttons={1: "dpi-up", 2: "back"}, backup_dir=self.backups)
                expected = bytearray(before)
                expected[5:7] = (900).to_bytes(2, "little")
                expected[40:44] = bytes.fromhex("80 01 00 08")
                self.assertEqual(dev.pages[1], with_crc(expected))
                self.assertEqual(updated.dpi_steps[1], 900)
                self.assertEqual(updated.buttons[2], "back")
                self.assertEqual(dev.writes, [6] + [7] * 16 + [8])
                self.assertIn(before, [p.read_bytes() for p in self.backups.glob("*.bin")])

    def test_stale_profile_device_and_repaired_receiver_are_rejected(self):
        for change in ("sector", "other-object", "unit", "contents"):
            with self.subTest(change=change):
                dev = ProfileDevice("G502 HERO")
                snap = read_profile(dev)
                if change == "sector":
                    dev.active = 2
                elif change == "other-object":
                    dev = ProfileDevice(dev.model)
                elif change == "unit":
                    dev.unit = b"Else"
                else:
                    raw = bytearray(dev.pages[1])
                    raw[95] ^= 1
                    dev.pages[1] = with_crc(raw)
                with self.assertRaises(ProfileError):
                    save_profile(dev, snap, buttons={3: "dpi-down"}, backup_dir=self.backups)
                self.assertFalse(dev.writes)

    def test_invalid_crc_and_damaged_directory_never_written(self):
        dev = ProfileDevice("G305")
        original = dev.pages[1]
        dev.pages[1] = original[:-1] + bytes([original[-1] ^ 255])
        with self.assertRaisesRegex(ProfileError, "checksum"):
            read_profile(dev)
        damaged = bytearray(dev.pages[0])
        damaged[32] ^= 1
        dev.pages[0] = bytes(damaged)
        directory = bytearray([255]) * 255
        directory[:4] = bytes.fromhex("01 01 01 ff")
        dev.pages[0x100] = directory
        dev.pages[0x101] = original
        factory = read_profile(dev)
        self.assertFalse(factory.editable)
        self.assertEqual(factory.slot, 1)
        with self.assertRaisesRegex(ProfileError, "Factory"):
            save_profile(dev, factory, buttons={1: "primary"}, backup_dir=self.backups)
        self.assertFalse(dev.writes)

    def test_invalid_changes_and_same_values_write_nothing(self):
        dev = ProfileDevice("G305")
        snap = read_profile(dev)
        self.assertIsNotNone(save_profile(dev, snap, dpi_steps=snap.dpi_steps,
                                          buttons={1: "dpi-up"}, backup_dir=self.backups))
        for steps in ((0, 800, 1600, 3200, 0), (400, 801, 1600, 3200, 0)):
            with self.assertRaises(ProfileError):
                save_profile(dev, snap, dpi_steps=steps, backup_dir=self.backups)
        dev.stage = 3
        with self.assertRaisesRegex(ProfileError, "Switch"):
            save_profile(dev, snap, dpi_steps=(400, 800, 1600, 0, 0), backup_dir=self.backups)
        with self.assertRaises(ProfileError):
            save_profile(dev, snap, buttons={2: "profile-cycle"}, backup_dir=self.backups)
        with self.assertRaisesRegex(ProfileError, "primary click"):
            save_profile(dev, snap, buttons={0: "back"}, backup_dir=self.backups)
        self.assertFalse(dev.writes)
        self.assertFalse(list(self.backups.glob("*.bin")))

    def test_failed_readback_keeps_backup_and_does_not_retry(self):
        dev = ProfileDevice("G502 HERO")
        snap = read_profile(dev)
        old_raw = dev.pages[1]
        dev.corrupt_write = True
        with self.assertRaisesRegex(ProfileError, "could not be verified"):
            save_profile(dev, snap, buttons={4: "dpi-cycle"}, backup_dir=self.backups)
        self.assertEqual(dev.writes.count(6), 1)
        self.assertEqual(next(self.backups.glob("*.bin")).read_bytes(), old_raw)

    def test_g502_with_256_byte_flash_layout(self):
        dev = ProfileDevice("G502 HERO")
        desc = bytearray(dev.description)
        desc[7:9] = b"\x01\x00"
        dev.description = bytes(desc)
        for address, page in dev.pages.items():
            dev.pages[address] = with_crc(bytearray(page[:-2] + b"\xff\x00\x00"))
        snap = read_profile(dev)
        updated = save_profile(dev, snap, buttons={1: "back"}, backup_dir=self.backups)
        self.assertEqual(updated.buttons[1], "back")
        self.assertEqual(len(dev.pages[1]), 256)

    def test_rate_name_and_shortcut_on_additional_layouts(self):
        for model in ("G203 Prodigy", "G203 LIGHTSYNC", "G502 LIGHTSPEED", "G PRO Wireless", "G903 HERO", "G604"):
            with self.subTest(model=model):
                dev = ProfileDevice(model)
                original = dev.pages[1]
                snap = read_profile(dev)
                self.assertEqual(snap.report_rates, (125, 250, 500, 1000))
                updated = save_profile(dev, snap, report_rate=250, name="Work 🎧",
                                       buttons={3: "copy"}, backup_dir=self.backups)
                self.assertEqual(updated.report_rate, 250)
                self.assertEqual(updated.name, "Work 🎧")
                expected = bytearray(original)
                expected[0] = 4
                expected[160:208] = "Work 🎧".encode("utf-16-le").ljust(48, b"\0")
                expected[44:48] = bytes.fromhex("80 02 01 06")
                self.assertEqual(dev.pages[1], with_crc(expected))
                with self.assertRaisesRegex(ProfileError, "polling rate"):
                    save_profile(dev, updated, report_rate=400, backup_dir=self.backups)
                with self.assertRaisesRegex(ProfileError, "24-character"):
                    save_profile(dev, updated, name="😀" * 13, backup_dir=self.backups)

    def test_profile_roster_and_create_on_supported_hardware(self):
        dev = ProfileDevice("G502 HERO")
        dev.pages[2] = bytes(255)
        directory = bytearray(dev.pages[0])
        directory[6] = 0
        dev.pages[0] = with_crc(directory)
        snap = read_profile(dev)
        slots = read_roster(dev)
        self.assertEqual([p.slot for p in slots if p.creatable], [2])
        self.assertTrue(slots[0].active)
        self.assertFalse(slots[1].enabled)
        with self.assertRaisesRegex(ProfileError, "enabled"):
            activate_profile(dev, 2)
        created = create_profile(dev, snap, 2, "Office", backup_dir=self.backups)
        self.assertEqual((created.slot, created.name), (2, "Office"))
        self.assertEqual(dev.pages[1], snap._original)
        self.assertEqual(dev.commits, [2, 0])
        self.assertEqual(activate_profile(dev, 1).slot, 1)
        with self.assertRaisesRegex(ProfileError, "unused"):
            single = ProfileDevice("G305")
            create_profile(single, read_profile(single), 2, "Work", backup_dir=self.backups)

    def test_protect_disabled_custom_slot_and_retry_directory_commit(self):
        dev = self.factory_mouse("G502 HERO")
        snap = read_profile(dev)
        snap = save_profile(dev, snap, name="G502", backup_dir=self.backups)
        table = bytearray(dev.pages[0])
        table[6] = 0
        dev.pages[0] = with_crc(table)
        dev.pages[2] = dev.pages[1]
        snap = read_profile(dev)
        self.assertFalse(read_roster(dev)[1].creatable)
        with self.assertRaisesRegex(ProfileError, "custom settings"):
            create_profile(dev, snap, 2, "New", backup_dir=self.backups)
        dev.pages[2] = bytes(255)
        old_commits = len(dev.commits)
        dev.fail_next_page = 0
        with self.assertRaisesRegex(ProfileError, "could not be verified"):
            create_profile(dev, snap, 2, "New", backup_dir=self.backups)
        self.assertEqual(dev.pages[0], snap._directory)
        created = create_profile(dev, snap, 2, "New", backup_dir=self.backups)
        self.assertEqual((created.slot, created.name), (2, "New"))
        self.assertEqual(dev.commits[old_commits:], [2, 0])

    def factory_mouse(self, model, missing=False):
        dev = ProfileDevice(model)
        count = dev.description[3]
        table = bytearray([255]) * 255
        for slot in range(1, count + 1):
            struct.pack_into(">HBB", table, (slot - 1) * 4, 0x100 + slot, 1, 255)
            dev.pages[0x100 + slot] = dev.pages[slot][:-2] + b"\xff\xff"
            dev.pages[slot] = bytes(255)
        dev.pages[0] = b"" if missing else bytes(255)
        dev.pages[0x100] = bytes(table)
        dev.active = 0x101
        return dev

    def test_first_save_backup_preservation_and_commit_order(self):
        for model in ("G502 HERO", "G305"):
            with self.subTest(model=model):
                dev = self.factory_mouse(model, missing=model == "G305")
                dev.stage = 3
                rom = {address: data for address, data in dev.pages.items() if address >= 256}
                blank = dev.pages[0]
                snap = read_profile(dev)
                self.assertTrue(snap.first_save)
                updated = save_profile(dev, snap, buttons={1: "middle"}, backup_dir=self.backups)
                self.assertFalse(updated.first_save)
                self.assertTrue(updated.editable)
                self.assertEqual(updated.buttons[1], "middle")
                self.assertEqual(dev.stage, 3)
                self.assertEqual(rom, {address: data for address, data in dev.pages.items() if address >= 256})
                self.assertEqual(dev.commits, [*range(1, dev.description[3] + 1), 0])
                for slot in range(2, dev.description[3] + 1):
                    self.assertEqual(dev.pages[slot], with_crc(bytearray(rom[0x100 + slot])))
                file = next(self.backups.glob(f"{model.replace(' ', '-')}-*.json"))
                self.assertEqual(json.loads(file.read_bytes())["original_pages_hex"]["0"], blank.hex())

    def test_first_save_refuses_unrelated_user_memory(self):
        dev = self.factory_mouse("G502 HERO")
        snap = read_profile(dev)
        dev.pages[4] = bytes([17]) * 255
        with self.assertRaisesRegex(ProfileError, "already contains"):
            save_profile(dev, snap, buttons={3: "forward"}, backup_dir=self.backups)
        self.assertFalse(dev.writes)

    def test_ambiguous_directory_read_is_read_only(self):
        dev = self.factory_mouse("G305")
        query = dev.query
        def ambiguous_reply(feature, fn, payload=b""):
            if fn == 5 and payload[:2] == b"\0\0":
                raise ProtocolError(1, 0x50, 0x0B)
            return query(feature, fn, payload)
        dev.query = ambiguous_reply
        snap = read_profile(dev)
        self.assertFalse(snap.first_save)
        self.assertFalse(snap.editable)
        with self.assertRaises(ProfileError):
            save_profile(dev, snap, buttons={1: "middle"}, backup_dir=self.backups)
        self.assertFalse(dev.writes)

    def test_interrupted_initialization_preserves_factory_and_resumes(self):
        dev = self.factory_mouse("G502 HERO")
        dev.fail_next_page = 3
        snap = read_profile(dev)
        with self.assertRaisesRegex(ProfileError, "Initialization"):
            save_profile(dev, snap, buttons={3: "back"}, backup_dir=self.backups)
        self.assertEqual(dev.commits, [1, 2])
        self.assertEqual(dev.pages[0], bytes(255))
        self.assertTrue(read_profile(dev).first_save)
        updated = save_profile(dev, snap, buttons={3: "back"}, backup_dir=self.backups)
        self.assertFalse(updated.first_save)
        self.assertEqual(dev.commits, [1, 2, 3, 4, 5, 0])

    def test_factory_address_banks_and_invalid_argument_fallback(self):
        for reported, rejected, expected in ((0x101, None, [0x102]), (1, None, [2]),
                                              (0x101, 0x102, [0x102, 2]), (1, 2, [2, 0x102])):
            with self.subTest(reported=reported, rejected=rejected):
                dev = self.factory_mouse("G502 HERO")
                dev.active = reported
                query = dev.query
                sent = []
                def choose(feature, fn, payload=b""):
                    if feature == 0x8100 and fn == 3:
                        address = int.from_bytes(payload, "big")
                        sent.append(address)
                        if address == rejected:
                            raise ProtocolError(1, 0x30, 2)
                    return query(feature, fn, payload)
                dev.query = choose
                self.assertEqual(activate_profile(dev, 2).slot, 2)
                self.assertEqual(sent, expected)

    def test_one_factory_profile_can_be_saved_then_expanded(self):
        dev = self.factory_mouse("G502 HERO")
        dev.description = dev.description[:4] + bytes((1,)) + dev.description[5:]
        table = bytearray([255]) * 255
        table[:4] = bytes.fromhex("01 01 01 ff")
        dev.pages[0x100] = table
        snap = read_profile(dev)
        self.assertTrue(snap.first_save)
        first = save_profile(dev, snap, name="Home", backup_dir=self.backups)
        self.assertEqual(dev.commits, [1, 0])
        self.assertTrue(read_roster(dev, first)[1].creatable)
        work = create_profile(dev, first, 2, "Work", backup_dir=self.backups)
        self.assertEqual((work.slot, work.name), (2, "Work"))

    def test_modern_axes_indices_and_rate_preserve_unedited_bytes(self):
        for model, layout, wireless, rate_offset in (
            ("PRO X SUPERLIGHT 2", 6, False, 1), ("PRO X SUPERLIGHT 2", 7, True, 0),
            ("PRO X SUPERLIGHT 2 DEX", 7, True, 1), ("PRO X SUPERLIGHT 2 DEX", 7, False, 0),
        ):
            with self.subTest(model=model, layout=layout, wireless=wireless):
                dev = ExtendedDevice(model, layout, wireless=wireless)
                original = dev.pages[1]
                snap = read_profile(dev)
                self.assertEqual(snap.dpi_steps, current_stages(dev, 1, layout))
                self.assertEqual(snap.dpi_steps_y, (450, 850, 1650, 3250, 0))
                self.assertEqual((snap.default_index, snap.shift_index, snap.buttons[0]), (1, 0, "primary"))
                self.assertEqual(snap.report_rates[-1], 8000 if wireless else 1000)
                settings = dict(dpi_steps=(400, 1200, 1600, 3200, 0), dpi_steps_y=(450, 1450, 1650, 3250, 0),
                                default_index=2, shift_index=3, buttons={1: "copy"}, report_rate=1000)
                updated = save_profile(dev, snap, **settings, backup_dir=self.backups)
                self.assertEqual((updated.default_index, updated.shift_index), (2, 3))
                self.assertEqual(updated.dpi_steps, settings["dpi_steps"])
                self.assertEqual(updated.dpi_steps_y, settings["dpi_steps_y"])
                self.assertEqual((updated.report_rate, updated.buttons[1]), (1000, "copy"))
                mutable = {rate_offset, 2, 3, 9, 10, 11, 12, 52, 53, 54, 55, 253, 254}
                self.assertTrue(all(dev.pages[1][i] == original[i] for i in range(255) if i not in mutable))
                with self.assertRaisesRegex(ProfileError, "20000 DPI"):
                    save_profile(dev, updated, dpi_steps_y=(450, 20000, 1650, 3250, 0), backup_dir=self.backups)

    def test_g309_x_only_and_pro2_preserve_unidentified_rate_bytes(self):
        for model in ("G309 LIGHTSPEED", "PRO 2 LIGHTSPEED"):
            dev = ExtendedDevice(model)
            snap = read_profile(dev)
            self.assertEqual(snap.report_rates, ())
            self.assertEqual(bool(snap.dpi_steps_y), model == "PRO 2 LIGHTSPEED")
            with self.assertRaises(ProfileError):
                save_profile(dev, snap, report_rate=8000, backup_dir=self.backups)
            edited = save_profile(dev, snap, dpi_steps=(450, 800, 1600, 3200, 0), backup_dir=self.backups)
            self.assertEqual(edited.dpi_steps[0], 450)
            self.assertEqual(edited._original[6:9], snap._original[6:9])

    def test_format8_exposes_only_unambiguous_profile_fields(self):
        dev = ExtendedDevice("PRO X2 SUPERSTRIKE", layout=8)
        snap = read_profile(dev)
        self.assertEqual(snap.dpi_steps, ())
        self.assertFalse(snap.can_select_defaults)
        self.assertEqual(current_stages(dev, 1, 8), ())
        with self.assertRaises(ProfileError):
            save_profile(dev, snap, default_index=1, backup_dir=self.backups)
        result = save_profile(dev, snap, buttons={1: "forward"}, report_rate=4000, backup_dir=self.backups)
        self.assertEqual(result.buttons[1], "forward")
        self.assertEqual(result._original[1:52], snap._original[1:52])
        self.assertEqual(result._original[56:-2], snap._original[56:-2])


if __name__ == "__main__":
    unittest.main()
