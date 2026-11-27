"""Known HID++ gaming mice with hardware-verified profile-layout checks at runtime.

USB and paired wireless product IDs come from the retail catalog. Receiver IDs
alone never authorize profile writes. Runtime descriptors select the byte
layout for each model and the subset of fields documented for its format.
"""

from .catalog import CATALOG, identify

_COMMON = ("G1 · Left click", "G2 · Right click", "G3 · Wheel click", "G4 · Back", "G5 · Forward")
_SIX = _COMMON + ("G6 · Top button",)
_G502 = _COMMON + ("G6 · Thumb paddle", "G7 · Left top button", "G8 · Left top button",
                   "G9 · Behind wheel", "Wheel tilt right", "Wheel tilt left")
_LEGACY_FORMATS = (1, 2, 3, 4, 5)


def _numbered(count):
    return tuple(f"Button {i}" for i in range(1, count + 1))


MODEL_PROFILES = {
    "G502 HERO": ((2,), _G502),
    "G305": ((3,), _SIX),
    "G203 Prodigy": (_LEGACY_FORMATS, _SIX),
    "G203 LIGHTSYNC": (_LEGACY_FORMATS, _SIX),
    "G502 LIGHTSPEED": (_LEGACY_FORMATS, _G502),
    "G PRO Wireless": (_LEGACY_FORMATS, _COMMON + ("G6 · Right rear side button", "G7 · Right front side button",
                                                   "G8 · Underside DPI button")),
    "G903 HERO": ((4,), _numbered(12)),
    "G604": ((3,), _numbered(16)),
    "G502 Proteus Core": ((1,), _G502),
    "G502 Proteus Spectrum": ((2,), _G502),
    "G402 Hyperion Fury": ((1,), _numbered(8)),
    "G502 X": ((4,), _numbered(11)),
    "G502 X LIGHTSPEED": (_LEGACY_FORMATS, _numbered(11)),
    "G502 X PLUS": (_LEGACY_FORMATS, _numbered(11)),
    "G900 Chaos Spectrum": (_LEGACY_FORMATS, _numbered(11)),
    "G903 LIGHTSPEED": (_LEGACY_FORMATS, _numbered(11)),
    "G302 Daedalus Prime": (_LEGACY_FORMATS, _SIX),
    "G303 Daedalus Apex": (_LEGACY_FORMATS, _SIX),
    "G303 Shroud Edition": (_LEGACY_FORMATS, _numbered(5)),
    "G403 Prodigy": (_LEGACY_FORMATS, _SIX),
    "G403 Prodigy Wireless": (_LEGACY_FORMATS, _SIX),
    "G403 HERO": (_LEGACY_FORMATS, _SIX),
    "G603 LIGHTSPEED": (_LEGACY_FORMATS, _SIX),
    "G703 LIGHTSPEED": (_LEGACY_FORMATS, _SIX),
    "G703 HERO": (_LEGACY_FORMATS, _SIX),
    "G705": (_LEGACY_FORMATS, _numbered(6)),
    "G PRO Gaming Mouse": (_LEGACY_FORMATS, _SIX),
    "G PRO HERO": (_LEGACY_FORMATS, _SIX),
    "PRO X SUPERLIGHT": (_LEGACY_FORMATS, _COMMON),
    "PRO X SUPERLIGHT 2": ((6, 7), _COMMON),
    "PRO X SUPERLIGHT 2 DEX": ((7,), _COMMON),
    "PRO 2 LIGHTSPEED": ((7,), _numbered(8)),
    "PRO X2 SUPERSTRIKE": ((8,), _COMMON),
    "G309 LIGHTSPEED": ((_LEGACY_FORMATS + (6, 7)), _SIX),
    "MX518 Legendary": (_LEGACY_FORMATS, _numbered(8)),
}

_ALIASES = {
    "G102 Prodigy": "G203 Prodigy", "G103": "G203 Prodigy", "G102 LIGHTSYNC": "G203 LIGHTSYNC",
    "G304 LIGHTSPEED": "G305", "G305 LIGHTSPEED": "G305", "G604 LIGHTSPEED": "G604",
}
for _alias, _model in _ALIASES.items():
    MODEL_PROFILES[_alias] = MODEL_PROFILES[_model]

# Product matches are restricted to these independently supported cohorts.
_PRODUCTS = {int(pid, 16) for group in CATALOG.values() for model, ids in group.items()
             if model in MODEL_PROFILES for pid in ids.split()}


def model_for_product(product_id: int, name: str = "") -> str | None:
    if product_id not in _PRODUCTS:
        return None
    label = identify(product_id, name)
    return _ALIASES.get(label, label) if label in MODEL_PROFILES else None


def button_count(model: str) -> int:
    profile = MODEL_PROFILES.get(model)
    return len(profile[1]) if profile else 0


def layout_labels(model: str, layout: int, reported_count: int) -> tuple[str, ...]:
    """Use numbered positions when a firmware revision exposes a different array."""
    formats, labels = MODEL_PROFILES[model]
    if layout not in formats or not 1 <= reported_count <= 16:
        return ()
    return labels if len(labels) == reported_count else _numbered(reported_count)
