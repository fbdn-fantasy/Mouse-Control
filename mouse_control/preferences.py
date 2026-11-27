"""Local desktop preferences for the application's window and appearance."""

import json
import os
import tempfile
from pathlib import Path

THEMES = ("System", "Light", "Dark")


def read_preferences():
    try:
        data = json.loads(_file().read_text())
        if not isinstance(data, dict):
            return {}
        return data
    except (OSError, ValueError):
        return {}


def _file():
    root = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(root) / "mouse-control" / "window.json"


def save_preferences(data):
    path = _file()
    temp = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            json.dump(data, stream)
        temp.replace(path)
    except OSError:
        if temp:
            temp.unlink(missing_ok=True)
