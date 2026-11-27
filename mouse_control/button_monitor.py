"""Selected-mouse telemetry. Event descriptors are private, filtered, and never grabbed."""

import ctypes
import fcntl
import os
import struct
from dataclasses import dataclass

BUTTON_NAMES = ("BTN_LEFT", "BTN_RIGHT", "BTN_MIDDLE", "BTN_SIDE", "BTN_EXTRA",
                "BTN_FORWARD", "BTN_BACK", "BTN_TASK")
_REPORT_BUTTON_NAMES = BUTTON_NAMES + tuple(f"Button {i}" for i in range(9, 17))
_FIRST = 0x110
_EVENT = struct.Struct("@llHHi")


@dataclass(frozen=True)
class ButtonState:
    physical_held: tuple[int, ...]
    physical_pressed: tuple[int, ...]
    mapped_held: tuple[str, ...]
    mapped_pressed: tuple[str, ...]
    source: str
    detail: str


def _set_filter(fd, event_type, bits):
    # EVIOCSMASK affects only this open descriptor, leaving desktop input intact.
    array = ctypes.create_string_buffer(bits, len(bits))
    argument = struct.pack("=IIQ", event_type, len(bits), ctypes.addressof(array))
    fcntl.ioctl(fd, 0x40104593, argument)


