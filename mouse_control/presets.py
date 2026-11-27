"""Named local drafts of a mouse's editable onboard settings."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def _path(model):
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    key = hashlib.sha256(model.encode()).hexdigest()[:24]
    return base / "mouse-control" / "profiles" / f"{key}.json"


def read_presets(model):
    path = _path(model)
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get("model") != model or not isinstance(data.get("profiles"), dict):
        raise ValueError("The saved profile file has an unexpected format.")
    return data["profiles"]


def save_preset(model, name, draft):
    name = name.strip()
    if not name or len(name) > 64 or not name.isprintable():
        raise ValueError("Enter a profile name of 1 to 64 printable characters.")
    path = _path(model)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        profiles = read_presets(model)
        if name in profiles:
            raise ValueError("That profile name already exists. Choose another name.")
        profiles[name] = draft
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"model": model, "profiles": profiles}, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return profiles
