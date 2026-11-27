"""Exercise live updates, device selection and profile controls on an isolated display."""

import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import nullcontext


@unittest.skipUnless(os.environ.get("MOUSE_CONTROL_GUI_TEST") == "1", "requires an isolated GTK display")
class AppTests(unittest.TestCase):
    def setUp(self):
        from mouse_control import app
        from mouse_control.hardware import MouseInfo, MouseState
        self.app, self.state_type = app, MouseState
        app.Adw.init()
        self.g305 = MouseInfo("g305@usb1", "Logitech G305", "G305")
        self.g502 = MouseInfo("g502@usb2", "Logitech G502 HERO", "G502 HERO")
        self.devices = [self.g305, self.g502]
        self.values = {self.g305.id: 800, self.g502.id: 1600}
        self.writes = []
        fake = SimpleNamespace(discover=lambda: list(self.devices), read=self.read,
                               set_dpi=self.set_dpi, close=lambda: None)
        self.prefs = {}
        self.p1 = patch.object(app, "read_preferences", return_value=self.prefs.copy())
        self.p2 = patch.object(app, "save_preferences", side_effect=self.prefs.update)
        self.p1.start()
        self.p2.start()
        self.addCleanup(self.p1.stop)
        self.addCleanup(self.p2.stop)
        self.window = app.MainWindow(None, fake)
        self.window.present()

    def tearDown(self):
        self.window.close()
        self.until(lambda: self.window.closed)

    def read(self, mouse_id):
        device = next(m for m in self.devices if m.id == mouse_id)
        stages = (400, 800, 1600)
        dpi = self.values[mouse_id]
        return self.state_type(device, dpi, stages, stages, stages.index(dpi), 1, True)

    def set_dpi(self, mouse_id, dpi):
        self.writes.append((mouse_id, dpi))
        self.values[mouse_id] = dpi
        return self.read(mouse_id)

    def until(self, predicate):
        loop = self.app.GLib.MainContext.default()
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            while loop.iteration(False):
                pass
            time.sleep(.005)
        self.assertTrue(predicate())

    def test_live_changes_selection_and_unplug(self):
        win = self.window
        self.until(lambda: win.metric.get_text() == "800")
        self.values[self.g305.id] = 1600  # hardware button, independent of the application
        self.until(lambda: win.metric.get_text() == "1,600")
        self.assertEqual(win.target.get_selected(), 2)
        self.assertEqual(self.writes, [])
        win.selector.set_selected(1)
        self.until(lambda: win.state and win.state.mouse.id == self.g502.id)
        win.target.set_selected(0)
        win.apply.emit("clicked")
        self.until(lambda: win.metric.get_text() == "400")
        self.assertEqual(self.writes, [(self.g502.id, 400)])
        self.devices.clear()
        win._refresh()
        self.until(lambda: win.state is None and not win.mice)
        self.assertEqual(win.metric.get_text(), "—")
        self.assertFalse(win.apply.get_sensitive())

    def test_factory_editor_requires_changes_and_explicit_initialization(self):
        snapshot = SimpleNamespace(dpi_steps=(400, 800, 1600, 0, 0), buttons=("primary", "secondary"),
                                   button_labels=("Left", "Right"), actions=("primary", "secondary"),
                                   editable=True, first_save=True, slot=1, name="Work", report_rate=1000, report_rates=(125, 500, 1000))
        editor = self.app.ProfileEditor(self.window, self.g305, snapshot)
        self.window.editor = editor
        editor.present()
        self.assertFalse(editor.save.get_sensitive())
        editor.entries[2].set_text("2000")
        self.assertFalse(editor.save.get_sensitive())
        editor.first_save_check.set_active(True)
        self.assertTrue(editor.save.get_sensitive())
        editor.close()
        self.assertEqual(self.writes, [])

    def test_onboard_step_follows_button_even_when_sensor_reports_old_value(self):
        win = self.window
        self.until(lambda: win.metric.get_text() == "800")
        def conflicted(mid):
            return self.state_type(self.g305, 800, (400, 800, 1600), (400, 800, 1600), 2, 1, True)
        win.backend.read = conflicted
        self.until(lambda: win.metric.get_text() == "1,600")
        self.assertEqual(win.metric_title.get_text(), "Active onboard DPI step")
        self.assertIn("Sensor reports 800 DPI", win.mode.get_text())
        self.assertEqual(win.target.get_selected(), 2)
        self.assertEqual(self.writes, [])

    def test_live_press_is_highlighted_and_release_clears_mesh(self):
        from mouse_control.button_monitor import ButtonState
        owner = self.window
        self.until(lambda: owner.state is not None)
        held = {1}
        owner.backend.enable_button_monitor = lambda mid: None
        owner.backend.disable_button_monitor = lambda mid: None
        owner.backend.buttons = lambda mid: ButtonState(tuple(held), (), (), (), "physical", "Listening")
        view = owner.inspector
        view.toggle.set_active(True)
        self.until(lambda: view.view.pressed == 2)
        self.assertIn("Physical", view.events.get_text())
        held.clear()
        self.until(lambda: view.view.pressed == 0)
        view.toggle.set_active(False)
        self.assertEqual(self.writes, [])

    def test_rate_and_name_save_uses_selected_mouse_and_exact_draft(self):
        owner = self.window
        self.until(lambda: owner.state is not None)
        owner.backend.device = lambda mid: nullcontext(mid)
        snapshot = SimpleNamespace(dpi_steps=(400, 800, 1600, 0, 0), buttons=("primary",),
                                   button_labels=("Left",), actions=("primary",), editable=True,
                                   first_save=False, slot=1, name="Work", report_rate=1000, report_rates=(125,500,1000))
        editor = self.app.ProfileEditor(owner, self.g305, snapshot)
        owner.editor = editor
        editor.present()
        with patch.object(self.app, "save_profile", return_value=snapshot) as save:
            editor.rate.set_selected(1)
            editor.name_entry.set_text("Gaming")
            editor.save.emit("clicked")
            self.until(lambda: owner.editor is None)
            save.assert_called_once_with(self.g305.id, snapshot, dpi_steps=snapshot.dpi_steps, buttons={},
                                         name="Gaming", report_rate=500)

    def test_catalog_switches_mesh_without_touching_a_device(self):
        from mouse_control.model_browser import ModelBrowser
        browser = ModelBrowser(self.window)
        browser.present()
        dropdown = browser.get_child().get_first_child().get_next_sibling()
        dropdown.set_selected(browser.models.index("MX Vertical"))
        self.assertEqual(browser.view.kind, "vertical")
        dropdown.set_selected(browser.models.index("G502 HERO"))
        self.assertNotEqual(browser.view.kind, "generic")
        browser.close()
        self.assertEqual(self.writes, [])

    def test_single_slot_mouse_can_load_local_profile_without_writing(self):
        from tempfile import TemporaryDirectory
        snapshot = SimpleNamespace(dpi_steps=(400, 800, 1600, 0, 0), buttons=("primary",),
                                   button_labels=("Left",), actions=("primary", "middle"), editable=True,
                                   first_save=False, slot=1, name="", report_rate=500, report_rates=(125, 500))
        with TemporaryDirectory() as config, patch.dict(os.environ, {"XDG_CONFIG_HOME": config}):
            editor = self.app.ProfileEditor(self.window, self.g305, snapshot)
            self.window.editor = editor
            editor.entries[0].set_text("600")
            editor.mappings[0][2].set_selected(1)
            editor.draft_name.set_text("Editing")
            editor._store_local()
            self.assertEqual(editor.saved_names, ["Editing"])
            editor.entries[0].set_text("400")
            editor.mappings[0][2].set_selected(0)
            editor._load_local()
            self.assertEqual(editor.entries[0].get_text(), "600")
            self.assertEqual(editor.mappings[0][2].get_selected(), 1)
            self.assertTrue(editor.save.get_sensitive())
            self.assertEqual(self.writes, [])
            editor.destroy()

    def test_theme_and_window_preferences_round_trip(self):
        win = self.window
        theme = self.app.Gtk.DropDown.new_from_strings(list(self.app.THEMES))
        theme.set_selected(2)
        win._theme(theme)
        win.set_default_size(850, 580)
        win.unmaximize()
        win.close()
        self.until(lambda: win.closed)
        self.assertEqual(self.prefs["theme"], "Dark")
        self.assertEqual(self.prefs["width"], 850)
        self.assertEqual(self.prefs["height"], 580)
        self.p1.stop()
        with patch.object(self.app, "read_preferences", return_value=self.prefs.copy()):
            self.window = self.app.MainWindow(None, win.backend)
        self.window.present()
        self.assertEqual(self.window.get_default_size(), (850, 580))
        self.assertEqual(self.app.Adw.StyleManager.get_default().get_color_scheme(), self.app.Adw.ColorScheme.FORCE_DARK)
