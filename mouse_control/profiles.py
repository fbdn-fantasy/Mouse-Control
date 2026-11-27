"""Read and patch the active 0x8100 user profile on a supported Logitech mouse."""

import binascii
import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
import struct
import tempfile

from .hardware import PROFILES, HardwareError, ProtocolError, UnsupportedFeature
from .models import MODEL_PROFILES, layout_labels
from . import sensors


class ProfileError(RuntimeError):
    """The active onboard profile cannot be inspected or safely changed."""


class _ProfileIOError(ProfileError, HardwareError):
    """An interrupted operation also invalidates the backend's connection."""


_BUTTONS = {
    "primary": ("Primary click", 0x80010001),
    "secondary": ("Secondary click", 0x80010002),
    "middle": ("Middle click", 0x80010004),
    "back": ("Back", 0x80010008),
    "forward": ("Forward", 0x80010010),
    "dpi-up": ("DPI up", 0x90030000),
    "dpi-down": ("DPI down", 0x90040000),
    "dpi-cycle": ("Cycle DPI", 0x90050000),
    "dpi-shift": ("Hold for shift DPI", 0x90070000),
    "profile-cycle": ("Cycle profile", 0x900A0000),
    "tilt-left": ("Wheel tilt left", 0x90010000),
    "tilt-right": ("Wheel tilt right", 0x90020000),
    "copy": ("Copy (Ctrl+C)", 0x80020106),
    "cut": ("Cut (Ctrl+X)", 0x8002011B),
    "paste": ("Paste (Ctrl+V)", 0x80020119),
    "undo": ("Undo (Ctrl+Z)", 0x8002011D),
    "redo": ("Redo (Ctrl+Shift+Z)", 0x8002031D),
    "select-all": ("Select all (Ctrl+A)", 0x80020104),
    "save": ("Save (Ctrl+S)", 0x80020116),
}
BUTTON_ACTIONS = {key: label for key, (label, _) in _BUTTONS.items()}
_BUTTON_CODES = {code: key for key, (_, code) in _BUTTONS.items()}


@dataclass(frozen=True)
class ProfileSnapshot:
    slot: int
    dpi_steps: tuple
    default_index: int | None
    shift_index: int | None
    buttons: tuple
    button_labels: tuple
    actions: tuple
    reason: str
    name: str
    report_rate: int | None
    report_rates: tuple[int, ...]
    _device: object = field(repr=False)
    _unit: bytes = field(repr=False)
    _descriptor: bytes = field(repr=False)
    _sector: int = field(repr=False)
    _directory: bytes = field(repr=False)
    _original: bytes = field(repr=False)
    _active: int = field(repr=False)
    _empty_directory: bytes | None = field(repr=False)
    dpi_steps_y: tuple = ()

    @property
    def can_select_defaults(self):
        return bool(self.dpi_steps)

    @property
    def editable(self):
        return not self.reason

    @property
    def can_rename(self):
        return True

    @property
    def first_save(self):
        return self.editable and self._empty_directory is not None


@dataclass(frozen=True)
class ProfileSlot:
    slot: int
    name: str
    enabled: bool
    active: bool
    creatable: bool


def _profile_name(raw):
    return raw[160:208].decode("utf-16-le", errors="replace").split("\0", 1)[0].rstrip("\uffff")


def _encoded_name(name):
    if not isinstance(name, str) or (name and not name.isprintable()):
        raise ProfileError("Enter a profile name without control characters.")
    encoded = name.encode("utf-16-le")
    if len(encoded) > 48:
        raise ProfileError("The profile name exceeds the mouse's 24-character storage.")
    return encoded.ljust(48, b"\0") if encoded else b"\xff" * 48


def _supported_rates(device):
    try:
        reply = device.query(0x8060, 0)
    except UnsupportedFeature:
        return ()
    if not reply:
        raise ProfileError("The mouse returned an incomplete polling rate list.")
    return tuple(round(1000 / ms) for ms in range(8, 0, -1) if reply[0] & (1 << (ms - 1)))


