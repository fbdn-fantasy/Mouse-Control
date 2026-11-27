"""Interactive mouse illustration and opt-in live button display."""

import time
from gi.repository import GLib, Gtk
from .mouse_view import MouseView


class ButtonInspector(Gtk.Box):
    def __init__(self, owner):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.owner = owner
        self.mouse = None
        self.monitor_id = None
        self.monitor_pending = False
        self.last_push = 0
        self.selection = None
        self.add_css_class("editable-panel")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Mouse view", xalign=0, hexpand=True)
        title.add_css_class("section")
        header.append(title)
        self.toggle = Gtk.ToggleButton(label="Show presses", sensitive=False)
        self.toggle.connect("toggled", self._toggle)
        header.append(self.toggle)
        self.append(header)
        self.view = MouseView("Generic", on_select=self._select)
        self.view.set_vexpand(True)
        self.view.set_size_request(300, 290)
        self.append(self.view)
        self.help = Gtk.Label(label="Drag to rotate. Select a button to inspect it.", xalign=0, wrap=True)
        self.help.add_css_class("subtle")
        self.append(self.help)
        self.assign = Gtk.Button(label="Edit selected assignment…", halign=Gtk.Align.START)
        self.assign.connect("clicked", owner._open_profile)
        self.assign.set_visible(False)
        self.append(self.assign)
        self.events = Gtk.Label(xalign=0, wrap=True)
        self.append(self.events)
        self.timer = GLib.timeout_add(65, self._sample)

    def _select(self, index):
        self.selection = index
        if self.mouse:
            self.help.set_text(self.view.button_name(index))
            self.update_assignment()

    def update_assignment(self):
        state = self.owner.state
        self.assign.set_visible(bool(self.selection is not None and self.view.kind != "generic"
                                     and state and state.can_edit_profile))

    def set_mouse(self, mouse):
        if self.mouse == mouse:
            return
        self.toggle.set_active(False)
        self.selection = None
        self.assign.set_visible(False)
        self.mouse = mouse
        self.events.set_text("")
        self.view.set_pressed(0)
        self.view.set_model(mouse.name if mouse else "Generic")
        self.view.set_visible(bool(mouse))
        self.toggle.set_sensitive(bool(mouse))
        hint = "Representative mouse shape. " if self.view.kind == "generic" else ""
        self.help.set_text(hint + "Drag to rotate. Select a button to inspect it." if mouse else "Connect a mouse to inspect it.")

    def _toggle(self, widget):
        previous = self.monitor_id
        self.monitor_id = None
        if previous:
            self.owner._job(lambda: self.owner.backend.disable_button_monitor(previous), lambda *_: None)
        self.view.set_pressed(0)
        self.events.set_text("")
        if not self.mouse or not widget.get_active():
            return
        mouse_id = self.mouse.id
        self.monitor_id = mouse_id
        self.events.set_text("Connecting the button monitor…")

        def enabled(_, error):
            if self.monitor_id == mouse_id and error:
                self.toggle.set_active(False)
                self.events.set_text(error)

        self.owner._job(lambda: self.owner.backend.enable_button_monitor(mouse_id), enabled)

    def _sample(self):
        if self.owner.closed:
            return GLib.SOURCE_REMOVE
        if not self.monitor_id or self.monitor_pending or self.owner.changing:
            return GLib.SOURCE_CONTINUE
        mouse_id = self.monitor_id
        self.monitor_pending = True

        def sampled(report, error):
            self.monitor_pending = False
            if self.monitor_id != mouse_id:
                return
            if error:
                self.toggle.set_active(False)
                self.events.set_text(error)
                return
            physical = set(report.physical_held) | set(report.physical_pressed)
            self.view.set_pressed(sum(1 << index for index in physical))
            mapped = set(report.mapped_held) | set(report.mapped_pressed)
            now = time.monotonic()
            if physical:
                text = "Physical buttons · " + ", ".join(self.view.button_name(i) or f"Button {i+1}" for i in sorted(physical))
            elif mapped:
                text = "Mapped buttons · " + ", ".join(n.removeprefix("BTN_").title() for n in sorted(mapped))
            elif now - self.last_push < .65:
                return
            else:
                self.events.set_text(report.detail or "Waiting for a button press…")
                return
            self.last_push = now
            self.events.set_text(text)

        self.owner._job(lambda: self.owner.backend.buttons(mouse_id), sampled)
        return GLib.SOURCE_CONTINUE
