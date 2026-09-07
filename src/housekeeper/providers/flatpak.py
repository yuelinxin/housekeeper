"""Optional libflatpak inventory and transactions."""

from pathlib import Path

from housekeeper.identity import digest, option
from housekeeper.models import (
    Action,
    AppRecord,
    ManagementError,
    OperationResult,
    Outcome,
    ProviderCapabilities,
    RemovalPlan,
    Source,
)


def load_flatpak():
    import gi

    gi.require_version("Flatpak", "1.0")
    from gi.repository import Flatpak

    return Flatpak


def configured_installations(fp):
    from gi.repository import Gio, GLib

    candidates, warnings = [], []
    for name, discover in (
        ("user", lambda: [fp.Installation.new_user(None)]),
        ("system", lambda: fp.get_system_installations(None)),
    ):
        try:
            candidates.extend(discover())
        except Exception as error:
            if (
                name == "system"
                and isinstance(error, GLib.Error)
                and error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND)
            ):
                continue
            warnings.append(f"Flatpak {name} installation discovery failed: {error}")
    return candidates, warnings


class FlatpakIndex:
    def __init__(self):
        self.installations = {}
        self.apps = []
        self.warnings = []
        try:
            fp = load_flatpak()
        except (ImportError, ValueError):
            return
        candidates, self.warnings = configured_installations(fp)
        for installation in candidates:
            path = installation.get_path().get_path()
            self.installations[path] = installation
            try:
                for ref in installation.list_installed_refs(None):
                    if ref.get_kind() != fp.RefKind.APP:
                        continue
                    identity = ref.format_ref()
                    self.apps.append(
                        AppRecord(
                            key=digest("flatpak", path, identity),
                            name=ref.get_appdata_name() or ref.get_name(),
                            source=Source.FLATPAK,
                            provider="flatpak",
                            version=ref.get_appdata_version() or "",
                            scope="User" if installation.get_is_user() else "System",
                            identity=identity,
                            origin=ref.get_origin() or "",
                            location=ref.get_deploy_dir() or "",
                            icon=ref.get_name(),
                            action=Action.UNINSTALL,
                            metadata={"installation": path, "app_id": ref.get_name()},
                        )
                    )
            except Exception as error:
                self.warnings.append(f"Could not read a Flatpak installation: {error}")

    @property
    def roots(self):
        return [Path(path) / "exports/share/applications" for path in self.installations]

    def associate(self, app):
        entry = app.entries[0]
        app_id = entry.flatpak_id
        if not app_id and not entry.visible and entry.desktop_id.endswith(".desktop"):
            app_id = entry.desktop_id.removesuffix(".desktop")
        if not app_id:
            return None
        matches = [a for a in self.apps if a.metadata["app_id"] == app_id]
        if not matches:
            if entry.flatpak_id:
                app.source = Source.FLATPAK
                app.metadata["management_reason"] = "This Flatpak installation is unavailable."
                return app
            return None
        branch, arch = option(entry.argv, "--branch"), option(entry.argv, "--arch")
        if branch:
            matches = [a for a in matches if a.identity.rsplit("/", 1)[-1] == branch]
        if arch:
            matches = [a for a in matches if a.identity.split("/")[-2] == arch]
        deployed = [
            a
            for a in matches
            if a.location and entry.path.resolve().is_relative_to(Path(a.location))
        ]
        if len(deployed) == 1:
            matches = deployed
        exact = [
            a
            for a in matches
            if str(entry.path).startswith(a.metadata["installation"] + "/")
            or entry.path.resolve().is_relative_to(Path(a.metadata["installation"]))
        ]
        if len(exact) == 1:
            matches = exact
        if len(matches) != 1:
            # User overrides can hide all matching installations but must not choose a removal target.
            if not entry.visible:
                for candidate in matches:
                    candidate.visible = False
                    candidate.status = entry.reason
            app.source = Source.FLATPAK
            app.metadata["management_reason"] = (
                "The Flatpak installation could not be identified uniquely."
            )
            return app
        match = matches[0]
        match.entries.append(entry)
        match.name, match.icon = app.name, app.icon
        match.visible, match.status = app.visible, app.status
        return match


class FlatpakProvider:
    def capabilities(self):
        try:
            load_flatpak()
            return ProviderCapabilities(True, True, True)
        except (ImportError, ValueError):
            return ProviderCapabilities(False, reason="Flatpak integration is not installed.")

    def _transaction(self, app):
        fp = load_flatpak()
        installations, warnings = configured_installations(fp)
        match = next(
            (i for i in installations if i.get_path().get_path() == app.metadata["installation"]),
            None,
        )
        if match is None:
            raise ManagementError(
                "The Flatpak installation is no longer available. " + " ".join(warnings)
            )
        refs = {r.format_ref(): r for r in match.list_installed_refs(None)}
        if app.identity not in refs:
            raise ManagementError("The application is no longer installed. Refresh the inventory.")
        transaction = fp.Transaction.new_for_installation(match, None)
        transaction.set_include_unused_uninstall_ops(False)
        transaction.set_disable_related(True)
        transaction.add_uninstall(app.identity)
        return fp, transaction, refs[app.identity].get_commit()

    @staticmethod
    def _operations(transaction):
        return tuple(
            sorted(
                (op.get_ref(), int(op.get_operation_type()))
                for op in transaction.get_operations()
                if not op.get_is_skipped()
            )
        )

    def prepare(self, app, inventory):
        fp, transaction, commit = self._transaction(app)
        captured = []

        def ready(tx):
            captured.extend(self._operations(tx))
            return False

        transaction.connect("ready", ready)
        try:
            transaction.run(None)
        except Exception:
            if not captured:
                raise
        expected = [(app.identity, int(fp.TransactionOperationType.UNINSTALL))]
        if captured != expected:
            raise ManagementError("Flatpak could not produce a single-application removal plan.")
        return RemovalPlan(
            app.key,
            "flatpak",
            app.identity,
            (app.name,),
            "This removes this installation of the application. User data and shared runtimes are kept.",
            digest(app.metadata["installation"], commit, captured),
        )

    def execute(self, app, plan, progress):
        _fp, transaction, commit = self._transaction(app)
        mismatch = []

        def ready(tx):
            fingerprint = digest(app.metadata["installation"], commit, list(self._operations(tx)))
            if fingerprint != plan.fingerprint:
                mismatch.append(True)
                return False
            return True

        def started(_tx, _operation, operation_progress):
            def changed(p):
                fraction = None if p.get_is_estimating() else p.get_progress() / 100
                progress(p.get_status() or "Removing Flatpak…", fraction, False)

            operation_progress.connect("changed", changed)
            changed(operation_progress)

        transaction.connect("ready", ready)
        transaction.connect("new-operation", started)
        try:
            transaction.run(None)
        except Exception:
            if mismatch:
                raise ManagementError(
                    "The Flatpak removal plan changed. Review a new preview."
                ) from None
            raise
        return OperationResult(
            Outcome.SUCCESS, "The Flatpak application was uninstalled.", (plan.target,)
        )