def _profile_rate(device, layout, raw):
    if layout <= 5:
        rate = round(1000 / raw[0]) if 1 <= raw[0] <= 8 else None
        return rate, _supported_rates(device), 0
    address = getattr(getattr(device, "node", None), "address", None)
    product = getattr(getattr(device, "node", None), "product", 0)
    if address is None or 0xB000 <= product < 0xC000:
        return None, (), None
    wireless = 1 <= address <= 6
    if device.model == "PRO X SUPERLIGHT 2":
        offset = int(not wireless)
    elif device.model == "PRO X SUPERLIGHT 2 DEX":
        offset = int(wireless)
    elif device.model == "PRO X2 SUPERSTRIKE" and wireless:
        offset = 0
    else:
        return None, (), None
    try:
        reply = device.query(0x8061, 0, bytes((int(wireless),)))
    except UnsupportedFeature:
        return None, (), None
    if len(reply) < 2:
        raise ProfileError("Incomplete polling-rate capability report.")
    mask = reply[1]
    rates = tuple(125 << i for i in range(7) if mask & (1 << i))
    return (125 << raw[offset] if raw[offset] < 7 else None), rates, offset


def _stage_values(raw, layout, axis=0):
    if layout <= 5 and axis == 0:
        values = struct.unpack_from("<5H", raw, 3)
    elif layout in (6, 7):
        values = (struct.unpack_from("<H", raw, 4 + 5 * i + 2 * axis)[0] for i in range(5))
    else:
        return ()
    return tuple(0 if value == 0xFFFF else value for value in values)


def current_stages(device, sector, layout):
    """Read only the header of a known layout; unavailable layouts have no ladder."""
    if not 1 <= layout <= 7:
        return ()
    header = device.query(PROFILES, 5, struct.pack(">HH", sector, 0))
    if layout > 5:
        header += device.query(PROFILES, 5, struct.pack(">HH", sector, 16))
    if len(header) < (29 if layout > 5 else 13):
        raise ProfileError("The mouse returned an incomplete DPI table.")
    return _stage_values(header, layout)


def _checksum(data):
    return binascii.crc_hqx(data, 0xFFFF).to_bytes(2, "big")


def _sector_data(device, sector, size, *, allow_missing_directory=False):
    data = bytearray(size)
    for start in range(0, size, 16):
        # The last read overlaps its predecessor on a 255-byte page.
        offset = min(start, size - 16)
        try:
            chunk = device.query(PROFILES, 5, struct.pack(">HH", sector, offset))
        except ProtocolError as exc:
            if allow_missing_directory and sector == offset == 0 and device.model == "G305" and exc.code == 2:
                return b""
            raise
        if len(chunk) != 16:
            raise ProfileError("The mouse returned an incomplete profile sector.")
        data[offset:offset + 16] = chunk
    return bytes(data)


def _blank(data):
    return data == bytes(len(data)) or data == b"\xff" * len(data)


def _directory_entries(data, limit):
    entries = {}
    for index in range(limit + 1):
        address, enabled = struct.unpack_from(">HB", data, 4 * index)
        if address == 0xFFFF:
            return entries
        if address in entries or enabled not in (0, 1):
            break
        entries[address] = bool(enabled)
    raise ProfileError("The profile directory has an unfamiliar layout.")


def _active_sector(device):
    mode = device.query(PROFILES, 2)
    if not mode or mode[0] != 1:
        raise ProfileError("The mouse must be in onboard mode to edit a stored profile.")
    reply = device.query(PROFILES, 4)
    if len(reply) < 2:
        raise ProfileError("The mouse returned an incomplete active profile number.")
    return int.from_bytes(reply[:2], "big")


def _unit_id(device):
    reply = device.query(0x0003, 0)
    if len(reply) < 5:
        raise ProfileError("The mouse returned incomplete unit identification.")
    return bytes(reply[1:5])


def _binding_key(data):
    value = int.from_bytes(data, "big")
    # The final two bytes in these no-argument firmware functions vary.
    if data[0] == 0x90:
        value &= 0xFFFF0000
    return _BUTTON_CODES.get(value, "")


def _legacy_reports(device):
    if getattr(getattr(device, "node", None), "product", None) in (0xC246, 0xC24A):
        from . import legacy_reports
        return legacy_reports
    return None


