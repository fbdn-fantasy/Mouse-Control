"""Preview catalog models without sending requests to a mouse."""

from gi.repository import Gtk

from .catalog import CATALOG
from .mouse_view import MouseView


class ModelBrowser(Gtk.Window):
    def __init__(self, parent):
        super().__init__(title="Mouse catalog", transient_for=parent, destroy_with_parent=True)
        self.set_default_size(590, 500)
        self.add_css_class("mouse-control")
        self.models = [name for group in CATALOG.values() for name in group]
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for side in ("start", "end", "top", "bottom"):
            getattr(content, "set_margin_" + side)(18)
        self.set_child(content)
        title = Gtk.Label(label="Mouse catalog", xalign=0)
        title.add_css_class("title")
        content.append(title)
        choose = Gtk.DropDown.new_from_strings(self.models)
        choose.set_enable_search(True)
        choose.set_tooltip_text("Search the supported model catalog")
        content.append(choose)
        self.view = MouseView(self.models[0], on_select=self._selected_button)
        self.view.set_size_request(300, 260)
        self.view.set_vexpand(True)
        content.append(self.view)
        self.info = Gtk.Label(xalign=0, wrap=True)
        content.append(self.info)
        self.selection = Gtk.Label(xalign=0, wrap=True)
        content.append(self.selection)
        choose.connect("notify::selected", self._model)
        if parent.state:
            name = parent.state.mouse.name.removeprefix("Logitech ")
            if name in self.models:
                choose.set_selected(self.models.index(name))
        self._model(choose)

    def _model(self, dropdown, *_):
        index = dropdown.get_selected()
        if index >= len(self.models):
            return
        name = self.models[index]
        self.view.set_model(name)
        family = "Representative button diagram" if self.view.kind == "generic" else "Model-family diagram"
        self.info.set_text(f"{family} · Drag to rotate. Connect the mouse to check its available controls.")
        self.selection.set_text("")

    def _selected_button(self, index):
        self.selection.set_text(self.view.button_name(index))
