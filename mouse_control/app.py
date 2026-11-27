"""GTK desktop app. Hardware work runs on a single background thread."""

import logging
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import as_file, files
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from .hardware import MouseBackend
from .inspector import ButtonInspector
from .preferences import THEMES, read_preferences, save_preferences
from .presets import read_presets, save_preset
from .profiles import BUTTON_ACTIONS, activate_profile, create_profile, read_profile, read_roster, save_profile
from .remap import read_controls, set_control
from .sensors import report_rates, set_report_rate


def column(spacing=8):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)


def row(spacing=10):
    return Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)


def label(text="", style=None, wrap=False):
    item = Gtk.Label(label=text, xalign=0, wrap=wrap)
    if style:
        item.add_css_class(style)
    return item


def margins(widget, amount):
    widget.set_margin_start(amount)
    widget.set_margin_end(amount)
    widget.set_margin_top(amount)
    widget.set_margin_bottom(amount)


def scroll(widget):
    container = Gtk.ScrolledWindow(vexpand=True)
    container.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    container.set_child(widget)
    return container


def message_area():
    item = label(style="message", wrap=True)
    item.set_selectable(True)
    item.set_visible(False)
    return item


def show_message(widget, text):
    widget.set_text(text)
    widget.set_visible(bool(text))


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app, backend):
        super().__init__(application=app, title="Mouse Control")
        self.backend = backend
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mouse-hid")
        self.closed = False
        self.polling = False
        self.changing = False
        self.rebuilding = False
        self.mice = []
        self.selected = None
        self.state = None
        self.editor = None
        self.last_discovery = 0
        self.preferences = read_preferences()
        self.pending_target = False
        self.setting_target = False
        self.add_css_class("mouse-control")
        width = self.preferences.get("width", 1060)
        height = self.preferences.get("height", 660)
        self.set_default_size(width if type(width) is int and width >= 420 else 640,
                              height if type(height) is int and height >= 360 else 660)
        if self.preferences.get("maximized", True):
            self.maximize()
        header = Gtk.HeaderBar()
        self.set_titlebar(header)
        catalog = Gtk.Button(label="Mouse catalog")
        catalog.connect("clicked", self._browse)
        header.pack_start(catalog)
        theme = Gtk.DropDown.new_from_strings(list(THEMES))
        theme.set_tooltip_text("Appearance")
        theme.update_property([Gtk.AccessibleProperty.LABEL], ["Appearance"])
        initial = self.preferences.get("theme", "System")
        theme.set_selected(THEMES.index(initial) if initial in THEMES else 0)
        theme.connect("notify::selected", self._theme)
        self._theme(theme)
        header.pack_end(theme)

        body = column(18)
        margins(body, 24)
        clamp = Adw.Clamp(maximum_size=1190, tightening_threshold=850)
        clamp.set_child(body)
        self.set_child(scroll(clamp))
        devices = row()
        self.selector = Gtk.DropDown.new_from_strings(["Looking for a mouse…"])
        self.selector.set_hexpand(True)
        self.selector.set_tooltip_text("Choose a connected mouse")
        self.selector.connect("notify::selected", self._select)
        devices.append(self.selector)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Find connected mice")
        refresh.connect("clicked", self._refresh)
        devices.append(refresh)
        body.append(devices)
        workspace = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                column_spacing=14, row_spacing=12, min_children_per_line=1,
                                max_children_per_line=2, valign=Gtk.Align.START)
        workspace.add_css_class("workspace")
        self.inspector = ButtonInspector(self)
        workspace.insert(self.inspector, -1)
        sidebar = column(12)
        sidebar.set_size_request(300, -1)
        workspace.insert(sidebar, -1)
        body.append(workspace)

        panel = column(4)
        panel.add_css_class("metric-panel")
        self.metric_title = label("Active sensitivity", "metric-label")
        panel.append(self.metric_title)
        metric_line = row(8)
        self.metric = label("—", "metric")
        metric_line.append(self.metric)
        metric_line.append(label("DPI", "unit"))
        panel.append(metric_line)
        self.mode = label("Looking for a mouse…", wrap=True)
        panel.append(self.mode)
        sidebar.append(panel)

        target_panel = column(10)
        target_panel.add_css_class("editable-panel")
        self.live_title = label("Change active DPI", "section")
        target_panel.append(self.live_title)
        target_row = row()
        self.target = Gtk.DropDown.new_from_strings([])
        self.target.set_hexpand(True)
        self.target.set_tooltip_text("Choose the DPI to apply")
        self.target.connect("notify::selected", self._target_changed)
        target_row.append(self.target)
        self.apply = Gtk.Button(label="Apply DPI")
        self.apply.add_css_class("apply-action")
        self.apply.connect("clicked", self._apply_dpi)
        target_row.append(self.apply)
        target_panel.append(target_row)
        self.target_hint = label(style="subtle", wrap=True)
        target_panel.append(self.target_hint)
        sidebar.append(target_panel)

        profile_panel = column(10)
        profile_panel.add_css_class("editable-panel")
        profile_panel.append(label("Device settings", "section"))
        profile_panel.append(label("Profiles, button assignments and polling rate.", "subtle", True))
        self.edit = Gtk.Button(label="Configure mouse…", halign=Gtk.Align.START)
        self.edit.connect("clicked", self._open_profile)
        profile_panel.append(self.edit)
        sidebar.append(profile_panel)
        self.feedback = message_area()
        body.append(self.feedback)
        self.choices = ()
        self._sensitivity()
        self.timer = GLib.timeout_add(450, self._poll)
        self.connect("close-request", self._close)
        self._poll()

    def _theme(self, widget, *_):
        index = widget.get_selected()
        scheme = (Adw.ColorScheme.DEFAULT, Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK)[index]
        Adw.StyleManager.get_default().set_color_scheme(scheme)
        self.preferences["theme"] = THEMES[index]

    def _browse(self, *_):
        from .model_browser import ModelBrowser
        ModelBrowser(self).present()

    def _target_changed(self, *_):
        if not self.setting_target:
            self.pending_target = True
            self._sensitivity()

    def _job(self, operation, done):
        future = self.worker.submit(operation)

        def complete():
            if not self.closed:
                try:
                    result = future.result()
                except Exception as error:
                    logging.debug("Mouse request failed", exc_info=True)
                    done(None, str(error) or type(error).__name__)
                else:
                    done(result, None)
            return GLib.SOURCE_REMOVE

        future.add_done_callback(lambda unused: GLib.idle_add(complete))

    def _select(self, widget, _param):
        if self.rebuilding:
            return
        idx = widget.get_selected()
        self.selected = self.mice[idx].id if idx < len(self.mice) else None
        self.inspector.set_mouse(self.mice[idx] if self.selected else None)
        self._clear_readout("Reading active DPI…")
        show_message(self.feedback, "")
        self._poll()

    def _refresh(self, *_):
        self.last_discovery = 0
        self._poll()

    def _poll(self):
        if self.closed:
            return GLib.SOURCE_REMOVE
        if self.polling or self.changing:
            return GLib.SOURCE_CONTINUE
        self.polling = True
        if time.monotonic() - self.last_discovery >= 3:
            self.last_discovery = time.monotonic()

            def discovered(mice, error):
                self.polling = False
                if error:
                    self._clear_readout(error)
                    return
                if self.mice != mice or (not mice and self.selected is None):
                    self.mice = mice
                    ids = [m.id for m in mice]
                    index = ids.index(self.selected) if self.selected in ids else 0
                    new_id = ids[index] if mice else None
                    if new_id != self.selected:
                        self._clear_readout("Reading active DPI…")
                        show_message(self.feedback, "")
                    self.selected = new_id
                    self.inspector.set_mouse(mice[index] if mice else None)
                    self.rebuilding = True
                    names = [m.name for m in mice]
                    options = [m.name if names.count(m.name) == 1 else f"{m.name} · {m.id.split('@')[-1]}"
                               for m in mice]
                    self.selector.set_model(Gtk.StringList.new(options or ["No supported mouse"]))
                    self.selector.set_selected(index)
                    self.rebuilding = False
                    self.selector.set_sensitive(len(mice) > 1)
                if not mice:
                    self._clear_readout("Connect a compatible Logitech mouse or its USB receiver.")
                else:
                    self._poll()

            self._job(self.backend.discover, discovered)
        elif self.selected:
            mouse_id = self.selected

            def received(state, error):
                self.polling = False
                if self.selected != mouse_id:
                    return
                if error:
                    self._clear_readout(error)
                else:
                    self._show_state(state)

            self._job(lambda: self.backend.read(mouse_id), received)
        else:
            self.polling = False
        return GLib.SOURCE_CONTINUE

    def _clear_readout(self, reason):
        self.state = None
        self.metric_title.set_text("Active sensitivity")
        self.metric.set_text("—")
        self.mode.set_text(reason)
        self._set_choices((), None)
        self.target_hint.set_text("")
        self._sensitivity()

    def _set_choices(self, values, dpi):
        self.setting_target = True
        if self.choices != values:
            self.choices = values
            self.target.set_model(Gtk.StringList.new([f"{v:,} DPI" for v in values]))
            self.pending_target = False
            self.target.set_enable_search(len(values) > 15)
        if not self.pending_target:
            self.target.set_selected(values.index(dpi) if dpi in values else Gtk.INVALID_LIST_POSITION)
        self.setting_target = False

    def _show_state(self, state):
        self.state = state
        stage_dpi = state.stages[state.active_stage] if state.onboard and state.active_stage is not None else None
        differs = bool(stage_dpi and state.dpi and stage_dpi != state.dpi)
        shown = stage_dpi if differs else state.dpi
        self.metric_title.set_text("Active onboard DPI step" if differs else "Active sensitivity")
        self.metric.set_text(f"{shown:,}" if shown else "—")
        if not state.can_set_dpi:
            self.mode.set_text(state.detail or ("Live sensor reading" if state.dpi else "Sensor DPI unavailable"))
            values = ()
            self.target_hint.set_text("A writable DPI range was not reported on this connection.")
        elif state.onboard:
            parts = ["Onboard profile"]
            if state.profile is not None:
                parts[0] += f" {state.profile & 255}"
            if state.active_stage is not None:
                parts.append(f"Step {state.active_stage + 1}")
            if differs:
                parts.append(f"Sensor reports {state.dpi:,} DPI")
            self.mode.set_text("  ·  ".join(parts))
            values = state.dpi_values
            self.target_hint.set_text("Select a saved DPI step. The physical DPI buttons still work.")
        else:
            self.mode.set_text("Sensor reading" if state.onboard is None else "Software-controlled sensitivity")
            values = state.dpi_values
            self.target_hint.set_text("Apply a sensor setting for the current session.")
        self._set_choices(values, shown)
        self._sensitivity()

    def _sensitivity(self):
        ready = self.state is not None and not self.changing
        self.apply.set_sensitive(bool(ready and self.target.get_selected() < len(self.choices)))
        self.target.set_sensitive(bool(ready and self.choices))
        self.edit.set_sensitive(bool(ready and self.state.mouse.has_protocol))
        self.selector.set_sensitive(not self.changing and len(self.mice) > 1)
        self.inspector.update_assignment()

    def _apply_dpi(self, *_):
        idx = self.target.get_selected()
        if not self.state or self.changing or idx >= len(self.choices):
            return
        mouse_id, dpi = self.selected, self.choices[idx]
        self.changing = True
        self._sensitivity()
        show_message(self.feedback, "")

        def applied(state, error):
            self.changing = False
            if mouse_id == self.selected:
                if error:
                    self._clear_readout("Refreshing the active DPI…")
                    show_message(self.feedback, error)
                else:
                    self.pending_target = False
                    self._show_state(state)
                    show_message(self.feedback, "")
            self._sensitivity()
            self._poll()

        self._job(lambda: self.backend.set_dpi(mouse_id, dpi), applied)

    def _open_profile(self, *_):
        if not self.state or self.changing:
            return
        if self.editor:
            self.editor.present()
            return
        mouse = self.state.mouse
        self.changing = True
        self._sensitivity()
        show_message(self.feedback, "Reading device settings…")
        onboard = self.state.can_edit_profile

        def read():
            with self.backend.device(mouse.id) as device:
                from .hardware import HardwareError
                from .profiles import ProfileError
                limitation = ""
                if onboard:
                    try:
                        snap = read_profile(device)
                        return "profile", snap, read_roster(device, snap)
                    except (HardwareError, ProfileError) as error:
                        limitation = str(error)
                try:
                    controls = read_controls(device)
                except (HardwareError, ValueError):
                    controls = ()
                try:
                    rate, options = report_rates(device)
                except (HardwareError, ValueError):
                    rate, options = None, ()
                return "session", controls, (rate, options, limitation)

        def loaded(snapshot, error):
            self.changing = False
            self._sensitivity()
            show_message(self.feedback, error or "")
            if snapshot is not None and self.selected == mouse.id:
                kind, data, extra = snapshot
                if kind == "profile":
                    self.editor = ProfileEditor(self, mouse, data, extra, self.inspector.selection)
                else:
                    self.editor = SessionEditor(self, mouse, data, extra)
                self.editor.present()

        self._job(read, loaded)

    def _close(self, *_):
        if self.editor and self.editor.pending:
            return True
        self.closed = True
        width, height = self.get_default_size()
        self.preferences.update(width=width, height=height, maximized=self.is_maximized())
        save_preferences(self.preferences)
        GLib.source_remove(self.timer)
        GLib.source_remove(self.inspector.timer)
        self.worker.submit(self.backend.close)
        self.worker.shutdown(wait=False)
        return False


