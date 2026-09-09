"""Read standard session activation files without starting a service."""

import configparser
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

BUS_NAME = re.compile(r"[A-Za-z_-][A-Za-z0-9_-]*(?:\.[A-Za-z_-][A-Za-z0-9_-]*)+")
MAX_SERVICE_SIZE = 1024 * 1024


class ServiceParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


@dataclass(frozen=True)
class ServiceFile:
    path: Path
    resolved: Path
    contents: bytes
    values: tuple[tuple[str, str], ...]


def service_roots() -> tuple[tuple[Path, bool], ...]:
    """The runtime directory alone requires exact bus-name filenames."""
    roots: dict[Path, bool] = {}
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if runtime and Path(runtime).is_absolute():
        roots[Path(runtime) / "dbus-1/services"] = True
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    if not Path(data_home).is_absolute():
        data_home = str(Path.home() / ".local/share")
    directories = [
        data_home,
        *(os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"),
        "/usr/share",
    ]
    for directory in directories:
        if directory and Path(directory).is_absolute():
            roots.setdefault(Path(directory) / "dbus-1/services", False)
    return tuple(roots.items())


def read_service(path: Path) -> ServiceFile:
    resolved = path.resolve(strict=True)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SERVICE_SIZE:
            raise ValueError("The D-Bus service is not a bounded regular file.")
        contents = stream.read(MAX_SERVICE_SIZE + 1)
    if len(contents) > MAX_SERVICE_SIZE or path.resolve(strict=True) != resolved:
        raise ValueError("The D-Bus service changed while being read.")
    parser = ServiceParser(interpolation=None)
    parser.read_string(contents.decode("utf-8"))
    if parser.defaults() or parser.sections() != ["D-BUS Service"]:
        raise ValueError("The D-Bus service format is unsupported.")
    return ServiceFile(path, resolved, contents, tuple(sorted(parser["D-BUS Service"].items())))


def effective_service(bus_name: str) -> ServiceFile | None:
    if len(bus_name) > 255 or not BUS_NAME.fullmatch(bus_name):
        return None
    for root, strict_names in service_roots():
        try:
            # Do not let an inaccessible directory silently become 'no override'.
            paths = sorted(p for p in root.iterdir() if p.name.endswith(".service"))
        except FileNotFoundError:
            continue
        matches = []
        for path in paths:
            exact = path.name == bus_name + ".service"
            if strict_names and not exact:
                continue
            try:
                service = read_service(path)
            except (configparser.Error, UnicodeError, ValueError):
                # Outside the runtime directory, even an alternate filename can
                # claim this bus name. Unsupported contents cannot prove absence.
                return None
            if dict(service.values).get("Name") == bus_name or exact:
                matches.append(service)
        if not matches:
            continue
        # Same-directory duplicate names have undefined selection; never guess.
        if len(matches) != 1 or dict(matches[0].values).get("Name") != bus_name:
            return None
        return matches[0]
    return None
