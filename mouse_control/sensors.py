"""Native sensor and USB report-rate controls. All setters target live settings."""

from struct import pack, unpack_from

from . import hardware as hw

_SHORT_RESOLUTION = (0xC041, 0xC049, 0xC048)

# Old gaming mice store a sensor code in HID++ 1 register 0x63.
# Similar retail names sometimes have different code tables.
def _legacy_table(product):
    if product in (0xC041, 0xC049):
        return dict(enumerate((400, 800, 1600, 2000), start=0x80))
    if product == 0xC048:
        return dict(enumerate(range(200, 3201, 200), start=0x81))
    if product in (0xC068, 0xC249, 0xC06B):
        return {i: min(5700, round(i * 16 / 17) * 25) for i in range(1, 244)}
    if product in (0xC24E, 0xC07C):
        return {i: i * 50 for i in range(1, 165)}
    if product == 0xC066:
        return {i: 200 + i * 50 for i in range(1, 111)}
    return None


def _u16(report, offset=0):
    if len(report) < offset + 2:
        raise hw.HardwareError("The mouse returned an incomplete sensor report.")
    return unpack_from(">H", report, offset)[0]


def _legacy_info(device):
    product = getattr(device.node, "product", None)
    return product, _legacy_table(product)


def _extended_caps(device):
    response = device.query(0x2202, 1, bytes([0]))
    if len(response) < 3 or response[0] != 0:
        raise hw.UnsupportedDpiRange("Invalid extended sensor capabilities.")
    return response[2]


def read_dpi(device, *, default_if_zero=True):
    product, table = _legacy_info(device)
    if table:
        short = product in _SHORT_RESOLUTION
        reply = device.exchange_raw(0x81 if short else 0x83, 0x63)
        code = int.from_bytes(reply[:1 if short else 2], "little")
        if code not in table:
            raise hw.HardwareError("The mouse returned an unknown legacy DPI code.")
        return table[code]
    try:
        report = device.query(0x2202, 5, bytes([0]))
    except hw.UnsupportedFeature:
        pass
    else:
        if not report or report[0] != 0:
            raise hw.HardwareError("Unexpected extended sensor ID.")
        return _u16(report, 1) or (_u16(report, 3) if default_if_zero else 0)
    try:
        report = device.query(0x2201, 2, bytes([0]))
    except hw.UnsupportedFeature:
        return _u16(device.query(0x2200, 0))
    if not report or report[0] != 0:
        raise hw.HardwareError("Unexpected sensor ID.")
    return _u16(report, 1) or (_u16(report, 3) if default_if_zero and len(report) >= 5 else 0)


def _ranges(device, axis):
    values = []
    stride = None
    carry = b""
    for index in range(32):
        report = device.query(0x2202, 2, bytes([0, axis, index]))
        if len(report) < 5 or report[:2] != bytes([0, axis]) or report[2] != index:
            raise hw.UnsupportedDpiRange("The mouse returned an unexpected DPI range page.")
        # Each page can contain 13 data bytes. A 16-bit value may span pages.
        data = carry + report[3:]
        aligned = len(data) - len(data) % 2
        carry = data[aligned:]
        for offset in range(0, aligned, 2):
            word = _u16(data, offset)
            if word == 0:
                if stride or not values:
                    raise hw.UnsupportedDpiRange("The sensor advertised an incomplete DPI range.")
                return tuple(values)
            if word & 0xE000 == 0xE000:
                stride_value = word & 0x1FFF
                if not values or stride or not stride_value:
                    raise hw.UnsupportedDpiRange("Invalid DPI interval.")
                stride = stride_value
            elif stride:
                if word <= values[-1] or (word - values[-1]) % stride:
                    raise hw.UnsupportedDpiRange("Invalid DPI range endpoint.")
                if len(values) + (word - values[-1]) // stride > 4096:
                    raise hw.UnsupportedDpiRange("Unexpectedly large DPI range.")
                values.extend(range(values[-1] + stride, word + 1, stride))
                stride = None
            else:
                values.append(word)
    raise hw.UnsupportedDpiRange("The sensor did not terminate its DPI list.")


