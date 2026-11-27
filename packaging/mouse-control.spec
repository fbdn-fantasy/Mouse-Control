Name:           mouse-control
Version:        1.0.0
Release:        1
Summary:        Native Logitech mouse controls
License:        AGPL-3.0-only
Source0:        %{name}-%{version}-payload.tar
BuildArch:      noarch
Requires:       python3, python3-gobject, python3-cairo, gtk4, libadwaita, systemd-udev

%description
Inspect 71 Logitech mouse models in an interactive GTK application.
Depending on the mouse and connection, display live sensitivity, monitor
button presses, change DPI and polling rate, or edit onboard assignments.

%install
mkdir -p "%{buildroot}"
tar -xf "%{SOURCE0}" -C "%{buildroot}"

%files
%{_bindir}/mouse-control
%{_datadir}/mouse-control
%{_datadir}/applications/io.github.mousecontrol.MouseControl.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.mousecontrol.MouseControl.svg
/usr/lib/udev/rules.d/70-mouse-control.rules
/usr/lib/udev/mouse-control-access
%dir %{_docdir}/mouse-control
%license %{_docdir}/mouse-control/copyright
%license %{_docdir}/mouse-control/LICENSE
%license %{_docdir}/mouse-control/NOTICE
%doc %{_docdir}/mouse-control/README.md
%doc %{_docdir}/mouse-control/COMPATIBILITY.md
