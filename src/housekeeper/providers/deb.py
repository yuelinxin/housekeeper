"""Read-only DEB attribution and installed sizes from the local dpkg database."""

import os
import re
import shutil
import subprocess

from housekeeper.i18n import _
from housekeeper.identity import digest
from housekeeper.models import Source

_FORMAT = "${Package}\t${Version}\t${Architecture}\t${db:Status-Status}\t${Installed-Size}\n"
_PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.-]+(?::[a-z0-9][a-z0-9-]*)?")


def query(*args):
    command = shutil.which("dpkg-query")
    if command is None:
        return ""
    result = subprocess.run(
        [command, *args],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode == 1:
        return ""  # No matching installed package or path.
    result.check_returncode()
    return result.stdout


def packages(target=None):
    args = ("--", target) if target is not None else ()
    output = query("--show", "--showformat=" + _FORMAT, *args)
    result = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 5:
            continue
        name, version, arch, status, size = fields
        if status != "installed" or not version or not _PACKAGE.fullmatch(name + ":" + arch):
            continue
        result.append({"name": name, "version": version, "arch": arch, "installed_size": size})
    return result


class DebIndex:
    def __init__(self):
        self._owners = None
        self._packages = []

    def _load(self):
        # Two queries per inventory, rather than starting dpkg-query for every app.
        self._owners = {}
        self._packages = packages()
        if not self._packages:
            return
        diverted = set()
        for line in query("--search", "*.desktop").splitlines():
            owners, separator, path = line.partition(": ")
            if not separator or not path.startswith("/"):
                continue
            if owners.startswith("diversion ") or owners.startswith("local diversion "):
                diverted.add(path)
                continue
            names = owners.split(", ")
            if all(_PACKAGE.fullmatch(name) for name in names):
                self._owners.setdefault(path, set()).update(names)
        for path in diverted:
            self._owners.pop(path, None)

    def enrich(self, app):
        if app.source != Source.OTHER or not app.entries:
            return
        from housekeeper.appearance import verified_icon_source

        if self._owners is None:
            self._load()
        entry = app.entries[0]
        owners = self._owners.get(str(verified_icon_source(entry.path)), set())
        if len(owners) != 1:
            return
        owner = next(iter(owners))
        matches = [
            package
            for package in self._packages
            if owner in {package["name"], package["name"] + ":" + package["arch"]}
        ]
        if len(matches) != 1:
            return
        package = matches[0]
        app.source, app.provider = Source.DEB, "deb"
        app.scope, app.version = "System", package["version"]
        app.identity = package["name"] + ":" + package["arch"]
        app.metadata.update(package)
        app.metadata["management_reason"] = _(
            "Manage this DEB package using your system package manager."
        )
        app.key = digest("deb", package["name"], package["arch"], entry.argv or (entry.desktop_id,))


def installed_size(app):
    target = app.metadata.get("name", "") + ":" + app.metadata.get("arch", "")
    if not _PACKAGE.fullmatch(target):
        return None
    matches = [
        package
        for package in packages(target)
        if package["name"] + ":" + package["arch"] == target and package["version"] == app.version
    ]
    if len(matches) != 1:
        return None
    size = matches[0]["installed_size"]
    # Debian Installed-Size is an estimate in KiB, not the archive's download size.
    return int(size) * 1024 if re.fullmatch(r"[0-9]+", size) else None
