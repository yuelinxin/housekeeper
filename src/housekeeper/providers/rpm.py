"""Read RPM ownership locally; delegate changes to PackageKit."""

import logging
import os
from pathlib import Path

from housekeeper.identity import digest, unwrap_env
from housekeeper.models import Action, ProviderCapabilities, Source


def host_support(os_release=None, markers=None):
    if os_release is None:
        os_release = {}
        try:
            for line in Path("/etc/os-release").read_text().splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    os_release[key] = value.strip('"')
        except OSError:
            pass
    if markers is None:
        markers = any(
            Path(p).exists()
            for p in (
                "/run/ostree-booted",
                "/sysroot/ostree",
                "/run/bootc",
                "/nix/var/nix/profiles/system",
            )
        )
    if markers:
        return False, "System packages are managed by this immutable or declarative system."
    if os_release.get("ID") != "fedora" or os_release.get("VERSION_ID") not in {"43", "44"}:
        return False, "Direct RPM removal is currently supported on Fedora 43 and 44."
    return True, ""


class RpmIndex:
    def __init__(self):
        self.ts = None
        self.error = ""
        self._cache = {}
        try:
            import rpm

            self.ts = rpm.TransactionSet()
            self.ts.openDB()
        except (ImportError, OSError, RuntimeError) as error:
            self.error = str(error)

    @property
    def available(self):
        return self.ts is not None

    def owners(self, path):
        if self.ts is None:
            raise RuntimeError("RPM ownership information is unavailable.")
        path = str(path)
        if path not in self._cache:
            packages = []
            for header in self.ts.dbMatch("basenames", path):
                if path not in header["filenames"]:
                    continue
                epoch = str(header["epoch"] or 0)
                version = f"{header['version']}-{header['release']}"
                if epoch != "0":
                    version = epoch + ":" + version
                packages.append(
                    {"name": header["name"], "version": version, "arch": header["arch"]}
                )
            self._cache[path] = packages
        return self._cache[path]

    def enrich(self, app):
        if app.source != Source.OTHER or not self.available:
            return
        entry = app.entries[0]
        argv = unwrap_env(entry.argv)
        entry_owners = self.owners(entry.path)
        if not entry_owners:
            binary = Path(entry.executable).name
            hosts = {
                "sh",
                "bash",
                "dash",
                "zsh",
                "fish",
                "env",
                "node",
                "java",
                "perl",
                "ruby",
                "wine",
                "wine64",
                "flatpak",
                "snap",
                "gtk-launch",
                "gio",
                "xdg-open",
                "electron",
                "appimagelauncher",
            }
            if binary in hosts or binary.startswith("python"):
                return
            # Without package-owned desktop metadata, arguments may identify a guest app.
            if any(arg not in {"%u", "%U", "%f", "%F", "%i", "%c", "%k"} for arg in argv[1:]):
                return
        paths = [entry.path]
        if entry.executable:
            paths.extend([Path(entry.executable), Path(entry.resolved_executable)])
        owners = {tuple(sorted(package.items())) for path in paths for package in self.owners(path)}
        if len(owners) != 1:
            return
        package = dict(next(iter(owners)))
        app.source, app.provider = Source.RPM, "rpm"
        app.scope, app.version = "System", package["version"]
        app.identity = "{name}-{version}.{arch}".format(**package)
        app.metadata.update(package)
        launch = entry.argv or (entry.desktop_id,)
        app.key = digest("rpm", package["name"], package["arch"], launch)
        supported, reason = host_support()
        if package["name"] == "housekeeper":
            supported, reason = (
                False,
                "Manage Housekeeper itself using your system package manager.",
            )
        if supported and os.geteuid() != 0:
            app.action = Action.UNINSTALL
        elif reason:
            app.metadata["management_reason"] = reason


