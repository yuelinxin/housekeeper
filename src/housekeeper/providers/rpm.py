"""Read RPM ownership locally; delegate changes to PackageKit."""

import logging
import os
from pathlib import Path

from housekeeper.i18n import _
from housekeeper.identity import digest, unwrap_env
from housekeeper.models import (
    Action,
    ManagementError,
    OperationCancelled,
    OperationResult,
    Outcome,
    ProviderCapabilities,
    Source,
    UpdateChange,
    UpdateCheckResult,
    UpdatePlan,
    UpdateState,
)
from housekeeper.providers.packages import byte_size
from housekeeper.sorting import package_timestamp


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
        return False, "Direct RPM management is currently supported on Fedora 43 and 44."
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
                package = {"name": header["name"], "version": version, "arch": header["arch"]}
                for tag in ("size", "installtime"):
                    try:
                        package[tag] = str(header[tag])
                    except (KeyError, ValueError, TypeError):
                        pass  # Optional metadata must not prevent package attribution.
                packages.append(package)
            self._cache[path] = packages
        return self._cache[path]

    def enrich(self, app):
        if app.source != Source.OTHER or not self.available:
            return
        entry = app.entries[0]
        from housekeeper.appearance import verified_icon_source

        entry_path = verified_icon_source(entry.path)
        argv = unwrap_env(entry.argv)
        entry_owners = self.owners(entry_path)
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
        paths = [entry_path]
        if entry.executable:
            paths.extend([Path(entry.executable), Path(entry.resolved_executable)])
        owners = {tuple(sorted(package.items())) for path in paths for package in self.owners(path)}
        if len(owners) != 1:
            return
        package = dict(next(iter(owners)))
        app.source, app.provider = Source.RPM, "rpm"
        app.scope, app.version = "System", package["version"]
        app.software_size = byte_size(package.get("size"))
        app.updated_at = package_timestamp(package.get("installtime"))
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
        self.cancel_requested = False

    def capabilities(self):
        supported, reason = host_support()
        try:
            import gi

            gi.require_version("PackageKitGlib", "1.0")
            from gi.repository import PackageKitGlib  # noqa: F401
        except (ImportError, ValueError):
            supported, reason = False, "PackageKit integration is not installed."
        update_supported, update_reason = supported, reason
        try:
            import rpm  # noqa: F401
        except ImportError:
            update_supported, update_reason = False, "RPM version comparison is unavailable."
        return ProviderCapabilities(
            True, supported, supported, reason, update_supported, update_supported, update_reason
        )

    def _client(self, operation="remove"):
        import gi

        gi.require_version("PackageKitGlib", "1.0")
        from gi.repository import Gio, GLib
        from gi.repository import PackageKitGlib as Pk

        from housekeeper.models import ManagementError

        capability = self.capabilities()
        if operation == "update":
            if not capability.update_execute:
                raise ManagementError(capability.update_reason)
        elif not capability.execute:
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
        required = (
            (
                Pk.RoleEnum.RESOLVE,
                Pk.RoleEnum.REFRESH_CACHE,
                Pk.RoleEnum.GET_UPDATES,
                Pk.RoleEnum.UPDATE_PACKAGES,
            )
            if operation == "update"
            else (Pk.RoleEnum.REMOVE_PACKAGES,)
        )
        if roles is None or any(not roles.unpack() & (1 << int(role)) for role in required):
            raise ManagementError(f"The installed PackageKit backend cannot {operation} packages.")
        client = Pk.Client()
        client.set_interactive(True)
        if operation == "update":
            # Checks explicitly refresh metadata; subsequent calls can reuse that fresh cache.
            client.set_cache_age(3600)
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
            progress("Removing the system package", fraction, p.get_allow_cancel())

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
        self.cancel_requested = True
        if self.cancel:
            self.cancel.cancel()

    @staticmethod
    def _compare(left, right):
        import rpm

        def evr(value):
            epoch, separator, rest = value.partition(":")
            if not separator:
                epoch, rest = "0", value
            version, separator, release = rest.rpartition("-")
            return (epoch, version, release) if separator else (epoch, rest, "")

        return rpm.labelCompare(evr(left), evr(right))

    @staticmethod
    def _update_progress(status, progress, message):
        if status.get_status().value_nick == "waiting-for-auth":
            message = _(
                "Waiting for administrator authorization. Complete the system password or fingerprint prompt."
            )
        value = status.get_percentage()
        progress(message, value / 100 if 0 <= value <= 100 else None, status.get_allow_cancel())

    def _check_update(self, result, pk):
        if result.get_exit_code() == pk.ExitEnum.CANCELLED or (
            self.cancel is not None and self.cancel.is_cancelled()
        ):
            raise OperationCancelled(_("The update operation was cancelled."))
        self._check(result)
        if result.get_exit_code() != pk.ExitEnum.SUCCESS:
            raise ManagementError(_("PackageKit did not report a successful update check."))

    def _installed(self, client, pk, names, report):
        result = client.resolve(
            1 << int(pk.FilterEnum.INSTALLED), sorted(set(names)), self.cancel, report, None
        )
        self._check_update(result, pk)
        installed = {}
        for package in result.get_package_array():
            if package.get_name() not in names:
                continue
            key = (package.get_name(), package.get_arch())
            if key in installed:
                raise ManagementError(
                    _("Multiple installed versions require manual package management.")
                )
            installed[key] = package
        local = self._local_versions(names)
        if local.keys() != installed.keys() or any(
            self._compare(local[key], installed[key].get_version()) != 0 for key in local
        ):
            raise ManagementError(
                _(
                    "PackageKit's installed package data changed. Refresh using your package manager."
                )
            )
        return installed

    @staticmethod
    def _local_versions(names):
        import rpm

        ts = rpm.TransactionSet()
        ts.openDB()
        versions = {}
        for name in set(names):
            for header in ts.dbMatch("name", name):
                key = (header["name"], header["arch"])
                if key in versions:
                    raise ManagementError(
                        _("Multiple installed versions require manual package management.")
                    )
                versions[key] = f"{header['epoch'] or 0}:{header['version']}-{header['release']}"
        return versions

    def prepare_update(self, app, inventory, progress, *, refresh=True):
        client, pk, gio, glib = self._client("update")
        self.cancel = gio.Cancellable()
        if self.cancel_requested:
            self.cancel.cancel()
        try:
            return self._prepare_update(app, inventory, progress, client, pk, refresh=refresh)
        except glib.Error as error:
            if self.cancel.is_cancelled():
                raise OperationCancelled(_("The update check was cancelled.")) from error
            raise ManagementError(
                _("PackageKit could not check this update: ") + error.message
            ) from error

    def _prepare_update(self, app, inventory, progress, client, pk, refresh):
        if app.metadata.get("name") == "housekeeper":
            raise ManagementError(_("Update Housekeeper using your system package manager."))

        def report(p, _kind, _data):
            self._update_progress(p, progress, _("Checking system package updates"))

        progress(_("Reading installed package details"), None, True)
        name, arch = app.metadata["name"], app.metadata["arch"]
        if refresh:
            progress(_("Refreshing configured software sources"), None, True)
            self._check_update(client.refresh_cache(True, self.cancel, report, None), pk)
        installed = self._installed(client, pk, [name], report)
        current = installed.get((name, arch))
        if current is None or self._compare(current.get_version(), app.version) != 0:
            raise ManagementError(
                _("The installed package changed. Refresh and check updates again.")
            )
        result = client.get_updates(0, self.cancel, report, None)
        self._check_update(result, pk)
        candidates = {
            p.get_id(): p
            for p in result.get_package_array()
            if p.get_name() == name and p.get_arch() == arch
        }
        if not candidates:
            return UpdateCheckResult(UpdateState.CURRENT)
        if len(candidates) != 1:
            raise ManagementError(
                _("The update target is ambiguous. Review it in your package manager.")
            )
        candidate = next(iter(candidates.values()))
        if candidate.get_info() == pk.InfoEnum.BLOCKED:
            raise ManagementError(_("This update is blocked by the package manager."))
        if self._compare(candidate.get_version(), current.get_version()) <= 0:
            raise ManagementError(_("PackageKit did not provide a newer package version."))
        flags = (1 << int(pk.TransactionFlagEnum.SIMULATE)) | (
            1 << int(pk.TransactionFlagEnum.ONLY_TRUSTED)
        )
        preview = client.update_packages(flags, [candidate.get_id()], self.cancel, report, None)
        self._check_update(preview, pk)
        packages = preview.get_package_array()
        new = {}
        removed = []
        for package in packages:
            key = (package.get_name(), package.get_arch())
            info = package.get_info()
            if info in (pk.InfoEnum.UPDATING, pk.InfoEnum.INSTALLING):
                if key in new and new[key].get_id() != package.get_id():
                    raise ManagementError(
                        _("The update preview contains ambiguous package versions.")
                    )
                new[key] = package
            elif info == pk.InfoEnum.REMOVING:
                removed.append(package)
            else:
                raise ManagementError(
                    _("The update preview contains an unsupported package operation.")
                )
        if (name, arch) not in new or new[name, arch].get_id() != candidate.get_id():
            raise ManagementError(
                _("A complete update preview is unavailable. Use your package manager.")
            )
        installed = self._installed(client, pk, [key[0] for key in new], report)
        changes = []
        for key, package in sorted(new.items()):
            previous = installed.get(key)
            if key[0] == "housekeeper":
                raise ManagementError(
                    _("This transaction would update Housekeeper. Use your package manager.")
                )
            if previous and self._compare(package.get_version(), previous.get_version()) <= 0:
                raise ManagementError(_("This transaction would downgrade or reinstall a package."))
            changes.append(
                UpdateChange(
                    ".".join(key),
                    package.get_id(),
                    "update" if previous else "install",
                    package.get_id().split(";", 3)[-1],
                    previous.get_version() if previous else "",
                    package.get_version(),
                )
            )
        for package in removed:
            key = (package.get_name(), package.get_arch())
            previous = installed.get(key)
            if (
                key not in new
                or previous is None
                or self._compare(previous.get_version(), package.get_version()) != 0
            ):
                raise ManagementError(
                    _("This update would remove another package. Use your package manager.")
                )
        changes = tuple(changes)
        names = sorted(
            {a.name for a in inventory if a.provider == "rpm" and a.identity == app.identity}
        )
        message = _(
            "Update this package and the dependencies listed below. Personal application data is kept."
        )
        if len(names) > 1:
            message += _("\n\nApplications provided by this package: ") + ", ".join(names)
        repositories = self._repository_state()
        plan = UpdatePlan(
            app.key,
            "rpm",
            candidate.get_id(),
            "System",
            current.get_version(),
            changes,
            digest(app.key, current.get_id(), changes, repositories),
            message,
            environment=digest(repositories),
        )
        return UpdateCheckResult(UpdateState.AVAILABLE, plan)

    def execute_update(self, app, plan, progress):
        if plan.app_key != app.key or plan.provider != "rpm":
            raise ManagementError(_("The update plan belongs to another application."))
        client, pk, gio, _glib = self._client("update")
        self.cancel = gio.Cancellable()
        if self.cancel_requested:
            self.cancel.cancel()
        check = self._prepare_update(app, [app], progress, client, pk, refresh=False)
        if check.plan is None or check.plan.fingerprint != plan.fingerprint:
            raise ManagementError(
                _("The update plan changed. Check updates and review a new preview.")
            )

        def report(p, _kind, _data):
            self._update_progress(p, progress, _("Updating system packages"))

        result, error = None, ""
        try:
            result = client.update_packages(
                1 << int(pk.TransactionFlagEnum.ONLY_TRUSTED),
                [plan.target],
                self.cancel,
                report,
                None,
            )
            self._check(result)
        except Exception as failure:
            error = str(failure)
        # Use a fresh local RPM database even after cancellation; emitted progress is not proof of installation.
        completed = self._verified_updates(plan)
        cancelled = self.cancel.is_cancelled() or (
            result is not None and result.get_exit_code() == pk.ExitEnum.CANCELLED
        )
        success = result is not None and result.get_exit_code() == pk.ExitEnum.SUCCESS and not error
        if success and len(completed) == len(plan.changes):
            outcome, message = Outcome.SUCCESS, _("The system packages were updated.")
        elif completed:
            outcome, message = (
                Outcome.PARTIAL,
                _("Some packages changed. Refresh and review the remaining updates."),
            )
        elif cancelled:
            outcome, message = Outcome.CANCELLED, _("The update operation was cancelled.")
        else:
            outcome, message = Outcome.FAILED, _("The package update could not be verified.")
        hints = set()
        if result is not None:
            for restart in result.get_require_restart_array():
                value = restart.get_restart().value_nick
                if value not in {"none", "unknown"}:
                    hints.add(value)
        hint = _("Restart required: ") + ", ".join(sorted(hints)) if hints else ""
        return OperationResult(outcome, message, completed, (error,) if error else (), hint)

    def _verified_updates(self, plan):
        import rpm

        ts = rpm.TransactionSet()
        ts.openDB()
        completed = []
        for change in plan.changes:
            name, version, arch, _repository = change.target.split(";", 3)
            for header in ts.dbMatch("name", name):
                installed = f"{header['epoch'] or 0}:{header['version']}-{header['release']}"
                if header["arch"] == arch and self._compare(installed, version) == 0:
                    completed.append(change.identity)
                    break
        return tuple(completed)

    @staticmethod
    def _repository_state():
        paths = [Path("/etc/dnf/dnf.conf"), Path("/etc/yum.conf")]
        paths.extend(Path("/etc/yum.repos.d").glob("*.repo"))
        for directory in ("/etc/dnf/vars", "/etc/yum/vars"):
            paths.extend(p for p in Path(directory).glob("*") if p.is_file())
        return tuple((str(p), digest(p.read_bytes())) for p in sorted(paths) if p.exists())
