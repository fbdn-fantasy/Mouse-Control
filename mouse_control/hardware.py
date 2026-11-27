"""Native Linux HID++ mouse discovery, live sensor readings, and DPI controls."""

from __future__ import annotations

import os
import fcntl
import secrets
import select
import struct
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import legacy_reports, older_mice, sensors
from .button_monitor import ButtonMonitor, ButtonState
from .catalog import identify
from .models import button_count, layout_labels, model_for_product

DPI = 0x2201
PROFILES = 0x8100
_LEGACY_REGISTER_MICE = {0xC041, 0xC049, 0xC048, 0xC066, 0xC249, 0xC068, 0xC24E, 0xC06B, 0xC07C}


class HardwareError(Exception):
    """A hardware request cannot currently be completed."""


class UnsupportedFeature(HardwareError):
    pass


class UnsupportedDpiRange(HardwareError):
    """The sensor's advertised DPI choices could not be validated."""


class ProtocolError(HardwareError):
    """The mouse explicitly rejected a request."""

    def __init__(self, feature, command, code):
        self.code = code
        super().__init__(f"Mouse rejected command {feature:02x}/{command >> 4:x} "
                         f"(device error 0x{code:02x}).")


@dataclass(frozen=True)
class MouseInfo:
    id: str
    name: str
    model: str
    can_set_dpi: bool = True
    can_edit_profile: bool = True
    has_protocol: bool = True


@dataclass(frozen=True)
class MouseState:
    mouse: MouseInfo
    dpi: int | None
    dpi_values: tuple[int, ...]
    stages: tuple[int, ...]
    active_stage: int | None
    profile: int | None
    onboard: bool | None
    detail: str = ""

    @property
    def can_set_dpi(self):
        return bool(self.dpi_values)

    @property
    def can_edit_profile(self):
        return self.mouse.can_edit_profile and self.onboard is True


@dataclass(frozen=True)
class _Node:
    info: MouseInfo
    path: Path
    address: int
    receiver: bool
    identity: str
    product: int = 0
    events: tuple[Path, ...] = ()
    control_path: Path | None = None


def _has_report(descriptor, tag_needed, ids):
    index = report_id = 0
    stack = []
    while index < len(descriptor):
        prefix = descriptor[index]
        if prefix == 0xFE:
            if index + 2 >= len(descriptor):
                break
            index += 3 + descriptor[index + 1]
            continue
        size = (0, 1, 2, 4)[prefix & 3]
        data = descriptor[index + 1:index + 1 + size]
        if len(data) != size:
            return False
        tag = prefix & 0xFC
        if tag == 0x84:
            report_id = int.from_bytes(data, "little")
        elif tag == 0xA4:
            stack.append(report_id)
        elif tag == 0xB4 and stack:
            report_id = stack.pop()
        elif tag == tag_needed and report_id in ids:
            return True
        index += size + 1
    return False


def _has_protocol_output(descriptor, allow_short=False):
    return _has_report(descriptor, 0x90, (0x10, 0x11) if allow_short else (0x11,))


def _pointer_caps(caps):
    if int((caps / "rel").read_text().split()[-1], 16) & 3 != 3:
        return False
    words = [int(v, 16) for v in reversed((caps / "key").read_text().split())]
    word, bit = divmod(0x110, struct.calcsize("L") * 8)
    return word < len(words) and bool((words[word] >> bit) & 1)


def _location(bus, phys, uniq, instance):
    if bus == 5:
        # HID_PHYS can name the local Bluetooth adapter. HID_UNIQ is the peer.
        return uniq or instance, None
    port = phys.partition("/input")[0] or instance
    tail = phys.rsplit("/", 1)[-1]
    slot = int(tail.split(":")[1], 16) if tail.startswith("input") and ":" in tail else None
    return port, slot


@dataclass
class _Interface:
    path: Path
    bus: int
    product: int
    name: str
    port: str
    slot: int | None
    instance: str
    pointer: bool
    protocol: bool
    events: tuple[Path, ...]