def read_profile(device):
    """Read the active profile under the backend's device context."""
    legacy = _legacy_reports(device)
    if legacy:
        return legacy.read_profile(device)
    if device.model not in MODEL_PROFILES:
        raise ProfileError("This mouse has no supported profile format.")
    desc = bytes(device.query(PROFILES, 0))
    if len(desc) < 10 or desc[0] != 1 or desc[2] != 1:
        raise ProfileError("This mouse reports an unsupported onboard profile layout.")
    labels = layout_labels(device.model, desc[1], desc[5])
    if not labels:
        raise ProfileError("The mouse uses an unsupported button or profile layout.")
    count, factory_count, sector_count = desc[3], desc[4], desc[6]
    size = int.from_bytes(desc[7:9], "big")
    if size not in (255, 256) or not 0 < count < sector_count or not 0 < factory_count <= count or count > 5:
        raise ProfileError("This mouse reports an unsupported onboard memory geometry.")
    active = _active_sector(device)
    if active >> 8 > 1 or not 1 <= active & 0xFF <= count:
        raise ProfileError("The mouse reported an unknown active profile.")
    slot = active & 0xFF
    unit = _unit_id(device)
    reason = ""
    try:
        directory = _sector_data(device, 0, size, allow_missing_directory=True)
        empty_directory = directory if _blank(directory) else None
    except ProtocolError as exc:
        if not ((device.model == "G305" and exc.code == 0x0B) or exc.code == 2):
            raise
        # View ROM data, but this nonstandard error alone cannot prove that
        # a user directory is erased and safe to initialize.
        directory, empty_directory = b"", None
    factory = active >= 0x100 or _checksum(directory[:-2]) != directory[-2:]
    if factory:
        directory = _sector_data(device, 0x100, size)
        sector = 0x100 + (slot if slot <= factory_count else 1)
        if empty_directory is None or slot > factory_count:
            reason = "Factory settings are in use. The existing user memory cannot be safely initialized."
    else:
        sector = active
    entries = _directory_entries(directory, factory_count if factory else count)
    if not entries.get(sector):
        raise ProfileError("The active profile is missing or disabled in the mouse directory.")
    if factory and tuple(entries) != tuple(range(0x101, 0x101 + factory_count)):
        reason = "This factory directory has an unfamiliar layout. Its profiles are read-only."
    if not factory and any(not 1 <= address <= count for address in entries):
        reason = "The user directory contains unrecognized sector addresses. Its profiles are read-only."
    raw = _sector_data(device, sector, size)
    if _blank(raw):
        raise ProfileError("The active profile contains no readable settings.")
    if not factory and _checksum(raw[:-2]) != raw[-2:]:
        raise ProfileError("The user profile checksum is invalid. No changes can be saved.")
    product = getattr(getattr(device, "node", None), "product", 0)
    paired = device.model == "G305" or 0x4000 <= product < 0x5000 or 0xB000 <= product < 0xC000
    if paired and unit in (bytes(4), b"\xff" * 4):
        reason = "The receiver could not identify the paired mouse reliably enough to save changes."
    if _active_sector(device) != active:
        raise ProfileError("The mouse switched profiles during the read. Reload the profile.")

    layout = desc[1]
    steps = _stage_values(raw, layout)
    y_steps = ()
    if layout in (6, 7):
        try:
            independent_axes = sensors._extended_caps(device) & 1
        except UnsupportedFeature:
            independent_axes = False
        if independent_axes:
            y_steps = _stage_values(raw, layout, 1)
    button_offset = 48 if layout >= 6 else 32
    keys = tuple(_binding_key(raw[p:p + 4]) for p in range(button_offset, button_offset + 4 * len(labels), 4))
    actions = tuple(key for key in BUTTON_ACTIONS
                    if (key != "profile-cycle" or count > 1)
                    and (key not in ("tilt-left", "tilt-right") or device.model.startswith("G502"))
                    and (key != "dpi-shift" or (desc[9] & 0x0C) == 0x08))
    rate, rates, _ = _profile_rate(device, layout, raw)
    idx = 2 if layout >= 6 else 1
    default, shift = raw[idx:idx + 2] if steps else (None, None)
    if (desc[9] & 0x0C) != 0x08:
        shift = None
    return ProfileSnapshot(slot, steps, default, shift, keys, labels, actions, reason,
                           _profile_name(raw), rate, rates, device, unit, desc,
                           sector, directory, raw, active, empty_directory, y_steps)


