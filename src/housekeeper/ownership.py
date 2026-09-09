"""File ownership queries distinguish negative answers from unavailable databases."""

import os
import shutil
from pathlib import Path

from housekeeper.models import FileOwnershipResult as Result
from housekeeper.models import FileOwnershipState as State


def combine(results):
    owners = tuple(sorted({owner for result in results for owner in result.owners}))
    if owners:
        return Result(State.OWNED, owners)
    errors = [result.reason for result in results if result.state == State.ERROR]
    if errors:
        return Result(State.ERROR, reason="; ".join(sorted(set(errors))))
    if any(result.state == State.UNOWNED for result in results):
        return Result(State.UNOWNED)
    return Result(State.ERROR, reason="No supported file ownership database is available.")


class FileOwnershipIndex:
    """One query snapshot per scan/preview. Execution creates a fresh snapshot."""

    def __init__(self, queries=None):
        self.queries = (
            queries
            if queries is not None
            else {
                "rpm": self.rpm,
                "deb": self.deb,
                "pacman": self.pacman,
                "apk": self.apk,
                "flatpak": self.flatpak,
                "snap": self.snap,
            }
        )
        self.cache = {}
        self.databases = {}

    def query(self, path):
        path = Path(path)
        if not path.is_absolute():
            return Result(State.ERROR, reason="File ownership requires an absolute path.")
        if path not in self.cache:
            results = []
            for name, query in self.queries.items():
                try:
                    results.append(query(path))
                except Exception as error:
                    results.append(Result(State.ERROR, reason=f"{name}: {error}"))
            self.cache[path] = combine(results)
        return self.cache[path]

    def load(self, name, factory):
        if name not in self.databases:
            self.databases[name] = factory()
        return self.databases[name]

    def rpm(self, path):
        from housekeeper.providers.rpm import RpmIndex

        index = self.load("rpm", RpmIndex)
        if not index.available:
            if any(p.exists() for p in (Path("/usr/lib/sysimage/rpm"), Path("/var/lib/rpm"))):
                return Result(State.ERROR, reason="RPM ownership cannot be verified.")
            return Result(State.NOT_APPLICABLE)
        owners = tuple("rpm:" + p["name"] + ":" + p["arch"] for p in index.owners(path))
        return Result(State.OWNED if owners else State.UNOWNED, owners)

    def deb(self, path):
        from housekeeper.providers.deb import file_owners

        if not Path("/var/lib/dpkg/status").exists() and not shutil.which("dpkg-query"):
            return Result(State.NOT_APPLICABLE)
        if not shutil.which("dpkg-query"):
            return Result(
                State.ERROR, reason="The dpkg database exists but dpkg-query is unavailable."
            )
        owners = file_owners(path)
        return Result(State.OWNED if owners else State.UNOWNED, owners)

    def pacman(self, path):
        from housekeeper.providers.packages import pacman_packages, pacman_paths

        database, _ = self.load("pacman_context", pacman_paths)
        if not database.exists():
            return Result(State.NOT_APPLICABLE)
        packages = self.load("pacman", lambda: pacman_packages(strict=True))
        owners = tuple(
            "pacman:" + p.database + ":" + p.name for p in packages if str(path) in p.files
        )
        return Result(State.OWNED if owners else State.UNOWNED, owners)

    def apk(self, path):
        from housekeeper.providers.packages import APK_DATABASE, apk_packages

        if not APK_DATABASE.exists():
            return Result(State.NOT_APPLICABLE)
        packages = self.load("apk", lambda: apk_packages(strict=True))
        owners = tuple("apk:" + p.database + ":" + p.name for p in packages if str(path) in p.files)
        return Result(State.OWNED if owners else State.UNOWNED, owners)

    def flatpak(self, path):
        from housekeeper.providers.flatpak import FlatpakIndex

        index = self.load("flatpak", FlatpakIndex)
        if not index.available:
            known = (
                Path(
                    os.environ.get("FLATPAK_USER_DIR")
                    or str(
                        Path(os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share"))
                        / "flatpak"
                    )
                ),
                Path("/var/lib/flatpak"),
                Path("/etc/flatpak/installations.d"),
            )
            if any(root.exists() for root in known):
                return Result(
                    State.ERROR,
                    reason="Flatpak installations exist but their ownership backend is unavailable.",
                )
            return Result(State.NOT_APPLICABLE)
        if index.warnings:
            return Result(State.ERROR, reason="; ".join(index.warnings))
        roots = [Path(root) for root in index.installations if Path(root).exists()]
        owners = tuple(
            "flatpak:" + str(root)
            for root in roots
            if path.resolve().is_relative_to(root.resolve())
        )
        return Result(
            State.OWNED if owners else State.UNOWNED if roots else State.NOT_APPLICABLE, owners
        )

    def snap(self, path):
        from housekeeper.providers.snap import SNAP_SOCKET, snap_packages

        roots = [Path("/snap"), Path("/var/lib/snapd/snaps"), Path("/var/lib/snapd/snap")]
        owned = tuple(
            "snap:" + str(root)
            for root in roots
            if root.exists() and path.resolve().is_relative_to(root.resolve())
        )
        if owned:
            return Result(State.OWNED, owned)
        if not SNAP_SOCKET.exists():
            if Path("/var/lib/snapd/state.json").exists():
                return Result(
                    State.ERROR,
                    reason="Snap is installed but its ownership backend is unavailable.",
                )
            return Result(State.NOT_APPLICABLE)
        packages = self.load("snap", snap_packages)
        owners = tuple("snap:" + p.name for p in packages if str(path) in p.desktops)
        return Result(State.OWNED if owners else State.UNOWNED, owners)
