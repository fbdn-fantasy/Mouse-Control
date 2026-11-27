import unittest
from mouse_control.button_monitor import ButtonMonitor


# Mouse Application / Physical Pointer, report 2. The 16 button bits precede
# two relative axes. A distinct keyboard application also shares this descriptor.
POINTER = bytes.fromhex("05 01 09 02 a1 01 85 02 09 01 a1 00 05 09 19 01 29 10 "
                        "15 00 25 01 95 10 75 01 81 02 05 01 09 30 09 31 95 02 "
                        "75 10 81 06 c0 c0")
KEYBOARD = bytes.fromhex("05 01 09 06 a1 01 85 01 05 07 19 00 29 65 95 06 75 08 81 00 c0")


class PointerReportTests(unittest.TestCase):
    def test_short_click_survives_hardware_poll_and_motion_is_ignored(self):
        monitor = ButtonMonitor(())
        self.assertTrue(monitor.attach_pointer_report(KEYBOARD + POINTER))
        monitor.observe(bytes((1, 9, 255, 5, 4, 0, 0)), 1)  # keyboard
        monitor.observe(bytes((2, 9, 0, 255, 255, 13, 37)), 1)  # primary and side
        monitor.observe(bytes((2, 0, 0, 255, 255, 255, 255)), 1)  # released
        state = monitor.sample()
        self.assertEqual(state.mapped_pressed, ("BTN_LEFT", "BTN_SIDE"))
        self.assertEqual(state.mapped_held, ())
        self.assertEqual(state.physical_pressed, ())
        self.assertEqual(state.source, "mapped")
        self.assertEqual(monitor.sample().mapped_pressed, ())
        monitor.observe(bytes((2, 2, 1, 0, 0, 0, 0)), 1)
        self.assertEqual(monitor.sample().mapped_held, ("BTN_RIGHT", "Button 9"))

    def test_only_confirmed_standard_button_field_under_pointer_collection(self):
        for invalid in (b"", KEYBOARD, POINTER[:-1], POINTER.replace(b"\x81\x02", b"\x81\x03"),
                        POINTER.replace(b"\x05\x09", b"\x05\x07"),
                        POINTER + KEYBOARD.replace(b"\x85\x01", b"\x85\x02")):
            monitor = ButtonMonitor(())
            self.assertFalse(monitor.attach_pointer_report(invalid))
            monitor.observe(b"\x02\xff\xff\x00\x00\x00\x00", 1)
            self.assertEqual(monitor.sample().mapped_pressed, ())
