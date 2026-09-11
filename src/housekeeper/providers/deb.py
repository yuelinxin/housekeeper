"""Read-only DEB attribution and installed sizes from the local dpkg database."""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from housekeeper.i18n import _
from housekeeper.models import Source

LOG = logging.getLogger(__name__)
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
    if result.returncode == 1 and (
        "no path found matching pattern" in result.stderr
        or "no packages found matching" in result.stderr
    ):
        return ""  # Only explicit negative answers establish absence.
    result.check_returncode()
    return result.stdout


def file_owners(path):
    # dpkg-query uses glob patterns even for absolute paths. Quote literal metacharacters.
    pattern = "".join(
        {"*": "[*]", "?": "[?]", "[": "[[]", "]": "[]]", "\\": "\\\\"}.get(c, c) for c in str(path)
    )
    output = query("--search", "--", pattern)
    owners = set()
    for line in output.splitlines():
        names, separator, filename = line.partition(": ")
        if not separator:
            raise ValueError("Malformed dpkg ownership response")
        if filename != str(path):
            continue
        if names.startswith(("diversion ", "local diversion ")):
            raise ValueError("The file is diverted; ownership cannot be verified")
        values = names.split(", ")
        if not all(_PACKAGE.fullmatch(name) for name in values):
            raise ValueError("Invalid dpkg file owner")
        owners.update("deb:" + name for name in values)
    return tuple(sorted(owners))


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
    roots = ()
    contexts = ("/:/var/lib/dpkg",)

    def __init__(self):
        self._owners = None
        self._packages = []
        self.attribution_errors = []

    def _load(self):
        # Two queries per inventory, rather than starting dpkg-query for every app.
        # One attempt per index: a failure is reported for every launcher of this
        # scan and retried by the fresh index the next scan builds, never per entry.
        self._packages, self._owners = [], {}
        if Path("/var/lib/dpkg/status").exists() and not shutil.which("dpkg-query"):
            self.attribution_errors.append(
                "The dpkg database exists but dpkg-query is unavailable."
            )
            return
        paths: dict[str, set[str]] = {}
        try:
            package_rows = packages()
            diverted = set()
            for line in query("--search", "*.desktop").splitlines() if package_rows else ():
                owners, separator, path = line.partition(": ")
                if not separator or not path.startswith("/"):
                    continue
                if owners.startswith("diversion ") or owners.startswith("local diversion "):
                    diverted.add(path)
                    continue
                names = owners.split(", ")
                if all(_PACKAGE.fullmatch(name) for name in names):
                    paths.setdefault(path, set()).update(names)
            for path in diverted:
                paths.pop(path, None)
        except Exception as error:
            LOG.debug("The dpkg database could not be read", exc_info=True)
            self.attribution_errors.append(f"Could not read dpkg packages: {error}")
            return
        self._packages, self._owners = package_rows, paths

    def candidates(self, entry):
        from housekeeper.appearance import verified_icon_source
        from housekeeper.attribution import candidate

        if self._owners is None:
            self._load()
        path = str(verified_icon_source(entry.path))
        owners = self._owners.get(path, set())
        matches = [
            package
            for package in self._packages
            if owners.intersection({package["name"], package["name"] + ":" + package["arch"]})
        ]
        known = {
            value
            for package in matches
            for value in (package["name"], package["name"] + ":" + package["arch"])
        }
        matches.extend(
            {"name": name, "arch": "unknown", "version": "", "installed_size": ""}
            for name in sorted(owners - known)
        )
        return tuple(
            candidate(
                Source.DEB,
                "/:/var/lib/dpkg",
                package["name"] + ":" + package["arch"],
                package["version"],
                package["name"] + ":" + package["arch"],
                path,
                metadata=package,
                size=package_size(package),
                reason=_("Manage this DEB package using your system package manager."),
            )
            for package in matches
        )


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
    return package_size(matches[0])


def package_size(package):
    size = package["installed_size"]
    # Debian Installed-Size is an estimate in KiB, not the archive's download size.
    return int(size) * 1024 if re.fullmatch(r"[0-9]+", size) else None
