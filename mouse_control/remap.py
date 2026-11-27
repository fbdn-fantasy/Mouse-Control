"""HID++ 0x1B04 remapping between native actions exposed by one mouse.

Logitech's special-keys and mouse-buttons v4 protocol defines group n as
bit n-1. Sending zero in all validity flags leaves diversion settings alone.
These assignments are temporary across a device configuration reset.
"""

from dataclasses import dataclass
import struct

FEATURE = 0x1B04
_LABELS = {
    0x50: "Left click", 0x51: "Right click", 0x52: "Middle click",
    0x53: "Back", 0x56: "Forward", 0xC3: "Gesture button", 0xC4: "SmartShift",
}


class ControlError(ValueError):
    """The requested control remapping is unsupported or could not be verified."""


@dataclass(frozen=True)
class Control:
    cid: int
    label: str
    target: int
    choices: tuple[tuple[int, str], ...]
    diverted: bool


def _name(cid):
    return _LABELS.get(cid, f"Control 0x{cid:04X}")


def _reporting(device, cid):
    data = device.query(FEATURE, 2, struct.pack(">H", cid))
    if len(data) < 5 or int.from_bytes(data[:2], "big") != cid:
        raise ControlError("The mouse returned an incomplete or unrelated button mapping.")
    target = int.from_bytes(data[3:5], "big") or cid
    return target, data[2], data[5] if len(data) >= 6 else 0


def read_controls(device):
    """Return the physical remappable controls and their firmware-approved choices."""
    count = device.query(FEATURE, 0)
    if not count:
        raise ControlError("The mouse returned an incomplete control count.")
    inventory = {}
    for index in range(count[0]):
        reply = device.query(FEATURE, 1, bytes((index,)))
        if len(reply) < 8:
            raise ControlError("The mouse returned an incomplete button description.")
        cid, _, flags, _, group, mask = struct.unpack_from(">HHBBBB", reply)
        if cid == 0 or cid in inventory or group > 8:
            raise ControlError("The mouse returned an inconsistent button table.")
        inventory[cid] = flags, group, mask
    results = []
    for cid, (flags, _, mask) in inventory.items():
        if not flags & 0x10 or flags & 0x80 or not mask:
            continue
        choices = tuple((other, _name(other)) for other, (_, group, _) in inventory.items()
                        if other == cid or (group and mask & (1 << (group - 1))))
        target, reporting_flags, _ = _reporting(device, cid)
        results.append(Control(cid, _name(cid), target, choices, bool(reporting_flags & 1)))
    return tuple(results)


def set_control(device, source_cid, target_cid, *, expected_target=None):
    """Map one physical control. Read back both the target and unrelated flags."""
    controls = read_controls(device)
    control = next((item for item in controls if item.cid == source_cid), None)
    if control is None or target_cid not in dict(control.choices):
        raise ControlError("The mouse does not support this button assignment.")
    previous, flags, extra = _reporting(device, source_cid)
    if expected_target is not None and previous != expected_target:
        raise ControlError("This assignment changed. Reload the buttons before saving.")
    if previous == target_cid:
        return control
    if control.diverted:
        raise ControlError("Another application diverts this button. Release its diversion before remapping.")
    if previous == 0x50 and not any(item.cid != source_cid and item.target == 0x50
                                    and not item.diverted for item in controls):
        raise ControlError("Assign left click to another button before removing the last left click.")
    # A zero validity mask requests no changes to diversion, rawXY, or other flags.
    device.query(FEATURE, 3, struct.pack(">HBH", source_cid, 0, target_cid))
    observed, new_flags, new_extra = _reporting(device, source_cid)
    if (observed, new_flags, new_extra) != (target_cid, flags, extra):
        raise ControlError("The mouse did not confirm the requested mapping and unchanged reporting flags.")
    return Control(control.cid, control.label, observed, control.choices, bool(new_flags & 1))