def _hardware_interfaces(sysroot, devroot):
    for entry in sorted(sysroot.glob("hidraw*")):
        device = entry / "device"
        try:
            props = dict(line.split("=", 1) for line in (device / "uevent").read_text().splitlines()
                         if "=" in line)
            bus, vendor, product = (int(part, 16) for part in props["HID_ID"].split(":"))
            if bus not in (3, 5) or vendor != 0x046D:
                continue
            instance = str(device.resolve(strict=True))
            port, slot = _location(bus, props.get("HID_PHYS", ""), props.get("HID_UNIQ", ""), instance)
            if slot is not None and not 1 <= slot <= 6:
                continue
            inputs = [i for i in device.glob("input/input*") if _pointer_caps(i / "capabilities")]
            events = tuple(devroot / "input" / e.name for i in inputs for e in i.glob("event*"))
            descriptor = (device / "report_descriptor").read_bytes()
            if product in legacy_reports.PRODUCTS:
                protocol = legacy_reports.supports_descriptor(product, descriptor)
            elif product in older_mice.G400_FAMILY:
                protocol = _has_report(descriptor, 0xB0, (0x8E,))
            elif product in older_mice.MX518:
                protocol = False  # This protocol uses usbfs, not hidraw.
            else:
                protocol = _has_protocol_output(descriptor, product in _LEGACY_REGISTER_MICE)
            yield _Interface(devroot / entry.name, bus, product, props.get("HID_NAME", "").strip(),
                             port, slot, instance, bool(inputs), protocol, events)
        except (OSError, KeyError, ValueError, IndexError):
            continue


def _pointer_interfaces(inputroot, devroot):
    # Some older mice have only an evdev pointer and no HID++ interface.
    for event in sorted(inputroot.glob("event*")):
        device = event / "device"
        try:
            vendor = int((device / "id/vendor").read_text(), 16)
            if vendor != 0x046D or not _pointer_caps(device / "capabilities"):
                continue
            bus = int((device / "id/bustype").read_text(), 16)
            if bus not in (3, 5):
                continue
            product = int((device / "id/product").read_text(), 16)
            name = (device / "name").read_text().strip()
            instance = str(device.resolve(strict=True))
            port, slot = _location(bus, (device / "phys").read_text().strip(),
                                   (device / "uniq").read_text().strip(), instance)
            path = devroot / "input" / event.name
            yield _Interface(path, bus, product, name, port, slot, instance, True, False, (path,))
        except (OSError, ValueError, IndexError):
            continue