def _patch_dpi(device, original, data, dpi_steps, dpi_steps_y, default_index, shift_index):
    layout = original._descriptor[1]
    values = original.dpi_steps if dpi_steps is None else tuple(dpi_steps)
    y_values = original.dpi_steps_y if dpi_steps_y is None else tuple(dpi_steps_y)
    defaults = (default_index, shift_index)
    original_defaults = (original.default_index, original.shift_index)
    changed_indices = [(i, new) for i, (new, old) in enumerate(zip(defaults, original_defaults))
                       if new is not None and new != old]
    if values == original.dpi_steps and y_values == original.dpi_steps_y and not changed_indices:
        return
    if not original.dpi_steps or (y_values and not original.dpi_steps_y):
        raise ProfileError("This mouse does not support editing that DPI table.")
    if len(values) != 5 or any(type(v) is not int or v < 0 for v in values):
        raise ProfileError("Enter five whole-number DPI values. Zero disables a stage.")
    if not any(values):
        raise ProfileError("At least one DPI stage must stay enabled.")
    if original.dpi_steps_y:
        if len(y_values) != 5 or any(type(v) is not int or v < 0 for v in y_values):
            raise ProfileError("Enter five whole-number Y-axis DPI values.")
        if any(x and not y and (not old_x or old_y) for old_x, old_y, x, y in
               zip(original.dpi_steps, original.dpi_steps_y, values, y_values)):
            raise ProfileError("Each enabled stage needs an X and a Y DPI value.")
    # Extended profiles use a zero or erased X value as a list terminator.
    if layout >= 6 and any(not x and any(values[i + 1:]) for i, x in enumerate(values)):
        raise ProfileError("Enable consecutive DPI stages from the first stage.")
    for index, new in changed_indices:
        if type(new) is not int or not 0 <= new < 5 or not values[new]:
            raise ProfileError("Choose an enabled default or shift DPI stage.")
        if index == 1 and (original._descriptor[9] & 0x0C) != 0x08:
            raise ProfileError("This firmware does not support a shift DPI stage.")
        data[(2 if layout >= 6 else 1) + index] = new
    selected = tuple(old if new is None else new for old, new in zip(original_defaults, defaults))
    disabling = any(old and not new for old, new in zip(original.dpi_steps, values))
    if disabling:
        if any(i is not None and 0 <= i < 5 and not values[i] for i in selected):
            raise ProfileError("The default and shift DPI stages must stay enabled.")
        active = device.query(PROFILES, 11)
        if not active or active[0] >= 5 or not values[active[0]]:
            raise ProfileError("Switch to an enabled DPI stage before disabling this one.")
    for axis, before, after in ((0, original.dpi_steps, values), (1, original.dpi_steps_y, y_values)):
        if before == after:
            continue
        if layout >= 6:
            allowed = set(sensors._ranges(device, axis))
        else:
            try:
                allowed = set(device.dpi_values())
            except UnsupportedFeature:
                allowed = set(sensors._ranges(device, axis))
        for i, (old, new) in enumerate(zip(before, after)):
            if old == new:
                continue
            if new and new not in allowed:
                raise ProfileError(f"The mouse does not support {new} DPI.")
            offset = 4 + 5 * i + 2 * axis if layout >= 6 else 3 + 2 * i
            struct.pack_into("<H", data, offset, new)