class RpmProvider:
    def __init__(self):
        self.cancel = None

    def capabilities(self):
        supported, reason = host_support()
        try:
            import gi

            gi.require_version("PackageKitGlib", "1.0")
            from gi.repository import PackageKitGlib  # noqa: F401
        except (ImportError, ValueError):
            supported, reason = False, "PackageKit integration is not installed."
        return ProviderCapabilities(True, supported, supported, reason)

    def _client(self):
        import gi

        gi.require_version("PackageKitGlib", "1.0")
        from gi.repository import Gio, GLib
        from gi.repository import PackageKitGlib as Pk

        from housekeeper.models import ManagementError

        capability = self.capabilities()
        if not capability.execute:
            raise ManagementError(capability.reason)
        if os.geteuid() == 0:
            raise ManagementError("Run Housekeeper as a regular desktop user.")
        proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SYSTEM,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.freedesktop.PackageKit",
            "/org/freedesktop/PackageKit",
            "org.freedesktop.PackageKit",
            None,
        )
        proxy.set_default_timeout(15000)
        roles = proxy.get_cached_property("Roles")
        if roles is None or not roles.unpack() & (1 << int(Pk.RoleEnum.REMOVE_PACKAGES)):
            raise ManagementError("The installed PackageKit backend cannot remove packages.")
        client = Pk.Client()
        client.set_interactive(True)
        return client, Pk, Gio, GLib

    @staticmethod
    def _check(result):
        from housekeeper.models import ManagementError

        error = result.get_error_code()
        if error:
            raise ManagementError(
                error.get_details() or "The package manager rejected this operation."
            )

    def prepare(self, app, inventory):
        from gi.repository import GLib

        from housekeeper.models import ManagementError

        try:
            return self._prepare(app, inventory)
        except GLib.Error as error:
            raise ManagementError(
                "PackageKit cannot safely preview this removal: " + error.message
            ) from error

    def _prepare(self, app, inventory):
        from housekeeper.models import ManagementError, RemovalPlan

        if app.metadata.get("name") == "housekeeper":
            raise ManagementError("Use your system package manager to remove Housekeeper.")
        client, pk, _gio, _glib = self._client()
        result = client.resolve(
            1 << int(pk.FilterEnum.INSTALLED), [app.metadata["name"]], None, lambda *_: None, None
        )
        self._check(result)
        matching = [
            p
            for p in result.get_package_array()
            if p.get_name() == app.metadata["name"]
            and p.get_arch() == app.metadata["arch"]
            and p.get_version().removeprefix("0:") == app.version.removeprefix("0:")
        ]
        if len(matching) != 1:
            logging.getLogger(__name__).debug(
                "Expected RPM %s %s %s; PackageKit returned %s",
                app.metadata["name"],
                app.version,
                app.metadata["arch"],
                [
                    (p.get_id(), p.get_name(), p.get_version(), p.get_arch())
                    for p in result.get_package_array()
                ],
            )
            raise ManagementError(
                "The installed package changed. Refresh the inventory and try again."
            )
        package_id = matching[0].get_id()
        preview = client.remove_packages(
            1 << int(pk.TransactionFlagEnum.SIMULATE),
            [package_id],
            False,
            False,
            None,
            lambda *_: None,
            None,
        )
        self._check(preview)
        # Backends must positively report the removal during simulation.
        packages = preview.get_package_array()
        affected_ids = {p.get_id() for p in packages}
        if (
            preview.get_exit_code() != pk.ExitEnum.SUCCESS
            or affected_ids != {package_id}
            or any(p.get_info() != pk.InfoEnum.REMOVING for p in packages)
        ):
            logging.getLogger(__name__).debug(
                "Removal preview for %s returned %s (exit %s)",
                package_id,
                [(p.get_id(), str(p.get_info())) for p in packages],
                str(preview.get_exit_code()),
            )
            raise ManagementError(
                "A safe, single-package removal preview is unavailable. "
                "Use your system package manager to review this operation."
            )
        names = sorted(
            {a.name for a in inventory if a.provider == "rpm" and a.identity == app.identity}
        )
        return RemovalPlan(
            app.key,
            "rpm",
            package_id,
            tuple(names),
            "This removes the software package and all application entries it provides. "
            "User data is kept. Additional packages will not be removed.",
            digest(package_id, sorted(affected_ids)),
        )

    def execute(self, app, plan, progress):
        from housekeeper.models import ManagementError, OperationResult, Outcome

        current = self.prepare(app, [app])
        if current.fingerprint != plan.fingerprint:
            raise ManagementError(
                "The removal plan changed. Review a new preview before continuing."
            )
        client, pk, gio, _glib = self._client()
        self.cancel = gio.Cancellable()

        def report(p, _kind, _data):
            value = p.get_percentage()
            fraction = value / 100 if 0 <= value <= 100 else None
            progress("Removing the system package…", fraction, p.get_allow_cancel())

        result = client.remove_packages(0, [plan.target], False, False, self.cancel, report, None)
        self._check(result)
        if result.get_exit_code() == pk.ExitEnum.CANCELLED:
            return OperationResult(Outcome.CANCELLED, "The package operation was cancelled.")
        if result.get_exit_code() != pk.ExitEnum.SUCCESS:
            return OperationResult(Outcome.FAILED, "The package manager did not report success.")
        return OperationResult(
            Outcome.SUCCESS, "The system package was uninstalled.", (plan.target,)
        )

    def request_cancel(self):
        if self.cancel:
            self.cancel.cancel()
