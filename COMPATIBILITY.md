# Mouse Control 1.0.0 compatibility

The catalog contains 71 retail models. Each has a rotatable diagram. Hardware controls depend on the product ID, connection, firmware features and onboard memory layout. Models that share an ID may appear under a regional sibling’s name.

| Models | Live DPI and sensitivity | Buttons, polling and profiles |
| --- | --- | --- |
| G102 Prodigy, G102 LIGHTSYNC, G103, G203 Prodigy, G203 LIGHTSYNC | Sensor and active onboard step | Gaming profile editor |
| G302 Daedalus Prime, G303 Daedalus Apex, G303 Shroud Edition | Sensor and active onboard step | Gaming profile editor |
| G304 LIGHTSPEED, G305 LIGHTSPEED | Sensor and active onboard step, including the factory profile | Gaming profile editor. One onboard slot, additional named profiles on the computer |
| G309 LIGHTSPEED | Sensor and recognized onboard DPI tables | Gaming profile editor. Onboard polling-rate editing depends on the layout |
| G402 Hyperion Fury, G403 Prodigy, G403 Prodigy Wireless, G403 HERO | Sensor and active onboard step | Gaming profile editor |
| G502 Proteus Core, G502 Proteus Spectrum, G502 HERO, G502 LIGHTSPEED | Sensor and active onboard step | Gaming profile editor. The wired HERO has up to five slots |
| G502 X, G502 X LIGHTSPEED, G502 X PLUS | Sensor and active onboard step | Gaming profile editor |
| G603 LIGHTSPEED, G604 LIGHTSPEED, G703 LIGHTSPEED, G703 HERO, G705 | Sensor and active onboard step | Gaming profile editor |
| G900 Chaos Spectrum, G903 LIGHTSPEED, G903 HERO | Sensor and active onboard step | Gaming profile editor |
| G PRO Gaming Mouse, G PRO HERO, G PRO Wireless, PRO X SUPERLIGHT | Sensor and active onboard step | Gaming profile editor |
| PRO 2 LIGHTSPEED | Sensor and recognized onboard DPI table | Format 7 profile editor. The onboard polling-rate field is unavailable |
| PRO X SUPERLIGHT 2, PRO X SUPERLIGHT 2 DEX | Sensor and recognized onboard DPI tables. Separate X/Y stages on compatible firmware | Formats 6/7 on SUPERLIGHT 2, format 7 on DEX. Wired and wireless report rates are handled separately |
| PRO X2 SUPERSTRIKE | Live sensor reading when exposed. The format 8 DPI ladder is unavailable | Format 8 supports documented button fields and wireless polling-rate field. Haptic actuation settings are unavailable |
| G300, G300s, G600 MMO | USB active profile and step, with four editable DPI values | Three fixed profiles, editable polling rate and button assignments. G600 secondary G-Shift assignments are preserved |
| G400, G400s | Model-specific USB sensitivity controls, limited to recognized stage codes | Model-specific polling control. No onboard profile editor |
| G602 | Live sensor readout. Use the mouse buttons to change DPI | Session polling depends on advertised features. No onboard profile editor |
| G500, G500s, G700s, G5 | Recognized USB revisions use model-specific DPI register values. Use the wired connection for G700s | Live polling-rate selection and readback for the documented revisions. Onboard profiles and persistent button remapping are unavailable |
| G700, G9, G9x | Recognized USB revisions use model-specific DPI register values. Use the wired connection for G700 | Polling interval readout where available. Onboard profiles and persistent button remapping are unavailable |
| MX518 | USB revision C01E supports 400, 800, 1200 and 1600 DPI. C051 offers the two confirmed lower values, 400 and 800 | No onboard profile editor |
| MX518 Legendary | Sensor and active onboard step | Gaming profile editor |
| MX Master, MX Master 2S, MX Master 3, MX Master 3S, MX Master 4 | Firmware sensor probe | Device-session controls |
| MX Anywhere 2, MX Anywhere 2S, MX Anywhere 3, MX Anywhere 3S | Firmware sensor probe | Device-session controls |
| MX Vertical, MX Ergo, MX Ergo S, Lift | Firmware sensor probe | Device-session controls |
| M720 Triathlon, M585, M590 | A fixed or unreported sensor may offer no writable DPI control | Device-session controls |
| G100s, G7 | No dedicated DPI implementation | Catalog and available pointer input. Hardware configuration remains unsupported |

**Gaming profile editor.** The mouse must be in onboard mode and report a recognized layout. Available operations include selecting and editing DPI stages, editing primary-layer button assignments, changing advertised polling rates, and switching profiles. A named new onboard profile requires a writable unused slot. Named copies on the computer can be loaded into an existing slot. Additional layers, macros and unknown assignments are preserved when other settings change.

**Device-session controls.** The app asks the mouse for its sensor, reporting intervals and allowable button assignments. Fixed sensors may provide a readout alone. A remappable mouse can offer only a subset of its buttons and target actions. Session mappings can reset on disconnect or power cycle. The productivity series has no onboard profile editor in this release. USB, Bluetooth and receiver connections can expose different controls.

**Button monitoring.** When physical-position reports are available in the current mode, the corresponding physical buttons light up. The fallback shows mapped actions from a mouse-only Linux input interface or the paired G305’s standard HID mouse-button report. Combined keyboard/mouse Linux event streams are skipped. A firmware-only DPI button may produce no visible press event while the DPI number still changes. Diagrams use family geometry, with representative controls where the exact positions or numbering are unknown.

**Protocol references.** The implementation uses public specifications, USB IDs and published device observations. [Logitech device protocol documentation](https://github.com/Logitech/cpg-docs), [Logitech native button mapping specification](https://lekensteyn.nl/files/logitech/x1b04_specialkeysmsebuttons.html), [published onboard memory observations](https://github.com/OpenMouse-Project/mouse-protocol/blob/main/docs/logitech-onboard-profiles.md), [G400s hardware observations](https://github.com/rprichard/logitech-g400-config/issues/3).
