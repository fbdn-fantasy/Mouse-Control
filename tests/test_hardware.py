import os
import struct
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from mouse_control.button_monitor import ButtonMonitor, _EVENT
from mouse_control import older_mice
from mouse_control.hardware import (DPI, PROFILES, HardwareError, HidDevice, MouseBackend,
                                   MouseInfo, ProtocolError, UnsupportedFeature, _Node, _enumerate_nodes,
                                   _has_protocol_output, _state)


MOUSE = MouseInfo("g305@port", "Logitech G305 LIGHTSPEED", "G305")
NODE = _Node(MOUSE, Path("/dev/hidraw7"), 1, False, "kernel-instance")


class TestMouse:
    __test__ = False
    model = "G305"
    profile_format = 3

    def __init__(self):
        self.node = NODE
        self.mode = 1
        self.stages = [400, 800, 1600, 3200, 0]
        self.index = 1
        self.sensor = 800
        self.sensor_default = 400
        self._sensor_choices = None
        self.closed = False
        self.calls = []
        self.unwritten = False

    def query(self, feature, fn, payload=b""):
        if feature == 0x2202:
            raise UnsupportedFeature("no extended sensor")
        self.calls.append((feature, fn, payload))
        if (feature, fn) == (PROFILES, 2):
            answer = bytes([self.mode])
        elif (feature, fn) == (PROFILES, 4):
            answer = b"\0\1"
        elif (feature, fn) == (PROFILES, 11):
            answer = bytes([self.index])
        elif (feature, fn) == (PROFILES, 5):
            if self.unwritten and payload[0] == 0:
                raise ProtocolError(1, 0x52, 2)
            answer = b"\1\1\0" + struct.pack("<5H", *self.stages)
        elif (feature, fn) == (DPI, 2):
            answer = struct.pack(">BHH", 0, self.sensor, self.sensor_default)
        elif (feature, fn) == (PROFILES, 12):
            self.index = payload[0]
            self.sensor = self.stages[self.index]
            answer = b""
        elif (feature, fn) == (DPI, 3):
            self.sensor = int.from_bytes(payload[1:3], "big")
            answer = b""
        elif (feature, fn) == (DPI, 1):
            answer = struct.pack(">BHHH", 0, 200, 0xE032, 12000)
        else:
            raise AssertionError((feature, fn, payload))
        return answer.ljust(16, b"\0")

    def has_onboard_layout(self):
        return True

    dpi_values = HidDevice.dpi_values
    sensor_choices = HidDevice.sensor_choices

    def close(self):
        self.closed = True


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.mouse = TestMouse()
        self.backend = MouseBackend()
        self.backend._connections[MOUSE.id] = self.mouse

    def test_read_after_physical_press_and_temporary_shift(self):
        self.assertEqual(self.backend.read(MOUSE.id).dpi, 800)
        self.mouse.index, self.mouse.sensor = 2, 1600
        self.assertEqual(self.backend.read(MOUSE.id).dpi, 1600)
        self.mouse.sensor = 200
        reading = self.backend.read(MOUSE.id)
        self.assertEqual((reading.dpi, reading.active_stage), (200, 2))
        self.mouse.sensor = 0
        self.assertEqual(self.backend.read(MOUSE.id).dpi, 1600)
        self.assertTrue(all(fn in {2, 4, 5, 11} for feature, fn, data in self.mouse.calls))

    def test_supported_choices_and_live_host_switch(self):
        self.mouse.mode = 2
        state = self.backend.set_dpi(MOUSE.id, 450)
        self.assertEqual(state.dpi, 450)
        self.assertIn(12000, state.dpi_values)
        self.assertIsNone(state.active_stage)
        self.assertIn((DPI, 3, b"\0\x01\xc2"), self.mouse.calls)
        with self.assertRaises(ValueError):
            self.backend.set_dpi(MOUSE.id, 451)

    def test_onboard_selection_and_rejected_out_of_profile_dpi(self):
        self.assertEqual(self.backend.set_dpi(MOUSE.id, 1600).active_stage, 2)
        self.assertIn((PROFILES, 12, b"\x02"), self.mouse.calls)
        self.assertFalse(any(feature == DPI and fn == 3 for feature, fn, data in self.mouse.calls))
        with self.assertRaises(ValueError):
            self.backend.set_dpi(MOUSE.id, 600)

    def test_select_and_verify_onboard_step_with_stale_sensor(self):
        original = self.mouse.query

        def stale_sensor(feature, fn, data=b""):
            if (feature, fn) == (DPI, 2):
                return struct.pack(">BHH", 0, 800, 400)
            return original(feature, fn, data)

        with patch.object(self.mouse, "query", side_effect=stale_sensor):
            changed = self.backend.set_dpi(MOUSE.id, 1600)
            self.assertEqual((changed.dpi, changed.stages[changed.active_stage]), (800, 1600))
            restored = self.backend.set_dpi(MOUSE.id, 800)
            self.assertEqual(restored.active_stage, 1)
            self.assertIn((PROFILES, 12, bytes([1])), self.mouse.calls)

    def test_working_handle_remains_available_if_only_sensor_times_out(self):
        info = MouseInfo("mx", "MX Master", "MX Master", False, False)
        self.mouse.node = _Node(info, NODE.path, 1, False, "device")
        self.backend._connections = {"mx": self.mouse}
        original = self.mouse.query

        def timeout(feature, fn, data=b""):
            if (feature, fn) == (DPI, 2):
                raise HardwareError("Sensor timeout")
            if feature == 0:
                return bytes([5])
            return original(feature, fn, data)

        with patch.object(self.mouse, "query", side_effect=timeout):
            reading = self.backend.read("mx")
            self.assertIsNone(reading.dpi)
            self.assertTrue(reading.mouse.has_protocol)
            with self.backend.device("mx") as device:
                self.assertIs(device, self.mouse)

    def test_unwritten_g305_page_uses_factory_page(self):
        self.mouse.unwritten = True
        self.assertEqual(_state(self.mouse, MOUSE).dpi, 800)
        self.assertIn((PROFILES, 5, b"\x01\x01\0\0"), self.mouse.calls)

    def test_stale_connections_are_discarded_on_replug(self):
        replacement = _Node(MOUSE, NODE.path, 1, False, "new-instance")
        with patch("mouse_control.hardware._enumerate_nodes", return_value={MOUSE.id: [replacement]}):
            self.backend.discover()
        self.assertTrue(self.mouse.closed)
        self.assertFalse(self.backend._connections)

    def test_protocol_endpoint_filter_and_receiver_deduplication(self):
        protocol = bytes.fromhex("0600ff0902a10185117508951309029100c0")
        self.assertTrue(_has_protocol_output(protocol))
        self.assertFalse(_has_protocol_output(bytes.fromhex("85118100")))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for n, product, phys, desc in (
                (0, "C53F", "usb-1/input0", bytes.fromhex("05010906a101c0")),
                (2, "C53F", "usb-1/input2", protocol),
                (7, "4074", "usb-1/input2:1", protocol),
                (8, "C08B", "usb-2/input1", protocol),
            ):
                node = root / f"hidraw{n}" / "device"
                node.mkdir(parents=True)
                (node / "uevent").write_text(f"HID_ID=0003:0000046D:0000{product}\nHID_PHYS={phys}\n")
                (node / "report_descriptor").write_bytes(desc)
            detected = _enumerate_nodes(root)
        self.assertEqual(len(detected), 2)
        self.assertEqual([c.path.name for c in detected["G305@usb-1#1"]], ["hidraw7", "hidraw2"])

    def test_legacy_register_probe_uses_wired_zero_address_and_short_g5_query(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root / "hidraw0" / "device"
            caps = device / "input/input0/capabilities"
            caps.mkdir(parents=True)
            (device / "uevent").write_text("HID_ID=0003:0000046D:0000C041\nHID_PHYS=usb-p/input0\nHID_NAME=G5\n")
            (device / "report_descriptor").write_bytes(bytes.fromhex("85109100"))
            (caps / "rel").write_text("3")
            word, bit = divmod(0x110, struct.calcsize("L") * 8)
            (caps / "key").write_text(" ".join([format(1 << bit, "x")] + ["0"] * word))
            nodes = _enumerate_nodes(root)
        mouse_id, candidates = next(iter(nodes.items()))
        self.assertEqual([entry.address for entry in candidates], [0, 255])
        self.backend._targets = nodes
        with patch("mouse_control.hardware.HidDevice") as constructor:
            self.backend._open(mouse_id)
            constructor.assert_called_once_with(candidates[0])
            constructor.return_value.exchange_raw.assert_called_once_with(0x81, 0x63)

    def test_other_mouse_uses_only_sensor_feature_and_refreshes_choices_before_write(self):
        generic = MouseInfo("m", "Logitech MX", "Logitech MX", False, False)
        self.mouse.node = _Node(generic, NODE.path, 2, False, "same-port")
        backend = MouseBackend()
        backend._connections["m"] = self.mouse
        reading = backend.read("m")
        self.assertEqual(reading.dpi, 800)
        self.assertFalse(reading.can_edit_profile)
        self.assertTrue(reading.can_set_dpi)
        self.assertIsNone(reading.onboard)
        before = len(self.mouse.calls)
        self.assertEqual(backend.set_dpi("m", 1200).dpi, 1200)
        self.assertIn((DPI, 1, b"\0"), self.mouse.calls[before:])
        self.assertTrue(all(feature == DPI for feature, fn, payload in self.mouse.calls))

    def test_generic_sensor_with_unusable_range_stays_read_only(self):
        generic = MouseInfo("m", "Logitech MX", "Logitech MX", False, False)
        self.mouse.node = _Node(generic, NODE.path, 2, False, "same-port")
        original_query = self.mouse.query

        def malformed(feature, fn, payload=b""):
            if (feature, fn) == (DPI, 1):
                return struct.pack(">BHHH", 0, 20, 0xE001, 40000)
            return original_query(feature, fn, payload)

        with patch.object(self.mouse, "query", side_effect=malformed):
            backend = MouseBackend()
            backend._connections["m"] = self.mouse
            reading = backend.read("m")
            self.assertFalse(reading.can_set_dpi)
            self.assertEqual(reading.dpi, 800)
            with self.assertRaisesRegex(ValueError, "read-only"):
                backend.set_dpi("m", 1200)
            self.assertTrue(all(fn == 2 for feature, fn, payload in self.mouse.calls))

    def test_separate_wireless_slots_and_ignore_non_pointer_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for n, name, slot, rel in [(1, "Logitech MX", 1, 3), (2, "Logitech MX", 2, 3),
                                       (3, "Logitech Keyboard", 3, 3), (4, "Receiver", 4, 0)]:
                dev = root / f"hidraw{n}" / "device"
                caps = dev / "input" / f"input{n}" / "capabilities"
                caps.mkdir(parents=True)
                (dev / "uevent").write_text(f"HID_ID=0003:0000046D:0000BEEF\nHID_PHYS=usb-p/input2:{slot}"
                                             f"\nHID_NAME={name}\n")
                (dev / "report_descriptor").write_bytes(bytes.fromhex("85119100"))
                (caps / "rel").write_text(str(rel))
                bits = struct.calcsize("L") * 8
                word, bit = divmod(0x110, bits)
                (caps / "key").write_text(" ".join([format(1 << bit, "x")] + ["0"] * word))
            found = _enumerate_nodes(root)
        self.assertEqual(list(found), ["Logitech MX@usb-p#1", "Logitech MX@usb-p#2"])
        self.assertTrue(all(not nodes[0].info.can_set_dpi for nodes in found.values()))

    def test_bluetooth_mice_use_remote_identity_and_alternate_addresses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for n in (1, 2):
                device = root / f"hidraw{n}" / "device"
                caps = device / "input" / f"input{n}" / "capabilities"
                caps.mkdir(parents=True)
                (device / "uevent").write_text(f"HID_ID=0005:0000046D:0000BEEF\nHID_PHYS=local-adapter"
                                                f"\nHID_UNIQ=remote-{n}\nHID_NAME=MX Anywhere\n")
                (device / "report_descriptor").write_bytes(bytes.fromhex("85119100"))
                (caps / "rel").write_text("3")
                word, bit = divmod(0x110, struct.calcsize("L") * 8)
                (caps / "key").write_text(" ".join([format(1 << bit, "x")] + ["0"] * word))
            found = _enumerate_nodes(root)
        self.assertEqual(len(found), 2)
        self.assertEqual([c.address for c in found["MX Anywhere@remote-1"]], [255, 0])

    def test_generic_device_cannot_send_profile_commands(self):
        device = HidDevice.__new__(HidDevice)
        info = MouseInfo("m", "Mouse", "Mouse", False, False)
        device.node = _Node(info, NODE.path, 1, False, "device")
        with patch.object(device, "_exchange") as exchange:
            with self.assertRaises(HardwareError):
                device.query(PROFILES, 6)
            exchange.assert_not_called()

    def test_transport_handles_other_clients_and_reports_device_errors(self):
        fake = HidDevice.__new__(HidDevice)
        fake._fd, fake.node, fake._software_id = 10, NODE, 7
        fake._features = {0: 0}
        replies = [
            bytes.fromhex("11010000000000"),  # unrelated notification
            bytes.fromhex("11010007090000"),  # root lookup returns feature index 9
            bytes.fromhex("11010926001234"),  # another client's response
            bytes.fromhex("11010927000320"),  # our actual response
            bytes.fromhex("1101ff09270800"),  # subsequent busy error
        ]
        with patch("mouse_control.hardware.select.select", return_value=([10], [], [])), \
             patch("mouse_control.hardware.os.write", return_value=20) as writes, \
             patch("mouse_control.hardware.os.read", side_effect=replies):
            self.assertEqual(fake.query(DPI, 2, b"\x00"), bytes.fromhex("000320"))
            self.assertEqual(writes.call_args_list[0].args[1][:6], bytes.fromhex("110100072201"))
            with self.assertRaises(ProtocolError) as caught:
                fake.query(DPI, 2)
            self.assertEqual(caught.exception.code, 8)
            self.assertEqual(writes.call_count, 3)

    def test_physical_event_retained_during_dpi_request_even_after_release(self):
        device = HidDevice.__new__(HidDevice)
        device._fd, device.node, device._software_id = 10, NODE, 7
        device._features = {DPI: 9}
        device.monitor = ButtonMonitor(())
        device.monitor.physical_count = 6
        device.monitor.physical_index = 8
        replies = [bytes.fromhex("11010800002000"), bytes.fromhex("11010800000000"),
                   bytes.fromhex("11010807000100"), bytes.fromhex("11010927000320")]
        with patch("mouse_control.hardware.select.select", return_value=([10], [], [])), \
             patch("mouse_control.hardware.os.write", return_value=20), \
             patch("mouse_control.hardware.os.read", side_effect=replies):
            device.query(DPI, 2)
        state = device.monitor.sample()
        self.assertEqual(state.physical_held, ())
        self.assertEqual(state.physical_pressed, (5,))
        self.assertEqual(state.source, "physical")
        self.assertEqual(device.monitor.sample().physical_pressed, ())

    def test_button_stream_start_and_stop_never_change_mode_or_mapping(self):
        device = HidDevice.__new__(HidDevice)
        device.node, device.model, device.monitor = NODE, "G305", None
        device._features = {0x8110: 8}
        with patch.object(device, "query", side_effect=[bytes([6]), bytes(16), bytes(16)]) as calls:
            device.start_monitor()
            device.start_monitor()
            self.assertEqual(device.monitor.sample().source, "waiting")
            device.stop_monitor()
            self.assertIsNone(device.monitor)
        self.assertEqual([c.args for c in calls.call_args_list], [(0x8110, 0), (0x8110, 1), (0x8110, 2)])

    def test_mapped_fallback_accumulates_taps_and_ignores_keyboard_events(self):
        monitor = ButtonMonitor(())
        read_fd, write_fd = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            monitor._inputs[read_fd] = set()
            events = [(1, 0x110, 1), (1, 0x110, 0), (1, 28, 1), (1, 0x111, 1)]
            os.write(write_fd, b"".join(_EVENT.pack(0, 0, *event) for event in events))
            state = monitor.sample()
            self.assertEqual(state.mapped_pressed, ("BTN_LEFT", "BTN_RIGHT"))
            self.assertEqual(state.mapped_held, ("BTN_RIGHT",))
            self.assertEqual(state.physical_pressed, ())
            self.assertEqual(state.source, "mapped")
            self.assertEqual(monitor.sample().mapped_pressed, ())
        finally:
            os.close(write_fd)
            monitor.close()

    def test_pointer_only_input_without_hidraw_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dev = root / "input/event42/device"
            (input_dev / "id").mkdir(parents=True)
            (input_dev / "capabilities").mkdir()
            for field, value in {"id/vendor": "046d", "id/product": "c246", "id/bustype": "0003",
                                 "name": "Logitech G300s", "phys": "usb-p/input0", "uniq": "",
                                 "capabilities/rel": "3"}.items():
                (input_dev / field).write_text(value)
            word, bit = divmod(0x110, struct.calcsize("L") * 8)
            (input_dev / "capabilities/key").write_text(" ".join([format(1 << bit, "x")] + ["0"] * word))
            backend = MouseBackend()
            backend._targets = _enumerate_nodes(root / "hidraw", root / "dev")
            mouse_id = next(iter(backend._targets))
            self.assertIsNone(backend.read(mouse_id).dpi)
            backend.enable_button_monitor(mouse_id)
            self.assertEqual(backend.buttons(mouse_id).source, "unavailable")
            backend.disable_button_monitor(mouse_id)
            backend.close()

    def test_timeout_drops_handle_to_allow_retry(self):
        with patch.object(self.mouse, "query", side_effect=HardwareError("offline")):
            with self.assertRaisesRegex(HardwareError, "offline"):
                self.backend.read(MOUSE.id)
        self.assertTrue(self.mouse.closed)
        self.assertNotIn(MOUSE.id, self.backend._connections)

    def test_g400_live_read_write_and_passive_physical_buttons(self):
        self.mouse.node = _Node(MOUSE, NODE.path, 255, False, "g400", older_mice.G400)
        codes = {0x8E: 4, 0x20: 2}

        def feature(device, report_id, new_code=None):
            if new_code is not None:
                codes[report_id] = new_code
            return codes[report_id]

        with patch.object(older_mice, "_feature", side_effect=feature):
            self.assertEqual(self.backend.read(MOUSE.id).dpi, 800)
            codes[0x8E] = 5
            self.assertEqual(self.backend.read(MOUSE.id).dpi, 1800)
            self.assertEqual(self.backend.set_dpi(MOUSE.id, 400).dpi, 400)
            codes[0x8E] = 7
            self.assertFalse(self.backend.read(MOUSE.id).can_set_dpi)
            older_mice.set_report_rate(self.mouse, 500)
            self.assertEqual(older_mice.report_rates(self.mouse)[0], 500)
        monitor = ButtonMonitor([])
        monitor.legacy_bitmap = True
        monitor.physical_count = 8
        monitor.observe(bytes([0x80, 0x40]), 0xFF)
        monitor.observe(bytes([0x80, 0]), 0xFF)
        self.assertEqual(monitor.sample().physical_pressed, (6,))

    def test_g400s_preserves_driver_mode_and_rejects_unverified_levels(self):
        node = _Node(MOUSE, NODE.path, 255, False, "g400s", older_mice.G400S, (), Path("/dev/bus/usb/001/002"))
        device = SimpleNamespace(node=node)
        with patch.object(older_mice, "_feature", side_effect=[0x85, 0x85, 0x84]), \
                patch("os.access", return_value=True), patch("os.open", return_value=3), patch("os.close"), \
                patch.object(older_mice, "_usb_resolution") as write:
            self.assertEqual(older_mice.set_dpi(device, 800).dpi, 800)
            write.assert_called_once_with(3, older_mice.G400S, 0x84)
        with patch.object(older_mice, "_feature", return_value=0x87):
            self.assertIsNone(older_mice.read_state(device, MOUSE).dpi)

    def test_g602_uses_the_first_generation_sensor_number(self):
        self.mouse.node = _Node(MOUSE, NODE.path, 1, False, "g602", older_mice.G602)
        with patch.object(self.mouse, "query", return_value=bytes.fromhex("0107d0")) as query:
            state = self.backend.read(MOUSE.id)
            self.assertEqual(state.dpi, 2000)
            self.assertFalse(state.can_set_dpi)
            query.assert_called_once_with(0x2201, 2, bytes([1]))

    def test_mx518_read_and_write_use_only_device_recipient_resolution_requests(self):
        from ctypes import cast, POINTER, c_ubyte
        descriptor = bytearray(18)
        descriptor[:2] = bytes([18, 1])
        struct.pack_into("<HH", descriptor, 8, 0x046D, 0xC01E)
        code = 4
        calls = []

        def ioctl(fd, operation, data, mutate):
            nonlocal code
            request = older_mice._UsbControl.from_buffer(data)
            calls.append((request.direction, request.request, request.value, request.index))
            if request.length:
                cast(request.data, POINTER(c_ubyte))[0] = code
            else:
                code = request.index
            return request.length

        self.mouse.node = _Node(MOUSE, NODE.path, 0, False, "mx518", 0xC01E)
        self.mouse._fd = 3
        with patch("os.pread", return_value=descriptor), patch("fcntl.ioctl", side_effect=ioctl):
            self.assertEqual(self.backend.read(MOUSE.id).dpi, 800)
            self.assertEqual(self.backend.set_dpi(MOUSE.id, 1600).dpi, 1600)
            self.assertEqual(calls.count((0x40, 2, 0x0E, 6)), 1)
            self.assertEqual(set(calls), {(0x40, 2, 0x0E, 6), (0xC0, 1, 0x0E, 0)})
        with patch("os.pread", return_value=bytes(18)), patch("fcntl.ioctl") as ioctl:
            with self.assertRaises(HardwareError):
                older_mice._usb_resolution(3, 0xC01E, 6)
            ioctl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