def _enumerate_nodes(sysroot=Path("/sys/class/hidraw"), devroot=Path("/dev")):
    interfaces = list(_hardware_interfaces(sysroot, devroot))
    interfaces.extend(_pointer_interfaces(sysroot.parent / "input", devroot))
    pointers = {}
    for dev in interfaces:
        if dev.pointer:
            pointers.setdefault((dev.product, dev.port, dev.slot), set()).update(dev.events)
    nodes = {}
    for dev in interfaces:
        receiver = dev.product == 0xC53F and dev.protocol
        key = dev.product, dev.port, dev.slot
        known = model_for_product(dev.product, dev.name)
        if receiver:
            known = "G305"
        if not known and (key not in pointers or not dev.name):
            continue
        if not known and any(word in dev.name.casefold() for word in ("receiver", "keyboard", "headset")):
            continue
        if not dev.protocol and key not in pointers:
            continue
        display_model = identify(dev.product, dev.name)
        model = known or display_model or dev.name
        name = f"Logitech {display_model or known}" if display_model or known else dev.name
        if receiver:
            name = "Logitech G305 LIGHTSPEED"
        slot = (dev.slot or 1) if model == "G305" else dev.slot
        location = f"{dev.port}#{slot}" if slot is not None else dev.port
        editable = bool(known) or dev.product in legacy_reports.PRODUCTS
        info = MouseInfo(f"{model}@{location}", name, model, editable or dev.product in older_mice.DIRECT,
                         editable, dev.protocol)
        events = pointers.get((0x4074, dev.port, 1), set()) if receiver else pointers.get(key, set())
        control_path = older_mice.usb_path(dev.instance, dev.product, devroot)
        if dev.product in older_mice.MX518 and control_path:
            controls = MouseInfo(info.id, name, model, True, False, True)
            usb = _Node(controls, control_path, 0, False, str(control_path), dev.product, tuple(sorted(events)))
            if usb not in nodes.setdefault(info.id, []):
                nodes[info.id].append(usb)
        addresses = (0xFF, 0) if dev.bus == 5 else (slot or 0xFF,)
        if dev.bus == 3 and dev.product in _LEGACY_REGISTER_MICE and slot is None:
            addresses = (0, 0xFF)
        elif dev.product == 0xC088 and slot is None:
            addresses = (0xFF, 1)
        for address in addresses:
            node = _Node(info, dev.path, address, receiver, dev.instance, dev.product, tuple(sorted(events)), control_path)
            nodes.setdefault(info.id, []).append(node)
    for candidates in nodes.values():
        candidates.sort(key=lambda node: (not node.info.has_protocol, node.receiver))
    return nodes


