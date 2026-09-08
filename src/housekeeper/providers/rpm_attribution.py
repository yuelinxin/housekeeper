"""Verify installed launcher contents and RPM relationships without launching commands."""

import configparser
import hashlib
import logging
import os
import re
import stat
from pathlib import Path

from housekeeper.appearance import verified_icon_source
from housekeeper.discovery import read_entry
from housekeeper.identity import unwrap_env

FIELD_CODES = {"%u", "%U", "%f", "%F", "%i", "%c", "%k"}
INTERPRETERS = {"sh", "bash", "dash", "zsh", "fish", "node", "java", "perl", "ruby", "gjs"}
HELPERS = {"env", "wine", "wine64", "flatpak", "snap", "gtk-launch", "gio", "xdg-open", "electron"}
HASHES = {1: "md5", 2: "sha1", 8: "sha256", 9: "sha384", 10: "sha512", 11: "sha224"}


def package_key(header):
    return header["name"], header["arch"]


def service_roots():
    roots = []
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if runtime and Path(runtime).is_absolute():
        roots.append(Path(runtime) / "dbus-1/services")
    data_home = os.environ.get("XDG_DATA_HOME", "")
    home = Path(data_home) if Path(data_home).is_absolute() else Path.home() / ".local/share"
    roots.append(home / "dbus-1/services")
    roots.extend(
        Path(path) / "dbus-1/services"
        for path in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
        if path and Path(path).is_absolute()
    )
    roots.append(Path("/usr/share/dbus-1/services"))
    return tuple(dict.fromkeys(roots))


class RpmAttribution:
    def __init__(self, index):
        self.index = index
        self.files = {}

    def header(self, path, package):
        matches = [h for h in self.index.headers(path) if package_key(h) == package_key(package)]
        return matches[0] if len(matches) == 1 else None

    def verified_file(self, path, package, seen=()):
        path = Path(path)
        if not path.is_absolute() or path in seen or len(seen) >= 16:
            return False
        header = self.header(path, package)
        if header is None:
            return False
        index = list(header["filenames"]).index(str(path))
        expected_mode = header["filemodes"][index]
        info = path.lstat()
        if stat.S_IFMT(info.st_mode) != stat.S_IFMT(expected_mode):
            return False
        if stat.S_ISLNK(expected_mode):
            link = os.readlink(path)
            if link != header["filelinktos"][index]:
                return False
            target = Path(os.path.normpath(path.parent / link))
            return self.verified_file(target, package, (*seen, path))
        if not stat.S_ISREG(expected_mode):
            return False
        algorithm = HASHES.get(header["filedigestalgo"])
        expected = header["filedigests"][index]
        if not algorithm or not expected:
            return False
        stamp = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        key = (path, stamp, algorithm, expected)
        if key not in self.files:
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                before = os.fstat(stream.fileno())
                checksum = hashlib.new(algorithm)
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum.update(chunk)
                after = os.fstat(stream.fileno())

            def unchanged(value):
                return (
                    value.st_dev,
                    value.st_ino,
                    value.st_size,
                    value.st_mtime_ns,
                    value.st_ctime_ns,
                ) == stamp

            self.files[key] = (
                unchanged(before) and unchanged(after) and checksum.hexdigest() == expected
            )
        return self.files[key]

    @staticmethod
    def depends_on(application, command):
        import rpm

        requirements = zip(
            application["requirename"],
            application["requireflags"],
            application["requireversion"],
            strict=True,
        )
        provides = list(
            zip(
                command["providename"],
                command["provideflags"],
                command["provideversion"],
                strict=True,
            )
        )
        return any(
            rpm.ds(required, "requires").Compare(rpm.ds(provided, "provides"))
            for required in requirements
            for provided in provides
            if required[0] == provided[0] and not required[0].startswith("(")
        )

    def command_verified(self, executable, argv, application):
        if not executable or not os.access(executable, os.X_OK):
            return False
        owners = self.index.headers(executable)
        valid = [owner for owner in owners if self.verified_file(executable, owner)]
        if not valid:
            return False
        binary = Path(executable).name
        if binary in HELPERS:
            return False
        if binary in INTERPRETERS or binary.startswith(("python", "gjs")):
            # A dependency on an interpreter alone cannot identify the script it runs.
            return (
                len(argv) >= 2
                and self.verified_file(argv[1], application)
                and any(self.depends_on(application, owner) for owner in valid)
            )
        matching = [
            owner
            for owner in valid
            if package_key(owner) == package_key(application) or self.depends_on(application, owner)
        ]
        return len({package_key(owner) for owner in matching}) == 1

    def dbus_verified(self, entry, argv, application):
        from gi.repository import GLib

        bus_name = entry.desktop_id.removesuffix(".desktop")
        if not re.fullmatch(r"[A-Za-z_-][A-Za-z0-9_-]*(?:\.[A-Za-z_-][A-Za-z0-9_-]*)+", bus_name):
            return False
        helper = Path(entry.executable).name == "gapplication"
        if helper:
            if (
                len(argv) < 3
                or argv[1:3] != ("launch", bus_name)
                or any(arg not in FIELD_CODES for arg in argv[3:])
            ):
                return False
        elif not self.command_verified(entry.executable, argv, application):
            return False
        # The helper is not the app. Match the effective session service to an
        # unchanged service file and entry point owned by the application package.
        if not any(
            (not helper or owner["name"] == "glib2") and self.verified_file(entry.executable, owner)
            for owner in self.index.headers(entry.executable)
        ):
            return False
        for root in service_roots():
            matches = []
            for path in sorted(root.glob("*.service")):
                parser = configparser.ConfigParser(interpolation=None)
                parser.optionxform = str
                try:
                    parser.read_string(path.read_text())
                    service = parser["D-BUS Service"]
                    if service.get("Name") == bus_name or path.name == bus_name + ".service":
                        matches.append((path, service))
                except (configparser.Error, KeyError, UnicodeError):
                    if path.name == bus_name + ".service":
                        return False
            if not matches:
                continue
            if len(matches) != 1:
                return False
            path, service = matches[0]
            if (
                service.get("Name") != bus_name
                or service.get("SystemdService")
                or not any(
                    self.verified_file(path, owner)
                    and (
                        package_key(owner) == package_key(application)
                        or self.depends_on(application, owner)
                    )
                    for owner in self.index.headers(path)
                )
            ):
                return False
            command = tuple(GLib.shell_parse_argv(service.get("Exec", ""))[1])
            return bool(
                command
                and Path(command[0]).is_absolute()
                and os.access(command[0], os.X_OK)
                and self.verified_file(command[0], application)
            )
        return False

    def verify(self, entry, package):
        try:
            current = read_entry(entry.path, entry.root)
            if (
                current.argv != entry.argv
                or current.executable != entry.executable
                or current.resolved_executable != entry.resolved_executable
                or current.dbus_activatable != entry.dbus_activatable
            ):
                return False
            path = verified_icon_source(entry.path)
            application = self.header(path, package)
            if application is None or not self.verified_file(path, application):
                return False
            argv = unwrap_env(entry.argv)
            if not argv:
                return False
            if Path(entry.executable).name == "gapplication" or entry.dbus_activatable:
                return self.dbus_verified(entry, argv, application)
            return self.command_verified(entry.executable, argv, application)
        except Exception:
            logging.getLogger(__name__).debug(
                "RPM launcher verification unavailable", exc_info=True
            )
            return False
