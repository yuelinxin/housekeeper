"""Optional libflatpak inventory and transactions."""

from copy import deepcopy
from pathlib import Path

from housekeeper import APP_ID
from housekeeper.i18n import _
from housekeeper.identity import digest
from housekeeper.models import (
    Action,
    AppRecord,
    ManagementError,
    OperationCancelled,
    OperationResult,
    Outcome,
    ProviderCapabilities,
    RemovalPlan,
    Source,
    UpdateChange,
    UpdateCheckResult,
    UpdatePlan,
    UpdateState,
)
from housekeeper.providers.packages import byte_size


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
                    try:
                        size = byte_size(ref.get_installed_size())
                    except Exception:
                        size = None
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
                            software_size=size,
                            metadata={
                                "installation": path,
                                "app_id": ref.get_name(),
                                "commit": ref.get_commit() or "",
                                "installation_id": installation.get_id() or "default",
                                "current": "true" if ref.get_is_current() else "false",
                            },
                        )
                    )
            except Exception as error:
                self.warnings.append(f"Could not read a Flatpak installation: {error}")
        from housekeeper.providers.flatpak_history import enrich_history

        enrich_history(self.apps, self.installations)

    @property
    def roots(self):
        return [Path(path) / "exports/share/applications" for path in self.installations]

    def associate(self, app):
        from housekeeper.providers.flatpak_attribution import binding, parse_launch

        if not app.entries or app.source not in {Source.OTHER, Source.FLATPAK}:
            return None
        entry = app.entries[0]
        # Visibility overlays never establish an ownership relationship.
        if entry.reason == "Hidden by a desktop entry override":
            for candidate in self.apps:
                if entry.desktop_id == candidate.metadata["app_id"] + ".desktop":
                    candidate.visible, candidate.status = False, entry.reason
            return None
        launch = parse_launch(entry)
        if launch is None or (entry.flatpak_id and entry.flatpak_id != launch.app_id):
            return None
        matches = [a for a in self.apps if a.metadata["app_id"] == launch.app_id]
        if launch.scope:
            matches = [
                a
                for a in matches
                if a.scope == launch.scope
                and (
                    launch.scope != "System"
                    or a.metadata.get("installation_id", "default") == "default"
                )
            ]
        elif launch.installation:
            matches = [
                a
                for a in matches
                if a.scope == "System"
                and a.metadata.get("installation_id", "default") == launch.installation
            ]
        elif any(a.scope == "User" for a in matches):
            matches = [a for a in matches if a.scope == "User"]
        if launch.branch:
            matches = [a for a in matches if a.identity.split("/")[-1] == launch.branch]
        elif len({a.identity for a in matches}) > 1:
            matches = [a for a in matches if a.metadata.get("current") == "true"]
        if launch.arch:
            matches = [a for a in matches if a.identity.split("/")[-2] == launch.arch]
        if len(matches) != 1:
            return None
        match = matches[0]
        evidence = binding(entry, match, launch)
        if not evidence:
            return None
        result = deepcopy(match)
        result.entries = [entry]
        result.name, result.icon = app.name, app.icon
        result.visible, result.status = app.visible, app.status
        result.metadata["flatpak_binding"] = evidence
        return result

    @staticmethod
    def validate(app):
        if not app.entries:
            return  # Installation records are rechecked by the transaction backend.
        from housekeeper.discovery import read_entry
        from housekeeper.identity import classify

        index = FlatpakIndex()
        for entry in app.entries:
            try:
                current = index.associate(classify(read_entry(entry.path, entry.root)))
            except Exception as error:
                raise ManagementError(
                    "The Flatpak launcher cannot be verified. Refresh the inventory."
                ) from error
            if (
                current is None
                or current.identity != app.identity
                or current.metadata.get("installation") != app.metadata.get("installation")
                or current.metadata.get("flatpak_binding") != app.metadata.get("flatpak_binding")
            ):
                raise ManagementError(
                    "The Flatpak launcher or installation changed. Refresh and review a new preview."
                )