class HidDevice:
    def __init__(self, node):
        self.node = node
        self.model = node.info.model
        self._features = {0: 0}
        self._sensor_choices = None
        self._verified_layout = None
        # ID 0 is reserved for device notifications and 1 for the kernel.
        self._software_id = secrets.choice((2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15))
        self.monitor = None
        self._fd = os.open(node.path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC) if node.info.has_protocol else None
        if self._fd is not None and node.product not in older_mice.MX518:
            # hidraw numbers are reusable. Pin and identify the open descriptor
            # before addressing a device found during an earlier enumeration.
            try:
                identity = bytearray(8)
                fcntl.ioctl(self._fd, 0x80084803, identity, True)  # HIDIOCGRAWINFO
                bus, vendor, product = struct.unpack("=IHH", identity)
                if bus not in (3, 5) or vendor != 0x046D or product != node.product:
                    raise HardwareError("The mouse changed during discovery. Refreshing its connection.")
            except (HardwareError, OSError):
                os.close(self._fd)
                self._fd = None
                raise

    def start_monitor(self):
        if self.monitor is not None:
            return
        self.monitor = ButtonMonitor(self.node.events)
        if self.node.product == 0x4074 and self.node.info.has_protocol and not self.node.receiver:
            # The kernel exposes this paired mouse's normal pointer report
            # alongside HID++. Its descriptor defines where mapped buttons sit.
            try:
                descriptor = (Path(self.node.identity) / "report_descriptor").read_bytes()
            except OSError:
                pass  # Evdev and the optional physical stream remain available.
            else:
                self.monitor.attach_pointer_report(descriptor)
        count = button_count(self.model)
        if self.node.info.has_protocol and self.node.product in older_mice.G400_FAMILY:
            self.monitor.physical_count = 8
            self.monitor.legacy_bitmap = True
            return
        if (not self.node.info.has_protocol or self.node.product in _LEGACY_REGISTER_MICE
                or self.node.product in legacy_reports.PRODUCTS or self.node.product in older_mice.DIRECT):
            return
        try:
            reported = self.query(0x8110, 0)[0]
            if not 1 <= reported <= 16 or (count and reported != count):
                return
            self.monitor.physical_index = self._features[0x8110]
            self.monitor.physical_count = reported
            # Start the event stream only. The separate mapping and operating
            # mode commands would change ordinary mouse behavior.
            self.query(0x8110, 1)
            self.monitor.streaming = True
        except (ProtocolError, UnsupportedFeature):
            pass  # Continue to listen passively and expose mapped pointer events.

    def stop_monitor(self):
        monitor, self.monitor = self.monitor, None
        if monitor is None:
            return
        try:
            if monitor.streaming:
                self.query(0x8110, 2)
        finally:
            monitor.close()

    def close(self):
        try:
            self.stop_monitor()
        except (HardwareError, OSError):
            pass  # The device may have already disconnected.
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _exchange(self, feature, function, payload):
        return self._send(feature, function << 4 | self._software_id, payload, 0x11)

    def exchange_raw(self, sub_id, address, payload=b"", report_id=0x10):
        if self.node.product not in _LEGACY_REGISTER_MICE or address not in (0x63, 0x64):
            raise UnsupportedFeature("The legacy sensor register is unavailable for this model.")
        if sub_id not in (0x81, 0x83, 0x80, 0x82):
            raise ValueError("Invalid legacy register operation.")
        return self._send(sub_id, address, payload, report_id)

    def _send(self, sub_id, address, payload, report_id):
        if self._fd is None:
            raise UnsupportedFeature("This mouse has no accessible HID++ protocol endpoint.")
        size = {0x10: 3, 0x11: 16}.get(report_id)
        if size is None or len(payload) > size:
            raise ValueError("Invalid HID report length.")
        feature, command = sub_id, address
        address = self.node.address
        frame = bytes((report_id, address, feature, command)) + payload.ljust(size, b"\0")
        if os.write(self._fd, frame) != len(frame):
            raise HardwareError("Incomplete report sent to the mouse.")
        deadline = time.monotonic() + 0.9
        while (remaining := deadline - time.monotonic()) > 0:
            if not select.select([self._fd], [], [], remaining)[0]:
                break
            try:
                report = os.read(self._fd, 64)
            except BlockingIOError:
                continue
            if not report:
                raise HardwareError("Mouse disconnected.")
            if getattr(self, "monitor", None):
                self.monitor.observe(report, address)
            if len(report) < 7 or report[0] not in (0x10, 0x11) or report[1] != address:
                continue
            if report[2] in (0x8F, 0xFF) and report[3:5] == bytes((feature, command)):
                raise ProtocolError(feature, command, report[5])
            if report[2:4] == bytes((feature, command)):
                return report[4:]
        raise HardwareError("Mouse did not reply. Wake the mouse and check the USB connection.")

    def query(self, feature_id: int, function_number: int, payload: bytes = b"") -> bytes:
        if not 0 <= function_number < 16 or len(payload) > 16:
            raise ValueError("Invalid HID++ command.")
        if self.node.product in legacy_reports.PRODUCTS or self.node.product in older_mice.DIRECT:
            raise UnsupportedFeature("This mouse uses USB feature reports for its settings.")
        if not self.node.info.can_edit_profile and feature_id == PROFILES:
            raise UnsupportedFeature("Onboard profile commands are unavailable for this model.")
        if feature_id not in self._features:
            answer = self._exchange(0, 0, feature_id.to_bytes(2, "big"))
            self._features[feature_id] = answer[0]
        if feature_id and not self._features[feature_id]:
            raise UnsupportedFeature(f"Mouse does not provide feature 0x{feature_id:04x}.")
        return self._exchange(self._features[feature_id], function_number, payload)

    def drain_notifications(self):
        if (self._fd is None or self.monitor is None or self.node.product in legacy_reports.PRODUCTS
                or self.node.product in older_mice.MX518):
            return
        for _ in range(512):
            try:
                report = os.read(self._fd, 64)
            except BlockingIOError:
                break
            if not report:
                raise HardwareError("Mouse disconnected.")
            self.monitor.observe(report, self.node.address)

    def check_receiver_model(self):
        """A receiver's USB ID alone cannot establish which mouse is paired."""
        if self.node.receiver:
            length = self.query(0x0005, 0)[0]
            if not 1 <= length <= 120:
                raise HardwareError("Receiver did not identify the paired mouse.")
            name = bytearray()
            while len(name) < length:
                name.extend(self.query(0x0005, 1, bytes([len(name)])))
            if not any(s in name[:length].upper() for s in (b"G305", b"G304")):
                raise HardwareError("This receiver is paired with a different mouse.")

    def dpi_values(self) -> tuple[int, ...]:
        """Read the resolution list for sensor 0. Reject ambiguous or unbounded lists."""
        reply = self.query(DPI, 1, b"\x00")
        if len(reply) < 3 or reply[0] != 0:
            raise UnsupportedDpiRange("Mouse returned an invalid sensor capability report.")
        words = [int.from_bytes(reply[i:i + 2], "big") for i in range(1, len(reply) - 1, 2)]
        values = []
        step = None
        for word in words:
            if not word:
                break
            if word & 0xE000 == 0xE000:
                if step is not None or not values or word == 0xE000:
                    raise UnsupportedDpiRange("Mouse reported an invalid DPI step size.")
                step = word & 0x1FFF
            elif step is not None:
                slots, remainder = divmod(word - values[-1], step)
                if slots <= 0 or remainder or len(values) + slots > 4096:
                    raise UnsupportedDpiRange("Mouse reported an unsupported DPI range.")
                values.extend(range(values[-1] + step, word + 1, step))
                step = None
            else:
                if values and values[-1] >= word:
                    raise UnsupportedDpiRange("Mouse returned an unordered resolution list.")
                values.append(word)
        if not values or step is not None:
            raise UnsupportedDpiRange("Mouse did not report a complete DPI range.")
        return tuple(values)

    def has_onboard_layout(self):
        if self._verified_layout is None:
            try:
                desc = self.query(PROFILES, 0)
            except UnsupportedFeature:
                self._verified_layout = False
            else:
                self._verified_layout = bool(
                    len(desc) >= 10 and desc[0] == 1 and desc[2] == 1
                    and layout_labels(self.model, desc[1], desc[5])
                    and int.from_bytes(desc[7:9], "big") in (255, 256)
                )
                if self._verified_layout:
                    self.profile_format = desc[1]
        return self._verified_layout

    def sensor_choices(self, refresh=False):
        if self._sensor_choices is None or refresh:
            try:
                self._sensor_choices = sensors.dpi_values(self)
            except ProtocolError as exc:
                # Busy and transport errors are transient. Other negative
                # acknowledgements can leave a readable sensor without choices.
                if exc.code == 8:
                    raise
                self._sensor_choices = ()
            except (UnsupportedFeature, UnsupportedDpiRange):
                self._sensor_choices = ()
        return self._sensor_choices