def _held_buttons(fd):
    keys = bytearray((_FIRST + len(BUTTON_NAMES) + 7) // 8)
    fcntl.ioctl(fd, 0x80004518 | len(keys) << 16, keys, True)
    return {i for i in range(len(BUTTON_NAMES)) if keys[(_FIRST + i) // 8] & 1 << (i % 8)}


def _pointer_report_length(descriptor):
    """Confirm the G305 paired child's 16 standard action bits at the start of input ID 2."""
    report = page = size = count = 0
    usage = low = high = None
    scopes = []
    collections = []
    found = False
    total = 0
    pos = 0
    while pos < len(descriptor):
        prefix = descriptor[pos]
        if prefix == 0xFE:
            return None
        end = pos + 1 + (0, 1, 2, 4)[prefix & 3]
        if end > len(descriptor):
            return None
        tag = prefix & 0xFC
        value = int.from_bytes(descriptor[pos+1:end], "little")
        pos = end
        if tag == 0x04:
            page = value
        elif tag == 0x74:
            size = value
        elif tag == 0x84:
            report = value
        elif tag == 0x94:
            count = value
        elif tag == 0xA4:
            scopes.append((report, page, size, count))
        elif tag == 0xB4:
            if not scopes:
                return None
            report, page, size, count = scopes.pop()
        elif tag == 0x08:
            usage = (page, value)
        elif tag == 0x18:
            low = (page, value)
        elif tag == 0x28:
            high = (page, value)
        elif tag == 0xA0:
            collections.append((value, usage))
        elif tag == 0xC0:
            if not collections:
                return None
            collections.pop()
        elif tag == 0x80 and report == 2:
            apps = [item for item in collections if item[0] == 1]
            if apps != [(1, (1, 2))] or (0, (1, 1)) not in collections:
                return None  # This ID is shared with an unrelated application collection.
            if total == 0:
                found = ((value & 7) == 2 and size == 1 and count == 16
                         and low == (9, 1) and high == (9, 16))
            total += size * count
            if total > 63 * 8:
                return None
        if prefix & 0x0C == 0:  # Each Main item consumes its local usage declarations.
            low = high = usage = None
    if found and not collections and not scopes and total % 8 == 0:
        return 1 + total // 8
    return None


class ButtonMonitor:
    def __init__(self, paths):
        self._inputs = {}
        self._dropped = set()
        self._mapped_pressed = set()
        self._pointer_size = None
        self._report_held = set()
        self._physical = 0
        self._physical_pressed = 0
        self.seen_physical = False
        self.physical_index = None
        self.physical_count = 0
        self.legacy_bitmap = False
        self.streaming = False
        for path in paths:
            fd = None
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                key_mask = bytearray(_FIRST // 8 + 1)
                key_mask[-1] = 0xFF
                _set_filter(fd, 1, bytes(key_mask))  # EV_KEY, mouse buttons only
                _set_filter(fd, 0, b"\x03")  # event types EV_SYN + EV_KEY
                self._inputs[fd] = _held_buttons(fd)
            except OSError:
                if fd is not None:
                    os.close(fd)

    def attach_pointer_report(self, descriptor):
        self._pointer_size = _pointer_report_length(descriptor)
        self._report_held.clear()
        return self._pointer_size is not None

    def observe(self, frame, address):
        if self._pointer_size is not None and len(frame) == self._pointer_size and frame[0] == 2:
            # Only button fields are decoded. Keyboard reports and movement are ignored.
            mask = frame[1] | (frame[2] << 8)
            held = {i for i in range(16) if mask & 1 << i}
            self._mapped_pressed.update(held - self._report_held)
            self._report_held = held
            return
        if self.legacy_bitmap and len(frame) == 2 and frame[0] == 0x80:
            mask = frame[1]
        elif (self.physical_index is not None and len(frame) >= 6 and frame[0] in (0x10, 0x11)
              and frame[1] == address and frame[2] == self.physical_index and frame[3] == 0):
            mask = int.from_bytes(frame[4:6], "big") & ((1 << self.physical_count) - 1)
        else:
            return
        self._physical_pressed |= mask & ~self._physical
        self._physical = mask
        self.seen_physical = True

    def _read_input(self, fd):
        # Limit processing per sample so a faulty input device cannot starve the UI.
        for _ in range(16):
            try:
                chunk = os.read(fd, _EVENT.size * 64)
            except BlockingIOError:
                return
            if not chunk:
                raise OSError("Pointer disconnected")
            for _, _, kind, code, value in _EVENT.iter_unpack(chunk):
                if kind == 0 and code == 3:  # SYN_DROPPED: queue overran
                    self._dropped.add(fd)
                elif fd in self._dropped:
                    if kind == 0 and code == 0:
                        self._inputs[fd] = _held_buttons(fd)
                        self._dropped.discard(fd)
                elif kind == 1 and _FIRST <= code < _FIRST + len(BUTTON_NAMES):
                    index = code - _FIRST
                    if value == 1:
                        if index not in self._inputs[fd]:
                            self._mapped_pressed.add(index)
                        self._inputs[fd].add(index)
                    elif value == 0:
                        self._inputs[fd].discard(index)

    def sample(self):
        for fd in list(self._inputs):
            try:
                self._read_input(fd)
            except (OSError, struct.error):
                os.close(fd)
                self._inputs.pop(fd)
                self._dropped.discard(fd)
        mapped = self._report_held.union(*self._inputs.values())
        if self.seen_physical:
            source, detail = "physical", "Physical button reports from the mouse."
        elif self._pointer_size is not None:
            source, detail = "mapped", "Mapped mouse button actions. Firmware-only controls may be invisible."
        elif self._inputs:
            source, detail = "mapped", "Linux pointer actions. Firmware-only buttons may be invisible."
        elif self.streaming or self.legacy_bitmap:
            source, detail = "waiting", "Listening for the first physical button report."
        else:
            source, detail = "unavailable", "No accessible mouse button reporting interface."
        state = ButtonState(tuple(i for i in range(self.physical_count) if self._physical & (1 << i)),
                            tuple(i for i in range(self.physical_count) if self._physical_pressed & (1 << i)),
                            tuple(_REPORT_BUTTON_NAMES[i] for i in sorted(mapped)),
                            tuple(_REPORT_BUTTON_NAMES[i] for i in sorted(self._mapped_pressed)), source, detail)
        self._mapped_pressed.clear()
        self._physical_pressed = 0
        return state

    def close(self):
        for fd in self._inputs:
            os.close(fd)
        self._inputs.clear()