def dpi_values(device):
    product, table = _legacy_info(device)
    if table:
        return tuple(sorted(set(table.values())))
    try:
        caps = _extended_caps(device)
    except hw.UnsupportedFeature:
        return device.dpi_values()
    x_values = _ranges(device, 0)
    if caps & 1:
        y_values = set(_ranges(device, 1))
        return tuple(v for v in x_values if v in y_values)
    return x_values


def set_dpi(device, dpi):
    if type(dpi) is not int or dpi not in dpi_values(device):
        raise ValueError("Choose a DPI value reported by this mouse.")
    product, table = _legacy_info(device)
    if table:
        code = next(key for key, value in table.items() if value == dpi)
        if product in _SHORT_RESOLUTION:
            device.exchange_raw(0x80, 0x63, bytes([code]))
        else:
            device.exchange_raw(0x82, 0x63, pack("<HH", code, code), report_id=0x11)
        return
    try:
        caps = _extended_caps(device)
    except hw.UnsupportedFeature:
        device.query(0x2201, 3, pack(">BH", 0, dpi))
    else:
        current = device.query(0x2202, 5, bytes([0]))
        if len(current) < 10:
            raise hw.HardwareError("Unable to preserve the sensor’s lift-off setting.")
        device.query(0x2202, 6, pack(">BHHB", 0, dpi, dpi if caps & 1 else 0,
                                     current[9] if caps & 2 else 0))


def _rate_settings(device):
    product, table = _legacy_info(device)
    if product in (0xC245, 0xC24C):
        from . import older_mice
        current, options = older_mice.report_rates(device)
        return current, options, None
    if table:
        response = device.exchange_raw(0x81, 0x64)
        intervals = (8, 5, 4, 3, 2, 1)
        if not response or response[0] not in intervals:
            raise hw.UnsupportedFeature("This mouse has no recognized polling interval.")
        options = ()
        # Register 0x64 has a documented short write on these exact G5,
        # G500/G500s and wired G700s products. It has no capability bitmap.
        if product in (0xC041, 0xC049, 0xC068, 0xC24E, 0xC07C):
            if product == 0xC041 and response[0] != 1:
                intervals = intervals[:-1]
            options = tuple(round(1000 / n) for n in intervals)
        return round(1000 / response[0]), options, 0x64
    try:
        response = device.query(0x8061, 1)
    except hw.UnsupportedFeature:
        mask = device.query(0x8060, 0)[0]
        interval = device.query(0x8060, 1)[0]
        choices = tuple(round(1000 / n) for n in range(8, 0, -1) if mask & (1 << (n - 1)))
        return (round(1000 / interval) if 1 <= interval <= 8 else None), choices, 0x8060
    if 0xB000 <= product < 0xC000 or "/0005:" in getattr(device.node, "instance", ""):
        # The active-rate query has selectors for cable and LIGHTSPEED only.
        return None, (), 0x8061
    transport = bytes([int(1 <= device.node.address <= 6)])
    if len(response) < 2:
        raise hw.HardwareError("The mouse did not identify its polling rates.")
    # Function 1 lists rates for the current connection. Only byte 1 bits
    # 0–6 encode rates. Byte 0 is reserved on currently documented firmware.
    mask = response[1]
    answer = device.query(0x8061, 2, transport)
    if not answer:
        raise hw.HardwareError("The mouse returned an incomplete polling rate.")
    idx = answer[0]
    choices = tuple(125 << i for i in range(7) if mask & (1 << i))
    return (125 << idx if idx < 7 else None), choices, 0x8061


def report_rates(device):
    """Return the current report rate and supported choices in Hz."""
    current, options, _ = _rate_settings(device)
    return current, options


def set_report_rate(device, rate):
    if device.node.product in (0xC245, 0xC24C):
        from . import older_mice
        return older_mice.set_report_rate(device, rate)
    current, options, feature = _rate_settings(device)
    if type(rate) is not int or rate not in options:
        raise ValueError("This polling rate is unavailable on the active connection.")
    if current == rate:
        return
    if feature == 0x64:
        device.exchange_raw(0x80, 0x64, bytes([round(1000 / rate)]))
    elif feature == 0x8060:
        device.query(0x8060, 2, bytes([round(1000 / rate)]))
    else:
        device.query(0x8061, 3, bytes([(rate // 125).bit_length() - 1]))
    actual, _ = report_rates(device)
    if actual != rate:
        raise hw.HardwareError(f"The mouse reports {actual} Hz after applying {rate} Hz.")