class FlatpakProvider:
    def __init__(self):
        self.cancel = None
        self.cancel_requested = False

    def capabilities(self):
        try:
            fp = load_flatpak()
            version = (fp.MAJOR_VERSION, fp.MINOR_VERSION, fp.MICRO_VERSION)
            supported = version >= (1, 9, 1)
            reason = "" if supported else "Flatpak 1.9.1 or newer is required for update previews."
            return ProviderCapabilities(True, True, True, "", supported, supported, reason)
        except (ImportError, ValueError):
            reason = "Flatpak integration is not installed."
            return ProviderCapabilities(False, reason=reason, update_reason=reason)

    def _transaction(self, app):
        FlatpakIndex.validate(app)
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
            digest(
                app.metadata["installation"],
                commit,
                captured,
                app.metadata.get("flatpak_binding", ""),
            ),
        )

    def execute(self, app, plan, progress):
        _fp, transaction, commit = self._transaction(app)
        mismatch = []

        def ready(tx):
            fingerprint = digest(
                app.metadata["installation"],
                commit,
                list(self._operations(tx)),
                app.metadata.get("flatpak_binding", ""),
            )
            if fingerprint != plan.fingerprint:
                mismatch.append(True)
                return False
            return True

        def started(_tx, _operation, operation_progress):
            def changed(p):
                fraction = None if p.get_is_estimating() else p.get_progress() / 100
                progress(p.get_status() or "Removing Flatpak", fraction, False)

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

    def request_cancel(self):
        self.cancel_requested = True
        if self.cancel:
            self.cancel.cancel()

    def _update_transaction(self, app, target=None):
        import os

        from gi.repository import Gio

        if os.geteuid() == 0:
            raise ManagementError(_("Run Housekeeper as a regular desktop user."))
        if app.metadata.get("app_id") == APP_ID:
            raise ManagementError(_("Update Housekeeper using your system package manager."))
        FlatpakIndex.validate(app)
        fp = load_flatpak()
        self.cancel = Gio.Cancellable()
        if self.cancel_requested:
            self.cancel.cancel()
        installations, warnings = configured_installations(fp)
        installation = next(
            (
                i
                for i in installations
                if i.get_path().get_path() == app.metadata.get("installation")
            ),
            None,
        )
        if installation is None:
            raise ManagementError(
                _("The Flatpak installation is unavailable. ") + " ".join(warnings)
            )
        refs = self._installed_refs(installation)
        current = refs.get(app.identity)
        if current is None:
            raise ManagementError(
                _("The application is no longer installed. Refresh the inventory.")
            )
        if not app.origin or current.get_origin() != app.origin:
            raise ManagementError(
                _("The application's Flatpak source changed. Refresh the inventory.")
            )
        if app.metadata.get("commit") and app.metadata["commit"] != current.get_commit():
            raise ManagementError(
                _("The installed Flatpak changed. Refresh and check updates again.")
            )
        remote = installation.get_remote_by_name(app.origin, self.cancel)
        if remote.get_disabled():
            raise ManagementError(_("This application's Flatpak source is disabled."))
        tx = fp.Transaction.new_for_installation(installation, self.cancel)
        # Use the desktop Polkit agent for password/fingerprint authorization when required.
        tx.set_no_interaction(False)
        tx.set_include_unused_uninstall_ops(False)
        tx.set_disable_dependencies(False)
        tx.set_disable_related(False)
        tx.set_disable_prune(True)
        # System-helper updates reject explicit commits before Polkit authorization.
        # Resolve the normal system update, then compare every commit at ready before deployment.
        tx.add_update(app.identity, None, target if installation.get_is_user() else None)
        return fp, installation, tx

    @staticmethod
    def _installed_refs(installation):
        return {ref.format_ref(): ref for ref in installation.list_installed_refs(None)}

    @staticmethod
    def _remote_state(installation):
        return tuple(
            sorted(
                (r.get_name(), r.get_url(), r.get_gpg_verify(), r.get_disabled())
                for r in installation.list_remotes(None)
            )
        )

    def _update_plan(self, app, fp, installation, tx):
        refs = self._installed_refs(installation)
        current = refs.get(app.identity)
        if current is None or current.get_origin() != app.origin:
            raise ManagementError(_("The Flatpak installation changed. Check updates again."))
        changes, total = [], 0
        target = current.get_commit()
        for op in tx.get_operations():
            if op.get_is_skipped():
                continue
            kind, identity, commit = op.get_operation_type(), op.get_ref(), op.get_commit()
            if kind not in (
                fp.TransactionOperationType.INSTALL,
                fp.TransactionOperationType.UPDATE,
            ):
                raise ManagementError(
                    _("This update requires removal or migration. Use your software manager.")
                )
            if not commit or identity.split("/")[1] == APP_ID:
                raise ManagementError(_("This update target requires external management."))
            previous = refs.get(identity)
            if previous and previous.get_origin() != op.get_remote():
                raise ManagementError(_("This update would change a Flatpak source."))
            if identity.startswith("app/") and identity != app.identity:
                raise ManagementError(_("This transaction would change another application."))
            if identity == app.identity:
                target = commit
            changes.append(
                UpdateChange(
                    identity,
                    commit,
                    "update" if previous else "install",
                    op.get_remote(),
                    previous.get_commit() if previous else "",
                    commit,
                )
            )
            total += op.get_download_size()
        changes = tuple(sorted(changes, key=lambda c: c.identity))
        current_commit = current.get_commit()
        message = _(
            "Update this installation and the dependencies listed below. Personal application data is kept."
        )
        if changes and not any(c.identity == app.identity for c in changes):
            message = _(
                "The application itself is current. Only the listed runtimes or extensions will be updated."
            )
        remotes = self._remote_state(installation)
        return UpdatePlan(
            app.key,
            "flatpak",
            target,
            app.metadata["installation"],
            current_commit,
            changes,
            digest(
                app.key,
                app.identity,
                app.metadata["installation"],
                current_commit,
                app.origin,
                app.metadata.get("flatpak_binding", ""),
                remotes,
                changes,
            ),
            message,
            total,
            environment=digest(remotes),
        )

    @staticmethod
    def _update_policy(tx, installation, issues):
        remotes = {r.get_name() for r in installation.list_remotes(None)}

        def refuse(message):
            def callback(*_args):
                issues.append(message)
                return False

            return callback

        def choose(_tx, _ref, choices):
            if len(choices) == 1 and choices[0] in remotes:
                return 0
            issues.append(_("The dependency source is ambiguous. Use your software manager."))
            return -1

        tx.connect(
            "add-new-remote",
            refuse(_("This update needs a new software source. Use your software manager.")),
        )
        tx.connect("choose-remote-for-ref", choose)
        tx.connect(
            "end-of-lifed-with-rebase",
            refuse(_("This application needs migration. Use your software manager.")),
        )
        tx.connect(
            "basic-auth-start",
            refuse(_("This source requires a login. Use your software manager.")),
        )
        tx.connect(
            "webflow-start", refuse(_("This source requires a login. Use your software manager."))
        )
        tx.connect(
            "install-authenticator",
            refuse(_("This source requires an authenticator. Use your software manager.")),
        )

    def prepare_update(self, app, inventory, progress):
        from gi.repository import GLib

        progress(_("Resolving Flatpak updates"), None, True)
        try:
            fp, installation, tx = self._update_transaction(app)
        except GLib.Error as error:
            if self.cancel_requested:
                raise OperationCancelled(_("The update check was cancelled.")) from error
            raise ManagementError(
                _("Flatpak could not check this update: ") + error.message
            ) from error
        issues, captured = [], []
        self._update_policy(tx, installation, issues)

        def ready(transaction):
            try:
                captured.append(self._update_plan(app, fp, installation, transaction))
            except Exception as error:
                issues.append(str(error))
            return False

        tx.connect("ready-pre-auth", ready)
        try:
            tx.run(self.cancel)
        except GLib.Error as error:
            if self.cancel.is_cancelled():
                raise OperationCancelled(_("The update check was cancelled.")) from error
            if issues:
                raise ManagementError("\n".join(issues)) from error
            if not captured or not error.matches(fp.error_quark(), fp.Error.ABORTED):
                raise ManagementError(
                    _("Flatpak could not check this update: ") + error.message
                ) from error
        if self.cancel.is_cancelled():
            raise OperationCancelled(_("The update check was cancelled."))
        if issues:
            raise ManagementError("\n".join(issues))
        if not captured:
            raise ManagementError(_("Flatpak did not provide a complete update check."))
        plan = captured[0]
        if not plan.changes:
            return UpdateCheckResult(UpdateState.CURRENT)
        return UpdateCheckResult(UpdateState.AVAILABLE, plan)

    def execute_update(self, app, plan, progress):
        if (
            plan.app_key != app.key
            or plan.provider != "flatpak"
            or plan.installation != app.metadata.get("installation")
        ):
            raise ManagementError(_("The update plan belongs to another installation."))
        fp, installation, tx = self._update_transaction(app, plan.target)
        issues, completed = [], []
        self._update_policy(tx, installation, issues)
        accepted = False

        def ready(transaction):
            nonlocal accepted
            try:
                fresh = self._update_plan(app, fp, installation, transaction)
                if fresh.fingerprint != plan.fingerprint:
                    issues.append(
                        _("The update plan changed. Check updates and review a new preview.")
                    )
            except Exception as error:
                issues.append(str(error))
            accepted = not issues
            return accepted

        def started(_tx, _op, operation_progress):
            def changed(p):
                progress(
                    p.get_status() or _("Updating Flatpak"),
                    None if p.get_is_estimating() else p.get_progress() / 100,
                    True,
                )

            operation_progress.connect("changed", changed)
            changed(operation_progress)

        def done(_tx, op, _commit, _result):
            completed.append(op.get_ref())

        def failed(_tx, _op, error, _details):
            issues.append(str(error))
            return False

        tx.connect("ready", ready)
        tx.connect("new-operation", started)
        tx.connect("operation-done", done)
        tx.connect("operation-error", failed)
        # GCancellable requests a cooperative stop; never terminate the helper or worker.
        progress(_("Rechecking the Flatpak update plan"), None, True)
        success = False
        try:
            success = tx.run(self.cancel)
        except Exception as error:
            if not issues:
                issues.append(str(error))
        progress(_("Verifying installed Flatpak components"), None, False)
        # Refresh the installation cache before verifying commits after deployment.
        try:
            installation.drop_caches(None)
            refs = self._installed_refs(installation)
        except Exception as error:
            refs = {}
            issues.append(_("Could not verify installed Flatpak commits: ") + str(error))
        verified = tuple(
            c.identity
            for c in plan.changes
            if c.identity in refs
            and refs[c.identity].get_commit() == c.target
            and refs[c.identity].get_origin() == c.source
            and c.current_version != c.target
            and accepted
        )
        if accepted and success and not issues and len(verified) == len(plan.changes):
            outcome, message = (
                Outcome.SUCCESS,
                _("The Flatpak application components were updated."),
            )
        elif accepted and (verified or completed):
            outcome, message = (
                Outcome.PARTIAL,
                _("Some Flatpak components changed. Review the remaining updates."),
            )
        elif self.cancel.is_cancelled():
            outcome, message = Outcome.CANCELLED, _("The update operation was cancelled.")
        else:
            outcome, message = Outcome.FAILED, _("The Flatpak update could not be completed.")
        return OperationResult(
            outcome,
            message,
            tuple(dict.fromkeys((*verified, *completed))),
            tuple(dict.fromkeys(issues)),
        )