class ProfileEditor(Gtk.Window):
    def __init__(self, parent, mouse, snapshot, roster=(), selected_button=None):
        super().__init__(title=f"{mouse.name} · Onboard profile", transient_for=parent,
                         modal=True, destroy_with_parent=True)
        self.owner = parent
        self.mouse = mouse
        self.snapshot = snapshot
        self.pending = False
        self.roster = roster
        self.add_css_class("mouse-control")
        self.set_default_size(560, 620)
        self.connect("close-request", self._close)
        root = column(0)
        self.set_child(root)
        content = column(16)
        margins(content, 20)
        root.append(scroll(content))
        content.append(label(mouse.name, "title"))
        content.append(label(f"Onboard profile {snapshot.slot}", "section"))
        self.name_entry = Gtk.Entry(text=snapshot.name, placeholder_text="Profile name")
        self.name_entry.set_tooltip_text("Name stored in this profile")
        self.name_entry.connect("changed", self._changed)
        self.name_entry.set_editable(getattr(snapshot, "can_rename", True))
        content.append(self.name_entry)
        self.slots = [s for s in roster if s.enabled and not s.active]
        self.free = [s for s in roster if s.creatable and not s.active]
        slot_row = row()
        if self.slots:
            self.slot_target = Gtk.DropDown.new_from_strings([f"{s.slot} · {s.name or 'Onboard profile'}" for s in self.slots])
            self.slot_target.set_hexpand(True)
            slot_row.append(self.slot_target)
            switch = Gtk.Button(label="Switch profile")
            switch.connect("clicked", self._switch)
            slot_row.append(switch)
            content.append(slot_row)
        if self.free:
            create_row = row()
            self.new_name = Gtk.Entry(placeholder_text="New profile name", hexpand=True)
            new = Gtk.Button(label=f"Create in slot {self.free[0].slot}")
            new.connect("clicked", self._create)
            create_row.append(self.new_name)
            create_row.append(new)
            slot_row = column()
            slot_row.append(create_row)
            content.append(slot_row)
        elif roster:
            count = len(roster)
            content.append(label("The mouse has one onboard slot." if count == 1 else
                                 f"{count} onboard slots. No unused writable slot is available.", "subtle", True))
        self.rate_values = tuple(snapshot.report_rates)
        self.rate = None
        if self.rate_values:
            rate_row = row()
            rate_label = label("Polling rate")
            rate_label.set_hexpand(True)
            rate_row.append(rate_label)
            self.rate = Gtk.DropDown.new_from_strings([f"{hz:,} Hz" for hz in self.rate_values])
            self.rate.set_selected(self.rate_values.index(snapshot.report_rate)
                                   if snapshot.report_rate in self.rate_values else Gtk.INVALID_LIST_POSITION)
            self.rate.connect("notify::selected", self._changed)
            rate_row.append(self.rate)
            content.append(rate_row)
        if snapshot.dpi_steps:
            content.append(label("DPI steps", "section"))
            content.append(label("Enter a sensitivity for each step supported by the mouse.", "subtle", True))
        steps = Gtk.Grid(column_spacing=8, row_spacing=4)
        self.entries = []
        self.y_entries = []
        y_steps = getattr(snapshot, "dpi_steps_y", ())
        for i, dpi in enumerate(snapshot.dpi_steps):
            steps.attach(label(f"Step {i + 1}"), i, 0, 1, 1)
            entry = Gtk.Entry(text=str(dpi), width_chars=5, max_width_chars=7, hexpand=True)
            entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
            entry.connect("changed", self._changed)
            steps.attach(entry, i, 1, 1, 1)
            self.entries.append(entry)
            if y_steps:
                entry.set_tooltip_text(f"Step {i + 1}, horizontal DPI")
                y = Gtk.Entry(text=str(y_steps[i]), width_chars=5, max_width_chars=7, hexpand=True)
                y.set_input_purpose(Gtk.InputPurpose.DIGITS)
                y.set_tooltip_text(f"Step {i + 1}, vertical DPI")
                y.connect("changed", self._changed)
                steps.attach(y, i, 2, 1, 1)
                self.y_entries.append(y)
        if y_steps:
            content.append(label("Horizontal DPI in the first row, vertical DPI in the second.", "subtle", True))
        if snapshot.dpi_steps:
            content.append(steps)
        self.defaults = {}
        if getattr(snapshot, "can_select_defaults", False):
            for attr, caption in (("default_index", "Default DPI step"), ("shift_index", "DPI-shift step")):
                index = getattr(snapshot, attr)
                if index is None or not 0 <= index < len(snapshot.dpi_steps):
                    continue
                line = row()
                desc = label(caption)
                desc.set_hexpand(True)
                line.append(desc)
                pick = Gtk.DropDown.new_from_strings([f"Step {n+1}" for n in range(len(snapshot.dpi_steps))])
                pick.set_selected(index)
                pick.connect("notify::selected", self._changed)
                self.defaults[attr] = pick
                line.append(pick)
                content.append(line)
        content.append(label("Button assignments", "section"))
        self.mappings = []
        for i, (physical, current) in enumerate(zip(snapshot.button_labels, snapshot.buttons)):
            actions = list(snapshot.actions)
            if current not in actions:
                actions.insert(0, current)
            names = [BUTTON_ACTIONS.get(key, "Keep existing assignment") for key in actions]
            line = row()
            button_label = label(physical, wrap=True)
            button_label.set_hexpand(True)
            line.append(button_label)
            dropdown = Gtk.DropDown.new_from_strings(names)
            dropdown.set_selected(actions.index(current))
            dropdown.connect("notify::selected", self._changed)
            line.append(dropdown)
            content.append(line)
            self.mappings.append((i, actions, dropdown))
            if i == selected_button:
                dropdown.add_css_class("suggested-action")
                self.set_focus(dropdown)
        content.set_sensitive(snapshot.editable)
        footer = column(8)
        margins(footer, 14)
        self.feedback = message_area()
        footer.append(self.feedback)
        controls = row()
        controls.set_halign(Gtk.Align.END)
        self.cancel = Gtk.Button(label="Close")
        self.cancel.connect("clicked", lambda *_: self.close())
        controls.append(self.cancel)
        self.save = Gtk.Button(label="Save to mouse")
        self.save.add_css_class("apply-action")
        self.save.set_sensitive(False)
        self.save.connect("clicked", self._save)
        controls.append(self.save)
        footer.append(controls)
        root.append(footer)
        self.content = content
        local = Gtk.Expander(label="Profiles on this computer")
        tools = column(8)
        local.set_child(tools)
        self.saved_list = Gtk.DropDown.new_from_strings([])
        self.saved_names = []
        load_row = row()
        self.saved_list.set_hexpand(True)
        load_row.append(self.saved_list)
        load = Gtk.Button(label="Load into editor")
        load.connect("clicked", self._load_local)
        load_row.append(load)
        tools.append(load_row)
        save_row = row()
        self.draft_name = Gtk.Entry(placeholder_text="New local profile name", hexpand=True)
        save_row.append(self.draft_name)
        store = Gtk.Button(label="Save local copy")
        store.connect("clicked", self._store_local)
        save_row.append(store)
        tools.append(save_row)
        content.append(local)
        try:
            self._list_local(read_presets(mouse.model))
        except (OSError, ValueError, AttributeError) as error:
            show_message(self.feedback, f"Could not read saved profiles: {error}")
        self.first_save_check = None
        if snapshot.first_save:
            self.first_save_check = Gtk.CheckButton(label="Create writable copies of the factory profiles")
            self.first_save_check.connect("toggled", self._changed)
            footer.prepend(self.first_save_check)
        if not snapshot.editable:
            show_message(self.feedback, snapshot.reason)

    def _list_local(self, profiles):
        self.saved_names = sorted(profiles, key=str.casefold)
        self.saved_list.set_model(Gtk.StringList.new(self.saved_names))
        self.saved_list.set_sensitive(bool(self.saved_names))

    def _store_local(self, *_):
        try:
            steps, _ = self._draft()
            draft = {"dpi": steps, "vertical": self.y_values, "rate": self._rate_choice(),
                     "buttons": [actions[pick.get_selected()] for _, actions, pick in self.mappings],
                     "indices": {attr: pick.get_selected() for attr, pick in self.defaults.items()}}
            self._list_local(save_preset(self.mouse.model, self.draft_name.get_text(), draft))
            self.saved_list.set_selected(self.saved_names.index(self.draft_name.get_text().strip()))
            self.draft_name.set_text("")
            show_message(self.feedback, "Local profile saved.")
        except (OSError, ValueError) as error:
            show_message(self.feedback, str(error))

    def _load_local(self, *_):
        idx = self.saved_list.get_selected()
        if idx >= len(self.saved_names):
            return
        try:
            draft = read_presets(self.mouse.model)[self.saved_names[idx]]
            fields = (("dpi", self.entries), ("vertical", self.y_entries))
            if (not isinstance(draft, dict)
                    or any(not isinstance(draft[key], list) or len(draft[key]) != len(items)
                           or any(type(value) is not int or not 0 <= value <= 65535 for value in draft[key])
                           for key, items in fields)
                    or not isinstance(draft["buttons"], list) or len(draft["buttons"]) != len(self.mappings)
                    or any(key not in actions for key, (_, actions, _) in zip(draft["buttons"], self.mappings))
                    or not isinstance(draft["indices"], dict) or set(draft["indices"]) != set(self.defaults)
                    or any(type(index) is not int or not 0 <= index < len(self.entries)
                           for index in draft["indices"].values())
                    or (draft["rate"] != self.snapshot.report_rate and draft["rate"] not in self.rate_values)):
                raise ValueError("This local profile was made with a different firmware layout.")
            for key, items in fields:
                for value, entry in zip(draft[key], items):
                    entry.set_text(str(value))
            for key, (_, actions, dropdown) in zip(draft["buttons"], self.mappings):
                if key in actions:
                    dropdown.set_selected(actions.index(key))
            for attr, pick in self.defaults.items():
                if attr in draft["indices"]:
                    pick.set_selected(draft["indices"][attr])
            if self.rate and draft["rate"] in self.rate_values:
                self.rate.set_selected(self.rate_values.index(draft["rate"]))
            self._changed()
            show_message(self.feedback, "Local profile loaded. Review the settings before saving to the mouse.")
        except (OSError, KeyError, TypeError, ValueError) as error:
            show_message(self.feedback, str(error))

    def _draft(self):
        steps = tuple(int(entry.get_text().strip()) for entry in self.entries)
        self.y_values = tuple(int(entry.get_text().strip()) for entry in self.y_entries)
        changes = {}
        for i, actions, dropdown in self.mappings:
            action = actions[dropdown.get_selected()]
            if self.snapshot.buttons[i] != action:
                changes[i] = action
        return steps, changes

    def _changed(self, *_):
        if not hasattr(self, "save") or self.pending:
            return
        try:
            steps, buttons = self._draft()
        except ValueError:
            self.save.set_sensitive(False)
            show_message(self.feedback, "Enter a whole number for each DPI step.")
            return
        allowed = self.first_save_check is None or self.first_save_check.get_active()
        self.save.set_sensitive(allowed and self.snapshot.editable and self._dirty(steps, buttons))
        show_message(self.feedback, "")

    def _rate_choice(self):
        if self.rate and self.rate.get_selected() < len(self.rate_values):
            return self.rate_values[self.rate.get_selected()]
        return self.snapshot.report_rate

    def _dirty(self, steps, buttons):
        return (steps != self.snapshot.dpi_steps or bool(buttons)
                or self.y_values != getattr(self.snapshot, "dpi_steps_y", ())
                or any(pick.get_selected() != getattr(self.snapshot, attr) for attr, pick in self.defaults.items())
                or self.name_entry.get_text() != self.snapshot.name
                or self._rate_choice() != self.snapshot.report_rate)

    def _slot_action(self, operation):
        if self.pending:
            return
        try:
            dirty = self._dirty(*self._draft())
        except ValueError:
            dirty = True
        if dirty:
            show_message(self.feedback, "Save your changes before switching or creating a profile.")
            return
        self.pending = self.owner.changing = True
        self.set_sensitive(False)
        def work():
            with self.owner.backend.device(self.mouse.id) as device:
                return operation(device)
        def finished(_, error):
            self.pending = self.owner.changing = False
            self.set_sensitive(True)
            self.owner._sensitivity()
            if error:
                show_message(self.feedback, error)
            else:
                self.close()
                self.owner._open_profile()
        self.owner._job(work, finished)

    def _switch(self, *_):
        slot = self.slots[self.slot_target.get_selected()].slot
        self._slot_action(lambda device: activate_profile(device, slot))

    def _create(self, *_):
        name = self.new_name.get_text().strip()
        if not name:
            show_message(self.feedback, "Enter a name for the new profile.")
            return
        if self.snapshot.first_save and not self.first_save_check.get_active():
            show_message(self.feedback, "Allow writable copies of the factory profiles first.")
            return
        slot = self.free[0].slot
        self._slot_action(lambda device: create_profile(device, self.snapshot, slot, name))

    def _save(self, *_):
        if self.pending or not self.snapshot.editable:
            return
        if self.first_save_check and not self.first_save_check.get_active():
            return
        try:
            steps, buttons = self._draft()
        except ValueError:
            self._changed()
            return
        self.pending = True
        self.owner.changing = True
        self.owner._sensitivity()
        self.save.set_sensitive(False)
        if self.first_save_check:
            self.first_save_check.set_sensitive(False)
        self.content.set_sensitive(False)
        self.cancel.set_sensitive(False)
        show_message(self.feedback, "Saving and checking the mouse’s profile…")
        name = self.name_entry.get_text()
        hz = self._rate_choice()
        extra = {attr: pick.get_selected() for attr, pick in self.defaults.items()
                 if pick.get_selected() != getattr(self.snapshot, attr)}
        if self.y_values and self.y_values != self.snapshot.dpi_steps_y:
            extra["dpi_steps_y"] = self.y_values

        def write():
            with self.owner.backend.device(self.mouse.id) as device:
                return save_profile(device, self.snapshot, dpi_steps=steps, buttons=buttons,
                                    name=name if name != self.snapshot.name else None,
                                    report_rate=hz if hz != self.snapshot.report_rate else None, **extra)

        def saved(snapshot, error):
            self.pending = False
            self.owner.changing = False
            self.owner._sensitivity()
            self.cancel.set_sensitive(True)
            if error:
                if self.first_save_check:
                    self.first_save_check.set_sensitive(True)
                self.content.set_sensitive(True)
                self._changed()
                show_message(self.feedback, error)
            else:
                show_message(self.owner.feedback, "Profile saved and read back from the mouse.")
                self.close()
            self.owner._poll()

        self.owner._job(write, saved)

    def _close(self, *_):
        if self.pending:
            return True
        self.owner.editor = None
        return False


