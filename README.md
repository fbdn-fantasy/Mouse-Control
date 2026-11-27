# Mouse Control

Mouse Control is an open-source, native Linux desktop application for configuring Logitech mice without requiring Logitech G HUB or Logi Options+.

Adjust mouse sensitivity, customise button assignments, manage profiles, and monitor button presses through a graphical interface. The application communicates directly with connected mice and exposes configuration options based on their hardware and firmware capabilities.

## Features

- **DPI adjustment** – View and change mouse sensitivity, configure DPI stages, and monitor sensitivity changes in real time.
- **DPI Shift** – Configure temporary sensitivity adjustments on compatible mice.
- **Button remapping** – Customise supported mouse button assignments.
- **Polling rate** – Adjust the mouse reporting rate using supported hardware settings.
- **3D mouse diagrams** – Rotate and interact with mouse models to inspect button positions and assignments.
- **Live button monitoring** – Visually identify button presses and mapped actions.
- **Onboard profiles** – Create, edit, and switch between supported hardware profiles.
- **Local profiles** – Save named configurations on your computer and load them onto compatible mice whenever needed.
- **Configuration backups** – Automatically back up onboard profiles before modifying them.
- **Hardware verification** – Read settings back from the mouse after applying changes.
- **Mouse catalog** – Browse and preview 71 Logitech mouse models without connecting them.
- **Connection support** – Supports compatible wired USB, wireless receiver, and Bluetooth mice.

## Supported Mice

Mouse Control includes a catalog of 71 Logitech mouse models across several product families:

- Logitech G-Series
- Logitech G PRO Series
- Logitech MX Master Series
- Logitech MX Anywhere Series
- Logitech ergonomic and productivity mice
- Legacy Logitech gaming mice

Available configuration options depend on the mouse model, firmware, connection type, and onboard memory capabilities. Some older models offer limited configuration support.

See the [Compatibility Matrix](COMPATIBILITY.md) for the complete model list and supported functionality.

## Installation

Mouse Control is available in the following Linux package formats:

- **RPM** – Fedora and compatible distributions.
- **DEB** – Debian and compatible distributions.
- **TAR** – Manual installation.

### Fedora

Download the RPM package from the Releases section and install it using:

`sudo dnf install ./mouse-control-1.0.0-1.noarch.rpm`

### Debian

Download the DEB package from the Releases section and install it using:

`sudo apt install ./mouse-control_1.0.0-1_all.deb`

After installation, reconnect your mouse or wireless receiver to activate the device-access permissions. Bluetooth devices may need to be disconnected and reconnected.

Launch Mouse Control from your applications menu.

### Manual Installation

Extract the TAR archive and run the following commands from the extracted directory:

```sh
sudo install -Dm755 mouse-control-access /usr/lib/udev/mouse-control-access
sudo install -Dm644 70-mouse-control.rules /etc/udev/rules.d/70-mouse-control.rules
sudo udevadm control --reload-rules
```

Reconnect your mouse and launch the application using:

`./Mouse-Control-V1.0.0`

## Requirements

- Linux
- Python 3.10 or later
- GTK 4
- libadwaita 1
- PyGObject with Cairo support
- Pycairo

The RPM and DEB packages handle the required dependencies and device-access configuration automatically.

The standalone Linux executable targets x86-64 systems.

## Hardware Configuration

Mouse Control communicates directly with supported devices through Linux HID interfaces. Configuration options are determined by the capabilities exposed by the connected mouse.

Onboard settings are stored directly on compatible mice, while local profiles can be saved and managed independently. Device-session settings may reset when a mouse is disconnected or powered off.

Mouse Control is independently developed and is not affiliated with or endorsed by Logitech.

## License

Mouse Control is licensed under the **GNU Affero General Public License v3.0 only (AGPL-3.0-only)**.

See [LICENSE](LICENSE) and [NOTICE](NOTICE) for details.