def _onboard_stages(device, info):
    from .profiles import current_stages
    # Read the current sector each time, including edits from other clients.
    sector = device.query(PROFILES, 4)[:2]
    stage = device.query(PROFILES, 11)[0]
    if len(sector) != 2:
        raise HardwareError("Mouse did not identify its active profile.")
    try:
        stages = current_stages(device, int.from_bytes(sector, "big"), device.profile_format)
    except ProtocolError as exc:
        # Some G305 firmware replies INVALID_ARGUMENT for unwritten flash.
        # Its factory page supplies the stages until a user profile exists.
        if info.model != "G305" or sector[0] != 0 or exc.code not in (2, 0x0B):
            raise
        stages = current_stages(device, 0x100 | sector[1], device.profile_format)
    if stage >= len(stages) or stages[stage] in (0, 0xFFFF):
        stage = None
    return sector[1], stages, stage


def _state(device, info):
    if device.node.product in legacy_reports.PRODUCTS:
        return legacy_reports.read_state(device, info)
    if device.node.product in older_mice.DIRECT or device.node.product == older_mice.G602:
        return older_mice.read_state(device, info)
    mode = None
    if info.can_edit_profile and device.has_onboard_layout():
        try:
            mode = device.query(PROFILES, 2)[0]
        except UnsupportedFeature:
            pass
        if mode is not None and mode not in (1, 2):
            raise HardwareError("Mouse reported an unknown profile mode.")
    if mode == 1:
        profile, stages, stage = _onboard_stages(device, info)
        choices = tuple(dict.fromkeys(value for value in stages if value not in (0, 0xFFFF)))
    else:
        profile, stages, stage = None, (), None
        choices = device.sensor_choices()
    # A temporary DPI override or held DPI-shift button can differ from the
    # selected onboard stage. Always query the sensor before using that stage.
    try:
        current = sensors.read_dpi(device, default_if_zero=stage is None)
    except (ProtocolError, UnsupportedFeature):
        if stage is None:
            raise
        current = 0
    if current == 0 and stage is not None:
        current = stages[stage]
    if current == 0:
        raise UnsupportedFeature("Mouse did not report its current DPI.")
    return MouseState(info, current, choices, stages, stage, profile, None if mode is None else mode == 1)


