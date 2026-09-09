"""Read-only installed-package indexes for Pacman, APK, and Snap."""

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from housekeeper.i18n import _
from housekeeper.models import Source
from housekeeper.sorting import package_timestamp

LOG = logging.getLogger(__name__)
PACKAGE_SOURCES = {Source.PACMAN, Source.APK, Source.SNAP}
APK_DATABASE = Path("/lib/apk/db/installed")


@dataclass(frozen=True)
class InstalledPackage:
    source: Source
    name: str
    version: str
    arch: str
    database: str
    size: int | None
    desktops: tuple[str, ...]
    revision: str = ""
    updated_at: int | None = None


def byte_size(value):
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    return value if type(value) is int and 0 <= value < 2**64 else None


def desktop_path(value, root=Path("/")):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or not value.endswith(".desktop"):
        return None
    return str(root / value)


def sections(path):
    result, current = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("%") and line.endswith("%"):
            if line in result:
                raise ValueError("Duplicate package database field")
            current = result.setdefault(line, [])
        elif line and current is not None:
            current.append(line)
    return result


def pacman_paths():
    command = shutil.which("pacman-conf")
    if command is None:
        return Path("/var/lib/pacman/local"), Path("/")
    values = []
    for field in ("DBPath", "RootDir"):
        result = subprocess.run(
            [command, field],
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
            env={**os.environ, "LC_ALL": "C"},
        )
        lines = result.stdout.splitlines()
        if len(lines) != 1 or not Path(lines[0]).is_absolute():
            raise ValueError("Could not identify the Pacman installation")
        values.append(Path(lines[0]))
    return values[0] / "local", values[1]


def pacman_packages():
    database, root = pacman_paths()
    if not database.exists():
        return []
    result = []
    for directory in database.iterdir():
        if not directory.is_dir():
            continue
        try:
            fields = sections(directory / "desc")
            files = sections(directory / "files").get("%FILES%", [])
            name, version, arch = (fields[key] for key in ("%NAME%", "%VERSION%", "%ARCH%"))
            if any(len(value) != 1 for value in (name, version, arch)):
                continue
            size = fields.get("%SIZE%", [])
            installed = fields.get("%INSTALLDATE%", [])
            result.append(
                InstalledPackage(
                    Source.PACMAN,
                    name[0],
                    version[0],
                    arch[0],
                    str(database),
                    byte_size(size[0]) if len(size) == 1 else None,
                    tuple(path for file in files if (path := desktop_path(file, root))),
                    updated_at=package_timestamp(installed[0]) if len(installed) == 1 else None,
                )
            )
        except (OSError, ValueError, KeyError):
            LOG.debug("Unreadable Pacman package record: %s", directory, exc_info=True)
    return result


def apk_packages():
    if not APK_DATABASE.exists():
        return []
    result = []
    for block in APK_DATABASE.read_text(encoding="utf-8").split("\n\n"):
        fields, desktops, directory = {}, [], None
        malformed = False
        for line in block.splitlines():
            if len(line) < 2 or line[1] != ":":
                malformed = True
                continue
            key, value = line[0], line[2:]
            if key == "F":
                directory = value
            elif key == "R" and directory is not None:
                if "/" not in value and (path := desktop_path(directory + "/" + value)):
                    desktops.append(path)
            elif key in {"P", "V", "A", "I", "f"}:
                if key in fields:
                    malformed = True
                fields[key] = value
        if malformed or fields.get("f") or not all(fields.get(key) for key in ("P", "V", "A")):
            continue
        result.append(
            InstalledPackage(
                Source.APK,
                fields["P"],
                fields["V"],
                fields["A"],
                str(APK_DATABASE),
                byte_size(fields.get("I")),
                tuple(desktops),
            )
        )
    return result


def load_packages(source):
    if source == Source.PACMAN:
        return pacman_packages()
    if source == Source.APK:
        return apk_packages()
    if source == Source.SNAP:
        from housekeeper.providers.snap import snap_packages

        return snap_packages()
    return []


class PackageIndex:
    roots = (Path("/var/lib/snapd/desktop/applications"),)
    contexts = ("/var/lib/pacman/local", "/lib/apk/db", "snapd")

    def __init__(self):
        self.owners = None
        self.warnings = []
        self.attribution_errors = []

    def _load(self):
        self.owners = {}
        for source in sorted(PACKAGE_SOURCES, key=lambda source: source.value):
            try:
                for package in load_packages(source):
                    for desktop in set(package.desktops):
                        self.owners.setdefault(desktop, []).append(package)
            except Exception as error:
                LOG.debug("Package inventory unavailable: %s", source.value, exc_info=True)
                self.warnings.append(f"Could not read {source.value} packages: {error}")
                self.attribution_errors.append(self.warnings[-1])

    def candidates(self, entry):
        from housekeeper.appearance import verified_icon_source
        from housekeeper.attribution import candidate

        if self.owners is None:
            self._load()
        path = str(verified_icon_source(entry.path))
        return tuple(
            candidate(
                package.source,
                package.database,
                package.name + ":" + package.arch,
                package.version,
                package.name + (":" + package.arch if package.arch else ""),
                path,
                metadata={
                    "name": package.name,
                    "arch": package.arch,
                    "package_database": package.database,
                    "package_revision": package.revision,
                    "package_desktop": path,
                },
                size=package.size,
                updated_at=package.updated_at,
                reason=_("Manage this package using its system package manager."),
            )
            for package in self.owners.get(path, [])
        )


def installed_size(app):
    matches = [
        package
        for package in load_packages(app.source)
        if package.name == app.metadata.get("name")
        and package.arch == app.metadata.get("arch")
        and package.version == app.version
        and package.database == app.metadata.get("package_database")
        and package.revision == app.metadata.get("package_revision")
        and app.metadata.get("package_desktop") in package.desktops
    ]
    return matches[0].size if len(matches) == 1 else None
