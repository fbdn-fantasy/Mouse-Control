"""Model-specific controls used by earlier Logitech gaming mice."""

import ctypes
import fcntl
import os
from pathlib import Path
import struct


G400, G400S, G602 = 0xC245, 0xC24C, 0x402C
G400_FAMILY = (G400, G400S)
MX518 = (0xC01E, 0xC051)
DIRECT = frozenset((*G400_FAMILY, *MX518))
_DPI = {
    G400: {3: 400, 4: 800, 5: 1800, 6: 3600},
    # The physical buttons on the G400s skip code 5.
    G400S: {3: 400, 4: 800, 6: 2000, 7: 4000},
    0xC01E: {3: 400, 4: 800, 5: 1200, 6: 1600},
    # The later revision changed the sensor. Only the shared lower settings
    # are exposed until its higher codes can be independently confirmed.
    0xC051: {3: 400, 4: 800},
}


class _UsbControl(ctypes.Structure):
    _fields_ = [("direction", ctypes.c_uint8), ("request", ctypes.c_uint8),
                ("value", ctypes.c_uint16), ("index", ctypes.c_uint16),
                ("length", ctypes.c_uint16), ("timeout", ctypes.c_uint32),
                ("data", ctypes.c_void_p)]


def usb_path(instance, product, devroot):
    if product not in (*MX518, G400S):
        return None
    for parent in Path(instance).parents:
        if (parent / "idProduct").is_file():
            try:
                if int((parent / "idVendor").read_text(), 16) != 0x046D:
                    return None
                if int((parent / "idProduct").read_text(), 16) != product:
                    return None
                bus = int((parent / "busnum").read_text())
                number = int((parent / "devnum").read_text())
            except (OSError, ValueError):
                return None
            if 1 <= bus <= 999 and 1 <= number <= 127:
                return devroot / "bus" / "usb" / f"{bus:03d}" / f"{number:03d}"
            return None
    return None


def _usb_resolution(fd, product, new_code=None):
    from .hardware import HardwareError
    descriptor = os.pread(fd, 18, 0)
    if (len(descriptor) != 18 or descriptor[:2] != b"\x12\x01"
            or struct.unpack_from("<HH", descriptor, 8) != (0x046D, product)):
        raise HardwareError("The USB descriptor does not match the selected mouse.")
    if product not in (*MX518, G400S) or (product == G400S and new_code is None):
        raise HardwareError("This mouse does not provide the requested USB resolution control.")
    register = 0x8E if product == G400S else 0x0E
    value = ctypes.c_ubyte()
    if new_code is None:
        transfer = _UsbControl(0xC0, 1, register, 0, 1, 600, ctypes.addressof(value))
    else:
        table_code = new_code & 0x7F if product == G400S else new_code
        if table_code not in _DPI[product]:
            raise ValueError("Unknown DPI stage for this mouse.")
        transfer = _UsbControl(0x40, 2, register, new_code, 0, 600, None)
    # Linux USBDEVFS_CONTROL. Device-recipient requests leave the kernel HID
    # driver bound and require no interface claim or input grab.
    result = fcntl.ioctl(fd, 0xC0005500 | ctypes.sizeof(transfer) << 16, bytearray(transfer), True)
    if result != transfer.length:
        raise HardwareError("Incomplete USB resolution response.")
    return value.value


def _feature(device, report_id, new_code=None):
    from .hardware import HardwareError
    if device.node.product not in G400_FAMILY or report_id not in (0x8E, 0x20):
        raise HardwareError("Unsupported mouse feature report.")
    data = bytearray((report_id, new_code or 0))
    number = 0xC0024807 if new_code is None else 0xC0024806
    if fcntl.ioctl(device._fd, number, data, True) != len(data) or data[0] != report_id:
        raise HardwareError("Incomplete mouse feature response.")
    return data[1]


def _code(device):
    if device.node.product in MX518:
        return _usb_resolution(device._fd, device.node.product)
    return _feature(device, 0x8E)


def read_state(device, info):
    from .hardware import HardwareError, MouseState
    product = device.node.product
    if product == G602:
        # Its early 0x2201 implementation numbers the first sensor one.
        current = device.query(0x2201, 2, bytes([1]))
        if len(current) < 3 or current[0] != 1 or not any(current[1:3]):
            raise HardwareError("The G602 did not report a current sensor DPI.")
        return MouseState(info, int.from_bytes(current[1:3], "big"), (), (), None, None, None,
                          "Live sensor reading. Change the active DPI with the mouse buttons.")
    raw = _code(device)
    stage_id = raw & 0x7F if product == G400S else raw
    table = _DPI[product]
    dpi = table.get(stage_id)
    if product == G400 and stage_id == 7:
        return MouseState(info, 3600, (), (), None, None, None, "The current mouse mode locks DPI at 3600.")
    options = tuple(table.values())
    detail = ""
    if product == G400S:
        if raw & 0x80:
            # Only the two lower settings have corroborated values when the
            # Windows driver's programmable mode is active.
            options = (400, 800)
            if stage_id not in (3, 4):
                dpi = None
        elif dpi and stage_id in (6, 7):
            detail = "Active physical stage on the G400s factory DPI ladder."
        if not device.node.control_path or not os.access(device.node.control_path, os.R_OK | os.W_OK):
            options = ()
    if dpi is None:
        detail = f"The mouse reports resolution code 0x{raw:02x}, whose DPI is unverified on this revision."
    return MouseState(info, dpi, options, (), None, None, None, detail)


def probe(device):
    # A read also confirms that the endpoint responds with the expected protocol.
    return read_state(device, device.node.info)


def set_dpi(device, dpi):
    from .hardware import HardwareError
    before = read_state(device, device.node.info)
    if type(dpi) is not int or dpi not in before.dpi_values:
        raise ValueError("This DPI is unavailable on the selected mouse.")
    if before.dpi == dpi:
        return before
    product = device.node.product
    code = next(key for key, value in _DPI[product].items() if value == dpi)
    if product in MX518:
        _usb_resolution(device._fd, product, code)
    elif product == G400S:
        mode = _code(device) & 0x80
        fd = os.open(device.node.control_path, os.O_RDWR | os.O_CLOEXEC)
        try:
            _usb_resolution(fd, product, mode | code)
        finally:
            os.close(fd)
    else:
        _feature(device, 0x8E, code)
    actual = read_state(device, device.node.info)
    if actual.dpi != dpi:
        raise HardwareError(f"The mouse reported {actual.dpi} DPI after selecting {dpi}.")
    return actual


def report_rates(device):
    from .hardware import HardwareError
    power = _feature(device, 0x20)
    if power > 3:
        raise HardwareError("The mouse reported an unrecognized polling interval.")
    return 1000 >> power, (125, 250, 500, 1000)


def set_report_rate(device, rate):
    from .hardware import HardwareError
    current, supported = report_rates(device)
    if type(rate) is not int or rate not in supported:
        raise ValueError("Choose a polling rate supported by this mouse.")
    if current == rate:
        return
    _feature(device, 0x20, (1000 // rate).bit_length() - 1)
    if report_rates(device)[0] != rate:
        raise HardwareError("The mouse did not confirm the selected polling rate.")