class MouseBackend:
    def __init__(self):
        self.lock = threading.RLock()
        self._targets = {}
        self._connections = {}
        self._watching = set()

    def discover(self) -> list[MouseInfo]:
        with self.lock:
            self._targets = _enumerate_nodes()
            for mouse_id, device in list(self._connections.items()):
                if device.node not in self._targets.get(mouse_id, ()):
                    self._discard(mouse_id)
                elif getattr(device, "retry_at", float("inf")) < time.monotonic():
                    replacement = self._open(mouse_id)
                    if replacement.node.info.has_protocol:
                        self._discard(mouse_id)
                        self._connections[mouse_id] = replacement
                    else:
                        device.retry_at = time.monotonic() + 8
                        replacement.close()
            return [nodes[0].info for nodes in self._targets.values()]

    def _discard(self, mouse_id):
        device = self._connections.pop(mouse_id, None)
        if device is not None:
            device.close()

    def _open(self, mouse_id):
        nodes = self._targets.get(mouse_id)
        if not nodes:
            raise HardwareError("Mouse is disconnected.")
        failures = []
        for node in nodes:
            device = None
            try:
                device = HidDevice(node)
                if not node.info.has_protocol:
                    if failures:
                        device.retry_at = time.monotonic() + 8
                        if any(isinstance(e, PermissionError) for e in failures):
                            device.read_limit = "HID access denied. Reconnect after installing the device access rule."
                        else:
                            device.read_limit = "DPI interface unavailable. Wake the mouse or check its connection."
                    else:
                        device.read_limit = "This pointer has no accessible DPI reporting interface."
                    return device
                device.check_receiver_model()
                # Reading mode confirms reachability, including paired nodes
                # that persist in sysfs while a wireless mouse is asleep.
                try:
                    if node.product in legacy_reports.PRODUCTS:
                        legacy_reports.probe(device)
                    elif node.product in older_mice.DIRECT:
                        older_mice.probe(device)
                    elif node.product in _LEGACY_REGISTER_MICE:
                        short = node.product in (0xC041, 0xC049, 0xC048)
                        device.exchange_raw(0x81 if short else 0x83, 0x63)
                    elif node.info.can_edit_profile:
                        device.query(PROFILES, 2)
                    else:
                        # A root feature lookup is an address probe. A zero
                        # feature index still confirms that endpoint is alive.
                        device.query(0, 0, b"\x22\x01")
                except UnsupportedFeature:
                    pass  # A known model may expose different controls on this transport.
                except ProtocolError as exc:
                    if node.info.can_edit_profile or exc.code not in (1, 9):
                        raise
                return device
            except (OSError, HardwareError) as exc:
                failures.append(exc)
                if device is not None:
                    device.close()
        if all(isinstance(error, PermissionError) for error in failures):
            raise HardwareError("USB access denied. Reconnect the mouse or receiver after installing the access rule.")
        raise HardwareError(str(failures[-1]))

    @contextmanager
    def device(self, mouse_id):
        with self.lock:
            if mouse_id not in self._connections:
                self._connections[mouse_id] = self._open(mouse_id)
            try:
                yield self._connections[mouse_id]
            except (OSError, HardwareError) as exc:
                self._discard(mouse_id)
                if isinstance(exc, HardwareError):
                    raise
                raise HardwareError(f"Mouse connection failed: {exc.strerror or exc}") from exc

    def read(self, mouse_id: str) -> MouseState:
        from .profiles import ProfileError
        with self.device(mouse_id) as device:
            info = device.node.info
            if not info.has_protocol:
                return MouseState(info, None, (), (), None, None, None, device.read_limit)
            try:
                return _state(device, info)
            except (UnsupportedFeature, UnsupportedDpiRange, ProfileError) as error:
                return MouseState(info, None, (), (), None, None, None, str(error))
            except ProtocolError as error:
                if error.code in (8, 9):  # Busy or unsupported/absent device.
                    raise
                return MouseState(info, None, (), (), None, None, None, str(error))
            except HardwareError as error:
                # A live endpoint can lack a usable sensor reply while still
                # exposing polling rates, button controls or battery state.
                if (device.node.product in _LEGACY_REGISTER_MICE or device.node.product in older_mice.DIRECT
                        or device.node.product in legacy_reports.PRODUCTS):
                    raise
                device.query(0, 0, b"\x22\x01")
                return MouseState(info, None, (), (), None, None, None, str(error))

    def enable_button_monitor(self, mouse_id):
        with self.device(mouse_id) as device:
            self._watching.add(mouse_id)
            device.start_monitor()

    def buttons(self, mouse_id) -> ButtonState:
        with self.device(mouse_id) as device:
            if mouse_id not in self._watching:
                return ButtonState((), (), (), (), "unavailable", "Button monitoring is off.")
            # A disconnected receiver may have reappeared since the last poll.
            device.start_monitor()
            device.drain_notifications()
            return device.monitor.sample()

    def disable_button_monitor(self, mouse_id):
        with self.lock:
            self._watching.discard(mouse_id)
            if mouse_id in self._connections:
                with self.device(mouse_id) as device:
                    device.stop_monitor()

    def set_dpi(self, mouse_id: str, dpi: int) -> MouseState:
        with self.device(mouse_id) as device:
            if not device.node.info.has_protocol:
                raise ValueError("This pointer has no accessible DPI controls.")
            if device.node.product in legacy_reports.PRODUCTS:
                return legacy_reports.set_dpi(device, dpi)
            if device.node.product in older_mice.DIRECT or device.node.product == older_mice.G602:
                return older_mice.set_dpi(device, dpi)
            before = _state(device, device.node.info)
            choices = before.dpi_values if before.onboard else device.sensor_choices(refresh=True)
            if not choices:
                raise ValueError("The mouse does not advertise available DPI settings. Monitoring is read-only.")
            if type(dpi) is not int or dpi not in choices:
                raise ValueError("Choose a DPI stage from the active profile." if before.onboard
                                 else "Choose a DPI supported by this mouse.")
            if before.onboard:
                if before.active_stage is not None and before.stages[before.active_stage] == dpi:
                    return before
                stage = before.stages.index(dpi)
                device.query(PROFILES, 12, bytes([stage]))
            else:
                if dpi == before.dpi:
                    return before
                sensors.set_dpi(device, dpi)
            after = _state(device, device.node.info)
            if before.onboard:
                if (after.profile != before.profile or after.active_stage != stage
                        or after.stages[stage] != dpi):
                    raise HardwareError("The mouse did not confirm the selected onboard DPI step.")
            elif after.dpi != dpi:
                raise HardwareError(f"The mouse currently reports {after.dpi} DPI after selecting {dpi}.")
            return after

    def close(self):
        with self.lock:
            self._watching.clear()
            for mouse_id in list(self._connections):
                self._discard(mouse_id)
