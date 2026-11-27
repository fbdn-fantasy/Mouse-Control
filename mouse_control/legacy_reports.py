"""Direct, fixed-layout USB feature reports for the wired G300/G300s and G600.

These devices expose the active profile and stage separately from the profile's
settings. Profile edits preserve all unrelated bytes, including G600 G-Shift.
"""

from dataclasses import dataclass, field
import fcntl
import hashlib


G300, G600 = 0xC246, 0xC24A
PRODUCTS = frozenset((G300, G600))
_LENGTH = {G300: 35, G600: 154}
_RATE = {G300: (1000, 125, 250, 500), G600: tuple(1000 // divisor for divisor in range(1, 9))}
_CLICK = {"primary": 1, "secondary": 2, "middle": 3, "back": 4, "forward": 5}
_SPECIAL = ("dpi-up", "dpi-down", "dpi-cycle", "profile-cycle", "dpi-shift")
_KEYS = {"copy": (1, 6), "cut": (1, 27), "paste": (1, 25), "undo": (1, 29),
         "redo": (3, 29), "select-all": (1, 4), "save": (1, 22)}


def supports_descriptor(product, descriptor):
    """Identify the actual Feature endpoint, even on composite keyboard/mouse USB devices."""
    if product not in PRODUCTS:
        return False
    totals = {}
    report_id = size = count = 0
    stack = []
    pos = 0
    while pos < len(descriptor):
        prefix = descriptor[pos]
        if prefix == 0xFE:
            if pos + 2 >= len(descriptor):
                return False
            pos += 3 + descriptor[pos + 1]
            if pos > len(descriptor):
                return False
            continue
        length = (0, 1, 2, 4)[prefix & 3]
        if pos + 1 + length > len(descriptor):
            return False
        value = int.from_bytes(descriptor[pos + 1:pos + 1 + length], "little")
        pos += length + 1
        tag = prefix & 0xFC
        if tag == 0x74:
            size = value
        elif tag == 0x94:
            count = value
        elif tag == 0x84:
            report_id = value
        elif tag == 0xA4:
            stack.append((report_id, size, count))
        elif tag == 0xB4:
            if not stack:
                return False
            report_id, size, count = stack.pop()
        elif tag == 0xB0:
            totals[report_id] = totals.get(report_id, 0) + size * count
    return totals.get(0xF0) == 24 and all(
        totals.get(i) == (_LENGTH[product] - 1) * 8 for i in (0xF3, 0xF4, 0xF5)
    )


def _product(device):
    from .hardware import HardwareError
    product = device.node.product
    if product not in PRODUCTS or not device.node.info.has_protocol:
        raise HardwareError("This is not a supported legacy mouse control interface.")
    return product


def _get(device, report_id, length):
    from .hardware import HardwareError
    _product(device)
    payload = bytearray(length)
    payload[0] = report_id
    returned = fcntl.ioctl(device._fd, 0xC0004807 | length << 16, payload, True)
    if returned != length or payload[0] != report_id:
        raise HardwareError("The mouse returned an incomplete or mismatched feature report.")
    return bytes(payload)


def _put(device, payload):
    from .hardware import HardwareError
    _product(device)
    returned = fcntl.ioctl(device._fd, 0xC0004806 | len(payload) << 16, bytearray(payload), True)
    if returned != len(payload):
        raise HardwareError("The mouse did not acknowledge the complete feature report.")


def dpi_values(product):
    if product == G300:
        return tuple(range(250, 2501, 250))
    if product == G600:
        return tuple(range(200, 8201, 50))
    return ()


def probe(device):
    from .hardware import HardwareError
    product = _product(device)
    active = _get(device, 0xF0, 4)[1]
    profile = active >> 4
    stage = active >> 1 & (7 if product == G300 else 3)
    if profile >= 3 or stage >= 4:
        raise HardwareError("The mouse reported an unknown active profile or DPI step.")
    return profile + 1, stage


def _layout(product):
    # Offsets include the one-byte USB report identifier.
    return (3, 8, 9, 2) if product == G300 else (14, 31, 20, 11)


def _decode(product, raw):
    from .hardware import HardwareError
    dpi_offset, _, _, rate_offset = _layout(product)
    levels = raw[dpi_offset:dpi_offset + 4]
    if product == G300:
        steps = tuple((v & 127) * 250 for v in levels)
        defaults = [n for n, v in enumerate(levels) if v & 128]
        default = defaults[0] if len(defaults) == 1 else None
    else:
        steps = tuple(v * 50 for v in levels)
        default = raw[13] - 1
    choices = dpi_values(product)
    valid_steps = choices if product == G300 else (0,) + choices
    if any(value not in valid_steps for value in steps) or not any(steps):
        raise HardwareError("The mouse returned an invalid legacy DPI table.")
    if default not in range(4) or not steps[default]:
        raise HardwareError("The profile does not contain a valid default DPI step.")
    rate_index = raw[rate_offset]
    if rate_index >= len(_RATE[product]):
        raise HardwareError("The mouse returned an unknown polling-rate code.")
    return steps, default, _RATE[product][rate_index]


def _active_image(device):
    from .hardware import HardwareError
    product = _product(device)
    for attempt in range(2):
        slot, stage = probe(device)
        raw = _get(device, 0xF2 + slot, _LENGTH[product])
        if (slot, stage) == probe(device):
            return slot, stage, raw
    raise HardwareError("The active profile changed during the read. Try again.")


def read_state(device, info):
    from .hardware import MouseState
    product = _product(device)
    slot, stage, raw = _active_image(device)
    steps, _, _ = _decode(product, raw)
    selected = steps[stage]
    detail = ("Mouse-reported active DPI step." if selected else "The active step has no configured DPI.")
    return MouseState(info, selected or None, tuple(dict.fromkeys(v for v in steps if v)),
                      steps, stage, slot, True, detail)


def set_dpi(device, value):
    from .hardware import HardwareError
    before = read_state(device, device.node.info)
    if type(value) is not int or value not in before.dpi_values:
        raise ValueError("Choose a nonzero DPI step stored in the current onboard profile.")
    if before.dpi != value:
        if probe(device)[0] != before.profile:
            raise HardwareError("The mouse changed profiles. Choose the DPI step again.")
        _put(device, bytes((0xF0, 0x40 | before.stages.index(value) << 1, 0, 0)))
    after = read_state(device, device.node.info)
    if after.profile != before.profile or after.dpi != value:
        raise HardwareError("The mouse did not confirm the selected DPI step.")
    return after


def _encodings(product):
    codes = dict(_CLICK)
    base = 10 if product == G300 else 17
    codes.update({key: base + i for i, key in enumerate(_SPECIAL)})
    actions = {key: bytes((code, 0, 0)) for key, code in codes.items()}
    actions.update({key: bytes((0, modifier, usage)) for key, (modifier, usage) in _KEYS.items()})
    return actions


@dataclass(frozen=True)
class LegacyProfile:
    slot: int
    dpi_steps: tuple
    default_index: int
    buttons: tuple
    button_labels: tuple
    actions: tuple
    report_rate: int
    report_rates: tuple
    _device: object = field(repr=False)
    _unit: bytes = field(repr=False)
    _original: bytes = field(repr=False)
    name = ""
    reason = ""
    first_save = False
    editable = True
    can_rename = False


def read_profile(device):
    product = _product(device)
    slot, _, raw = _active_image(device)
    steps, default, hz = _decode(product, raw)
    _, offset, count, _ = _layout(product)
    actions = _encodings(product)
    reverse = {data: key for key, data in actions.items()}
    bindings = tuple(reverse.get(raw[i:i + 3], "existing") for i in range(offset, offset + count * 3, 3))
    labels = tuple(f"Button {i + 1}" for i in range(count))
    unit = hashlib.sha256(device.node.identity.encode()).digest()[:6]
    return LegacyProfile(slot, steps, default, bindings, labels, tuple(actions), hz, tuple(sorted(_RATE[product])),
                         device, unit, raw)


def read_roster(device, snapshot=None):
    from .profiles import ProfileError, ProfileSlot
    if snapshot is not None and snapshot._device is not device:
        raise ProfileError("The selected mouse changed. Reload the profile list.")
    slot, _ = probe(device)
    return tuple(ProfileSlot(i, "", True, i == slot, False) for i in range(1, 4))


def activate_profile(device, slot):
    from .profiles import ProfileError
    if type(slot) is not int or slot not in range(1, 4):
        raise ProfileError("Choose one of the mouse's three onboard profiles.")
    product = _product(device)
    raw = _get(device, 0xF2 + slot, _LENGTH[product])
    _, default, _ = _decode(product, raw)
    if probe(device)[0] != slot:
        _put(device, bytes((0xF0, 0x80 | (slot - 1) << 4, 0, 0)))
        if probe(device)[0] != slot:
            raise ProfileError("The mouse did not confirm the selected profile.")
        if product == G600:
            _put(device, bytes((0xF0, 0x40 | default << 1, 0, 0)))
            if probe(device) != (slot, default):
                raise ProfileError("The mouse did not confirm the new profile's default DPI step.")
    return read_profile(device)


def save_profile(device, snapshot, *, dpi_steps=None, buttons=None, report_rate=None, name=None, backup_dir=None):
    from .hardware import HardwareError
    from .profiles import ProfileError, _backup, _write_lock
    if device is not snapshot._device or name not in (None, ""):
        raise ProfileError("Reload the selected mouse. The legacy profiles have fixed slot numbers.")
    product = _product(device)
    dpi_offset, button_offset, count, rate_offset = _layout(product)
    image = bytearray(snapshot._original)
    if dpi_steps is not None:
        values = tuple(dpi_steps)
        choices = dpi_values(product)
        if product == G600:
            choices = (0,) + choices
        if len(values) != 4 or any(type(v) is not int or v not in choices for v in values):
            raise ProfileError("Choose four supported DPI values. G300 steps use 250 DPI increments and G600 steps use 50.")
        if not values[snapshot.default_index]:
            raise ProfileError("The default DPI step must remain enabled.")
        if not values[probe(device)[1]]:
            raise ProfileError("Select a different active step before disabling this one.")
        for i, value in enumerate(values):
            previous = image[dpi_offset+i]
            image[dpi_offset+i] = ((previous & 128) | (value // 250)) if product == G300 else value // 50
    if report_rate is not None:
        if type(report_rate) is not int or report_rate not in snapshot.report_rates:
            raise ProfileError("Select a supported polling rate.")
        image[rate_offset] = _RATE[product].index(report_rate)
    actions = _encodings(product)
    for i, key in (buttons or {}).items():
        if type(i) is not int or not 0 <= i < count or key not in actions:
            raise ProfileError("Select a supported mouse button and assignment.")
        offset = button_offset + i * 3
        image[offset:offset+3] = actions[key]
    if "primary" in snapshot.buttons and all(image[i:i+3] != actions["primary"]
            for i in range(button_offset, button_offset + count*3, 3)):
        raise ProfileError("Keep at least one button assigned to primary click.")
    with _write_lock(backup_dir) as directory:
        if read_profile(device) != snapshot:
            raise ProfileError("The active mouse profile changed. Reload before saving.")
        if image == snapshot._original:
            return snapshot
        backup = _backup(directory, snapshot, snapshot._original)
        if read_profile(device) != snapshot:
            raise ProfileError("The mouse changed while the backup was being prepared. Reload before saving.")
        slot, stage = probe(device)
        if slot != snapshot.slot or not _decode(product, image)[0][stage]:
            raise ProfileError("The mouse switched to a different profile or a step scheduled for removal.")
        try:
            _put(device, bytes(image))
            if _get(device, image[0], len(image)) != image:
                raise HardwareError("The mouse returned different profile bytes after writing.")
        except (OSError, HardwareError) as error:
            raise ProfileError(f"Profile write could not be verified. Backup: {backup}. {error}") from error
        return read_profile(device)
