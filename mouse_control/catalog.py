"""Retail names and Linux HID product IDs. A catalog match grants no write access."""

import re

# A shared regional ID cannot distinguish every retail name. In those cases
# use the device-reported name first and fall back to the first-listed model.
# A mouse can also be recognized by name when a connection exposes a new ID.
CATALOG = {
    "G-Series": {
        "G100s": "c247",
        "G102 Prodigy": "c084",
        "G102 LIGHTSYNC": "c09d c092",
        "G103": "c084",
        "G203 Prodigy": "c084",
        "G203 LIGHTSYNC": "c092",
        "G300": "c246",
        "G300s": "c246",
        "G302 Daedalus Prime": "c07f",
        "G303 Daedalus Apex": "c080",
        "G303 Shroud Edition": "c097",
        "G304 LIGHTSPEED": "4074",
        "G305 LIGHTSPEED": "4074",
        "G309 LIGHTSPEED": "40b1 b03c",
        "G400": "c245",
        "G400s": "c24c",
        "G402 Hyperion Fury": "c07e",
        "G403 Prodigy": "c083",
        "G403 Prodigy Wireless": "405d c082",
        "G403 HERO": "c08f",
        "G500": "c068",
        "G500s": "c24e",
        "G502 Proteus Core": "c07d",
        "G502 Proteus Spectrum": "c332",
        "G502 HERO": "c08b",
        "G502 LIGHTSPEED": "407f c08d",
        "G502 X": "c099",
        "G502 X LIGHTSPEED": "409f c098",
        "G502 X PLUS": "4099 c095",
        "G600 MMO": "c24a",
        "G602": "402c",
        "G603 LIGHTSPEED": "406c b01c",
        "G604 LIGHTSPEED": "4085 b024",
        "G700": "c531 c06b",
        "G700s": "c07c",
        "G703 LIGHTSPEED": "4070 c087",
        "G703 HERO": "4086 c090",
        "G705": "409d b02e c096",
        "G900 Chaos Spectrum": "4053 c081",
        "G903 LIGHTSPEED": "4067 c086",
        "G903 HERO": "4087 c091",
    },
    "PRO Series": {
        "G PRO Gaming Mouse": "c085",
        "G PRO HERO": "c08c",
        "G PRO Wireless": "4079 c088",
        "PRO 2 LIGHTSPEED": "c09a 40a8",
        "PRO X SUPERLIGHT": "4093 c094",
        "PRO X SUPERLIGHT 2": "c09b 40a9",
        "PRO X SUPERLIGHT 2 DEX": "c0a0 40b8",
        "PRO X2 SUPERSTRIKE": "40bd c0a8",
    },
    "Productivity": {
        "MX Master": "4041 4060 4071 b012 b017 b01e",
        "MX Master 2S": "4069 b019",
        "MX Master 3": "4082 b023",
        "MX Master 3S": "b034 b035",
        "MX Master 4": "b042",
        "MX Anywhere 2": "404a 4072 4063 b013 b018 b01f",
        "MX Anywhere 2S": "406a b01a",
        "MX Anywhere 3": "4090 b025 b02d",
        "MX Anywhere 3S": "b037",
        "MX Vertical": "407b b020 c08a",
        "MX Ergo": "406f b01d",
        "MX Ergo S": "b03e",
        "Lift": "b031 b033",
        "M720 Triathlon": "405e b015",
        "M585": "406b b01b",
        "M590": "406b b01b",
    },
    "Legacy Gaming": {
        "G5": "c041 c049",
        "G7": "c51a",
        "G9": "c048",
        "G9x": "c066 c249",
        "MX518": "c01e c051",
        "MX518 Legendary": "c08e",
    },
}
ALIASES = {
    "g102": "G102 Prodigy", "g203": "G203 Prodigy", "g302": "G302 Daedalus Prime",
    "g303": "G303 Daedalus Apex", "g304": "G304 LIGHTSPEED", "g305": "G305 LIGHTSPEED",
    "g309": "G309 LIGHTSPEED", "g402": "G402 Hyperion Fury", "g403 wireless": "G403 Prodigy Wireless",
    "g600": "G600 MMO", "g603": "G603 LIGHTSPEED", "g604": "G604 LIGHTSPEED",
    "g900": "G900 Chaos Spectrum", "pro x 2 superstrike": "PRO X2 SUPERSTRIKE",
    "pro x wireless superlight": "PRO X SUPERLIGHT", "mx ergo s": "MX Ergo S",
    "m720": "M720 Triathlon",
}


def _words(name):
    return " ".join(re.findall(r"[a-z0-9]+", name.lower()))


_NAMES = {name: tuple(int(i, 16) for i in ids.split())
          for group in CATALOG.values() for name, ids in group.items()}
_LOOKUP = sorted([(_words(name), name) for name in _NAMES] + list(ALIASES.items()),
                 key=lambda pair: -len(pair[0]))


def identify(product: int, name: str) -> str | None:
    """Resolve IDs and delimited product names without treating a receiver as a mouse."""
    words = " " + _words(name) + " "
    if any(f" {kind} " in words for kind in ("keyboard", "receiver", "headset")):
        return None
    by_id = [label for label, ids in _NAMES.items() if product in ids]
    for token, label in _LOOKUP:
        if f" {token} " in words and (not by_id or label in by_id):
            return label
    # The common receiver child often exposes only the shared G304/G305 ID.
    if product == 0x4074:
        return "G305 LIGHTSPEED"
    return by_id[0] if by_id else None