class SessionEditor(Gtk.Window):
    """Live firmware controls for a mouse without an editable onboard profile."""

    def __init__(self, parent, mouse, controls, rate_data):
        super().__init__(title=f"{mouse.name} · Device settings", transient_for=parent,
                         modal=True, destroy_with_parent=True)
        self.owner = parent
        self.mouse = mouse
        self.pending = False
        self.add_css_class("mouse-control")
        self.set_default_size(540, 420)
        self.connect("close-request", self._close)
        self.content = column(14)
        margins(self.content, 20)
        self.set_child(scroll(self.content))
        self.content.append(label(mouse.name, "title"))
        rate, options, limitation = rate_data
        if limitation:
            self.content.append(label(limitation, "subtle", True))
        if rate or options:
            self.content.append(label("Polling rate", "section"))
            if options:
                self._choice("Report frequency", [(v, f"{v:,} Hz") for v in options], rate, set_report_rate)
            else:
                self.content.append(label(f"{rate:,} Hz · Read only"))
        if controls:
            self.content.append(label("Device-session button assignments", "section"))
            for control in controls:
                def assign(device, target, old, cid=control.cid):
                    set_control(device, cid, target, expected_target=old)
                self._choice(control.label, control.choices, control.target, assign,
                             control_mapping=True, enabled=not control.diverted)
        if not controls and not options:
            self.content.append(label("The device did not offer editable button assignments or polling rates on this connection.", "subtle", True))
        self.feedback = message_area()
        self.content.append(self.feedback)
        close = Gtk.Button(label="Close", halign=Gtk.Align.END)
        close.connect("clicked", lambda *_: self.close())
        self.content.append(close)

    def _choice(self, title, choices, current, operation, control_mapping=False, enabled=True):
        self.content.append(label(title))
        line = row()
        values = [x[0] for x in choices]
        dropdown = Gtk.DropDown.new_from_strings([x[1] for x in choices])
        dropdown.set_selected(values.index(current) if current in values else Gtk.INVALID_LIST_POSITION)
        dropdown.set_hexpand(True)
        line.append(dropdown)
        apply = Gtk.Button(label="Apply")
        line.append(apply)
        line.set_sensitive(enabled)
        self.content.append(line)
        verified = [current]

        def apply_change(_):
            idx = dropdown.get_selected()
            if self.pending or idx >= len(values):
                return
            target = values[idx]
            self.pending = self.owner.changing = True
            self.content.set_sensitive(False)

            def perform():
                with self.owner.backend.device(self.mouse.id) as device:
                    if control_mapping:
                        operation(device, target, verified[0])
                    else:
                        operation(device, target)

            def finished(_, error):
                self.pending = self.owner.changing = False
                self.content.set_sensitive(True)
                self.owner._sensitivity()
                if not error:
                    verified[0] = target
                show_message(self.feedback, error or "Change verified on the mouse.")

            self.owner._job(perform, finished)

        apply.connect("clicked", apply_change)

    def _close(self, *_):
        if self.pending:
            return True
        self.owner.editor = None
        return False


class MouseApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.mousecontrol.MouseControl")
        self.icon_directory = None

    def do_activate(self):
        window = self.get_active_window()
        if window is None:
            Adw.init()
            css = Gtk.CssProvider()
            with as_file(files("mouse_control").joinpath("style.css")) as css_path:
                css.load_from_path(str(css_path))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            icon_name = self.get_application_id()
            icons = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            if not icons.has_icon(icon_name):
                self.icon_directory = tempfile.TemporaryDirectory(prefix="mouse-control-icons-")
                resource = files("mouse_control").joinpath(f"{icon_name}.svg")
                (Path(self.icon_directory.name) / resource.name).write_bytes(resource.read_bytes())
                icons.add_search_path(self.icon_directory.name)
            Gtk.Window.set_default_icon_name(icon_name)
            window = MainWindow(self, MouseBackend())
        window.present()
