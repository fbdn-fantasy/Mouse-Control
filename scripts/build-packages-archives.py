"""Build the reproducible Python executable and source distribution."""

import os
import stat
import sys
import time
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def write_entry(archive, name, data, mode=0o644):
    timestamp = int(os.environ["SOURCE_DATE_EPOCH"])
    # ZIP timestamps have two-second resolution and start in 1980.
    info = ZipInfo(name, time.gmtime(max(timestamp, 315532800))[:6])
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    archive.writestr(info, data, compress_type=ZIP_DEFLATED, compresslevel=9)


def source_files(root):
    for name in ("README.md", "COMPATIBILITY.md", ".gitignore"):
        if (root / name).is_file():
            yield root / name
    for directory in ("mouse_control", "packaging", "scripts", "tests"):
        for path in sorted((root / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".pyo"):
                yield path


def main():
    root, destination = (Path(arg) for arg in sys.argv[1:3])
    version = sys.argv[3]
    executable = destination / f"Mouse-Control-V{version}"
    source_name = f"mouse-control-{version}"
    sources = destination / f"{source_name}-source.zip"
    with ZipFile(executable, "a") as app, ZipFile(sources, "w") as source:
        write_entry(app, "__main__.py", b"from mouse_control.__main__ import main\nraise SystemExit(main())\n")
        for name in ("LICENSE", "NOTICE"):
            data = (root / name).read_bytes()
            write_entry(app, name, data)
            write_entry(source, f"{source_name}/{name}", data)
        for path in source_files(root):
            name = path.relative_to(root).as_posix()
            mode = 0o755 if os.access(path, os.X_OK) else 0o644
            data = path.read_bytes()
            write_entry(source, f"{source_name}/{name}", data, mode)
            if name.startswith("mouse_control/"):
                write_entry(app, name, data)
    executable.chmod(0o755)
    for path in (sources, executable):
        with ZipFile(path) as archive:
            bad_file = archive.testzip()
            if bad_file:
                raise ValueError(f"Checksum mismatch for {bad_file} in {path}")


if __name__ == "__main__":
    main()