def _patched_image(device, original, dpi_steps, buttons, report_rate, name, *,
                   dpi_steps_y=None, default_index=None, shift_index=None):
    data = bytearray(original._original)
    layout = original._descriptor[1]
    if report_rate is not None and report_rate != original.report_rate:
        _, rates, offset = _profile_rate(device, layout, data)
        if type(report_rate) is not int or report_rate not in rates or offset is None:
            raise ProfileError("Choose a polling rate advertised by this mouse.")
        data[offset] = ((report_rate // 125).bit_length() - 1) if layout >= 6 else round(1000 / report_rate)
    if name is not None and name != original.name:
        data[160:208] = _encoded_name(name)
    _patch_dpi(device, original, data, dpi_steps, dpi_steps_y, default_index, shift_index)
    base = 48 if layout >= 6 else 32
    for index, key in (buttons or {}).items():
        if type(index) is not int or not 0 <= index < len(original.buttons) or key not in original.actions:
            raise ProfileError("Select a button assignment supported by this mouse.")
        if key != original.buttons[index]:
            struct.pack_into(">I", data, base + 4 * index, _BUTTONS[key][1])
    if "primary" in original.buttons and not any(
        _binding_key(data[base + 4 * index:base + 4 + 4 * index]) == "primary"
        for index in range(len(original.buttons))
    ):
        raise ProfileError("Assign primary click to another button before removing the last primary click.")
    data[-2:] = _checksum(data[:-2])
    return bytes(data)


def _backup(directory, snapshot, contents, suffix=".bin"):
    model = snapshot._device.model.replace(" ", "-")
    prefix = f"{model}-{snapshot._unit.hex()}-slot{snapshot.slot}-"
    with tempfile.NamedTemporaryFile(prefix=prefix, suffix=suffix, dir=directory, delete=False) as stream:
        stream.write(contents)
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return Path(stream.name)


def _write_page(device, page, image):
    device.query(PROFILES, 6, struct.pack(">HHH", page, 0, len(image)))
    for offset in range(0, len(image), 16):
        device.query(PROFILES, 7, image[offset:offset + 16].ljust(16, b"\xff"))
    device.query(PROFILES, 8)
    if _sector_data(device, page, len(image)) != image:
        raise ProfileError(f"The mouse returned different bytes after writing sector {page}.")


def _same_target(device, current):
    if _active_sector(device) != current._active or _unit_id(device) != current._unit:
        raise ProfileError("The selected mouse or active profile changed before saving.")


def _first_save(device, snap, image, directory):
    size = len(image)
    plan, before = {}, {0: snap._empty_directory}
    table = bytearray(b"\xff" * size)
    entries = _directory_entries(snap._directory, snap._descriptor[4])
    for i, (rom, enabled) in enumerate(entries.items()):
        slot = rom & 0xFF
        factory = _sector_data(device, rom, size)
        layout = snap._descriptor[1]
        if _blank(factory) or (layout < 8 and not any(_stage_values(factory, layout))):
            raise ProfileError("An expected factory profile has no usable presets. Initialization stopped.")
        plan[slot] = image if slot == snap.slot else factory[:-2] + _checksum(factory[:-2])
        before[slot] = _sector_data(device, slot, size)
        # An interrupted save can leave fully verified pages before the directory.
        # Never overwrite an orphan sector with unrelated settings.
        if not _blank(before[slot]) and before[slot] != plan[slot]:
            raise ProfileError(f"User sector {slot} already contains other settings. Initialization stopped.")
        struct.pack_into(">HBB", table, 4 * i, slot, enabled, 0)
    struct.pack_into(">HBB", table, len(entries) * 4, 0xFFFF, 0, 0)
    plan[0] = bytes(table[:-2]) + _checksum(table[:-2])
    record = {"model": device.model, "unit": snap._unit.hex(), "active_slot": snap.slot,
              "original_pages_hex": {str(k): v.hex() for k, v in before.items()},
              "planned_pages_hex": {str(k): v.hex() for k, v in plan.items()}}
    backup = _backup(directory, snap, json.dumps(record, indent=2).encode(), "-first-save.json")
    prior_stage = None
    try:
        for slot, data in plan.items():  # Directory insertion is deliberately last.
            _same_target(device, snap)
            if _sector_data(device, 0, size, allow_missing_directory=True) != before[0]:
                raise ProfileError("The user directory changed during initialization.")
            if slot and _sector_data(device, slot, size) != before[slot]:
                raise ProfileError(f"User sector {slot} changed during initialization.")
            if slot == 0:
                for user_slot, expected in plan.items():
                    if user_slot and _sector_data(device, user_slot, size) != expected:
                        raise ProfileError(f"Staged user sector {user_slot} changed before directory commit.")
                prior_stage = device.query(PROFILES, 11)[0]
                _same_target(device, snap)
            if before[slot] != data:
                _write_page(device, slot, data)
    except (OSError, HardwareError, ProfileError) as exc:
        raise _ProfileIOError(f"Initialization could not be verified. Backup and write plan: {backup}. {exc}") from exc
    # Depending on firmware the directory commit may leave the active address
    # in ROM. Select its new user copy only if this is still the same slot.
    try:
        active = _active_sector(device)
        if active == 0x100 + snap.slot and _unit_id(device) == snap._unit:
            device.query(PROFILES, 3, snap.slot.to_bytes(2, "big"))
            # Activating a profile can reset the stage to its default.
            # Re-select the stage that was in use just before the commit.
            steps = _stage_values(image, snap._descriptor[1])
            if prior_stage < len(steps) and steps[prior_stage] not in (0, 0xFFFF):
                if device.query(PROFILES, 11)[0] != prior_stage:
                    device.query(PROFILES, 12, bytes((prior_stage,)))
        updated = read_profile(device)
        if updated.first_save or updated._sector >> 8:
            raise ProfileError("The mouse is still using its factory memory.")
        return updated
    except (OSError, HardwareError, ProfileError) as exc:
        raise ProfileError(f"User profiles were written and verified. Reload the mouse. {exc}") from exc


@contextmanager
def _write_lock(backup_dir):
    if backup_dir is None:
        state_dir = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        backup_dir = state_dir / "mouse-control" / "backups"
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (backup_dir / ".profile-write.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProfileError("Another profile save is in progress. Reload and try again.") from exc
        yield backup_dir


def save_profile(device, snapshot, *, dpi_steps=None, buttons=None, report_rate=None, name=None,
                 backup_dir=None, dpi_steps_y=None, default_index=None, shift_index=None):
    """Apply edits to the same active user sector. Preserve the original bytes on disk."""
    legacy = _legacy_reports(device)
    if legacy:
        if dpi_steps_y or default_index is not None or shift_index is not None:
            raise ProfileError("This mouse does not expose independent axes or default-stage selection.")
        return legacy.save_profile(device, snapshot, dpi_steps=dpi_steps, buttons=buttons, report_rate=report_rate,
                                   name=name, backup_dir=backup_dir)
    if device is not snapshot._device:
        raise ProfileError("The device connection changed. Reload the selected mouse.")
    if not snapshot.editable:
        raise ProfileError(snapshot.reason)
    with _write_lock(backup_dir) as backup_dir:
        image = _patched_image(device, snapshot, dpi_steps, buttons, report_rate, name,
                               dpi_steps_y=dpi_steps_y, default_index=default_index, shift_index=shift_index)
        current = read_profile(device)
        if current != snapshot:
            raise ProfileError("The mouse or its profile changed. Reload before saving.")
        if image[:-2] == current._original[:-2]:
            return current
        if current.first_save:
            return _first_save(device, current, image, backup_dir)
        backup = _backup(backup_dir, current, current._original)
        _same_target(device, current)
        if _sector_data(device, 0, len(image)) != current._directory or (
            _sector_data(device, current._sector, len(image)) != current._original
        ):
            raise ProfileError("The profile changed while preparing the backup. Reload before saving.")
        try:
            _write_page(device, current._sector, image)
        except (OSError, HardwareError, ProfileError) as exc:
            raise _ProfileIOError(f"The save could not be verified. Original profile backup: {backup}. {exc}") from exc
        # The physical profile switch can be used immediately after a save.
        # Preserve the success of the verified write in that case.
        try:
            refreshed = read_profile(device)
        except (OSError, HardwareError, ProfileError) as exc:
            raise ProfileError(f"Profile {current.slot} was saved and verified. Reload the mouse. {exc}") from exc
        return refreshed


def _slot_available(device, snap, slot, data):
    if _blank(data):
        return True
    if _checksum(data[:-2]) != data[-2:]:
        return False
    factory_count = snap._descriptor[4]
    factory = _sector_data(device, 0x100 + (slot if slot <= factory_count else 1), len(data))
    return data[:-2] == factory[:-2]


def _enable_slot(snap, slot):
    """Patch the directory flag or append the next slot at its terminator."""
    table = bytearray(snap._directory)
    entries = _directory_entries(table, snap._descriptor[3])
    for index, (page, enabled) in enumerate(entries.items()):
        if page == slot:
            if enabled:
                return None
            table[4 * index + 2] = 1
            break
    else:
        index = len(entries)
        if tuple(entries) != tuple(range(1, index + 1)) or slot != index + 1:
            return None
        offset = 4 * index
        if not _blank(table[offset + 4:offset + 8]):
            return None
        table[offset + 4:offset + 8] = table[offset:offset + 4]
        struct.pack_into(">HBB", table, offset, slot, 1, 0)
    table[-2:] = _checksum(table[:-2])
    return bytes(table)


def read_roster(device, snapshot=None):
    """Enumerate hardware slots; an already-enabled slot always remains occupied."""
    legacy = _legacy_reports(device)
    if legacy:
        return legacy.read_roster(device, snapshot)
    snap = snapshot if snapshot is not None else read_profile(device)
    if snap._device is not device:
        raise ProfileError("The selected mouse changed. Reload its profile list.")
    total = snap._descriptor[3]
    rom = snap._sector >= 0x100
    entries = _directory_entries(snap._directory, total)
    roster = []
    for slot in range(1, total + 1):
        page = 0x100 + slot if rom else slot
        enabled = entries.get(page, False)
        if rom and page not in entries:
            roster.append(ProfileSlot(slot, "", False, False, False))
            continue
        data = _sector_data(device, page, len(snap._original))
        healthy = not _blank(data) and (rom or _checksum(data[:-2]) == data[-2:])
        available = (not rom and snap.editable and not enabled and
                     _enable_slot(snap, slot) is not None and _slot_available(device, snap, slot, data))
        roster.append(ProfileSlot(slot, _profile_name(data) if healthy else "", enabled and healthy,
                                  snap.slot == slot, available))
    _same_target(device, snap)
    return tuple(roster)


def activate_profile(device, slot):
    """Select an enabled slot on the device and confirm its new active address."""
    legacy = _legacy_reports(device)
    if legacy:
        return legacy.activate_profile(device, slot)
    if type(slot) is not int:
        raise ProfileError("Choose an enabled profile slot.")
    snap = read_profile(device)
    valid = any(item.slot == slot and item.enabled for item in read_roster(device, snap))
    if not valid:
        raise ProfileError("This slot has no enabled, readable profile.")
    if slot != snap.slot:
        bank = snap._active & 0xFF00
        try:
            device.query(PROFILES, 3, (bank | slot).to_bytes(2, "big"))
        except ProtocolError as exc:
            if exc.code != 2 or snap._sector < 0x100:
                raise
            # Some firmware uses a logical slot number even while running ROM.
            # A rejected address never warrants trying a different memory bank
            # if a user directory could select an unrelated saved profile.
            if snap._empty_directory is None:
                raise
            _same_target(device, snap)
            device.query(PROFILES, 3, ((bank ^ 0x100) | slot).to_bytes(2, "big"))
        snap = read_profile(device)
        if snap.slot != slot:
            raise ProfileError("The mouse did not select the requested profile.")
    return snap


def create_profile(device, source, slot, name, *, backup_dir=None):
    """Clone the current profile into unused space, enable it, then activate it."""
    if _legacy_reports(device):
        raise ProfileError("This mouse has three fixed onboard profiles. Edit an existing slot.")
    if device is not source._device or not source.editable:
        raise ProfileError("Reload a writable profile on the selected mouse.")
    if source.first_save:
        raise ProfileError("Save the current profile first to initialize user memory.")
    if type(slot) is not int or not 1 <= slot <= source._descriptor[3] or slot == source.slot:
        raise ProfileError("Choose an unused hardware profile slot.")
    if not isinstance(name, str) or not name.strip():
        raise ProfileError("Enter a name for the new profile.")
    image = _patched_image(device, source, None, None, None, name)
    with _write_lock(backup_dir) as directory:
        if read_profile(device) != source:
            raise ProfileError("The source profile changed. Reload before creating a profile.")
        table = _enable_slot(source, slot)
        if table is None:
            raise ProfileError("This slot is occupied or cannot be added to the current directory.")
        saved = _sector_data(device, slot, len(image))
        if saved != image and not _slot_available(device, source, slot, saved):
            raise ProfileError("This slot already contains custom settings. Choose another slot.")
        record = {"unit": source._unit.hex(), "model": device.model, "source_slot": source.slot,
                  "original_pages_hex": {"0": source._directory.hex(), str(slot): saved.hex()},
                  "planned_pages_hex": {"0": table.hex(), str(slot): image.hex()}}
        backup = _backup(directory, source, json.dumps(record, indent=2).encode(), f"-new-slot{slot}.json")
        try:
            _same_target(device, source)
            if (_sector_data(device, slot, len(image)) != saved or
                    _sector_data(device, 0, len(image)) != source._directory or
                    _sector_data(device, source.slot, len(image)) != source._original):
                raise ProfileError("The profile memory changed while preparing the backup.")
            if saved != image:
                _write_page(device, slot, image)
            _same_target(device, source)
            if _sector_data(device, 0, len(image)) != source._directory:
                raise ProfileError("The directory changed while creating the profile.")
            _write_page(device, 0, table)
        except (OSError, HardwareError, ProfileError) as exc:
            raise _ProfileIOError(f"Profile creation could not be verified. Backup and write plan: {backup}. {exc}") from exc
        try:
            if _unit_id(device) != source._unit:
                raise ProfileError("The mouse changed before activation.")
            return activate_profile(device, slot)
        except (OSError, HardwareError, ProfileError) as exc:
            raise ProfileError(f"Profile {slot} was created and verified. Select it after reloading. {exc}") from exc
