"""Remove only verified, user-owned website launchers created by Housekeeper."""

import hashlib
import os
import stat
from pathlib import Path

from gi.repository import Gio, GLib

from housekeeper.appearance import data_home
from housekeeper.attribution import check_binding, plan_binding
from housekeeper.discovery import read_entry
from housekeeper.i18n import _
from housekeeper.identity import BROWSERS, digest
from housekeeper.installations import desktop_exec, normalize_url
from housekeeper.models import (
    FileOwnershipState,
    ManagementError,
    OperationCancelled,
    OperationResult,
    Outcome,
    RemovalPlan,
    Source,
)
from housekeeper.providers.appimage import snapshot
from housekeeper.web_identity import launch_args, legacy_identifier, window_identity


class WebLauncherProvider:
    def __init__(self, ownership=None, trash=None):
        self.ownership = ownership
        self.trash = trash
        self.cancel = Gio.Cancellable()

    def request_cancel(self):
        self.cancel.cancel()

    def _check_cancelled(self):
        if self.cancel.is_cancelled():
            raise OperationCancelled(_("Web app removal was cancelled."))

    def prepare(self, app, inventory):
        self._check_cancelled()
        if app.source != Source.WEB or len(app.entries) != 1:
            raise ManagementError(_("This web app must be managed in its browser."))
        entry = app.entries[0]
        path = entry.path
        root = data_home() / "applications"
        # Refuse a symlinked launcher, but not a symlinked data directory: many systems
        # link ~/.local/share to another disk. The snapshot below uses lstat as well.
        if not path.is_absolute() or path.parent != root or path.is_symlink():
            raise ManagementError(_("Only local launchers created by Housekeeper can be removed."))
        before = snapshot(path)
        if not stat.S_ISREG(before.mode) or before.uid != os.getuid():
            raise ManagementError(_("The launcher must be a regular file owned by you."))
        current = read_entry(path, root)
        # Undo literal percent escaping, then require our exact generated Exec format.
        argv = tuple(arg.replace("%%", "%") for arg in current.argv)
        if (
            not current.housekeeper_created
            or current.dbus_activatable
            or current.flatpak_id
            or len(argv) not in {2, 3}
            or not Path(argv[0]).is_absolute()
            or Path(argv[0]).name not in BROWSERS
            or not argv[-1].startswith("--app=")
            or current.command != desktop_exec(argv)
        ):
            raise ManagementError(_("This launcher is not a Housekeeper website shortcut."))
        url = normalize_url(argv[-1][len("--app=") :])
        if len(argv) == 2:
            identifier = legacy_identifier(argv[0], url)
        else:
            identifier, _wm_class = window_identity(url)
            if argv != launch_args(argv[0], url):
                raise ManagementError(_("This launcher is not a Housekeeper website shortcut."))
        if path.name != identifier + ".desktop" or current.command != entry.command:
            raise ManagementError(_("The launcher changed. Refresh and review a new preview."))
        if any(
            other.key != app.key and path in {e.path for e in other.entries} for other in inventory
        ):
            raise ManagementError(_("Other application entries share this launcher."))
        if self.ownership is None:
            from housekeeper.ownership import FileOwnershipIndex

            owners = FileOwnershipIndex().query(path)
        else:
            owners = self.ownership(path)
        if owners.state == FileOwnershipState.OWNED:
            raise ManagementError(_("This launcher belongs to a software package."))
        if owners.state not in {FileOwnershipState.UNOWNED, FileOwnershipState.NOT_APPLICABLE}:
            raise ManagementError(_("The launcher's package ownership could not be verified."))
        contents = hashlib.sha256(path.read_bytes()).hexdigest()
        if snapshot(path) != before:
            raise ManagementError(_("The launcher changed. Refresh and review a new preview."))
        self._check_cancelled()
        return RemovalPlan(
            app.key,
            "web-launcher",
            str(path),
            (str(path),),
            _(
                "This removes the web app from your app menu by moving its desktop launcher to Trash. Browser data is kept."
            ),
            digest(before, contents),
            (before,),
            **plan_binding(app),
        )

    def execute(self, app, plan, progress):
        self._check_cancelled()
        check_binding(app, plan)
        progress(_("Removing Web App"), None, False)
        # Re-read after the progress callback and compare the complete approved plan.
        if self.prepare(app, [app]) != plan:
            raise ManagementError(_("The launcher changed since the preview. Review a new plan."))
        path = Path(plan.target)
        self._check_cancelled()
        try:
            if self.trash is not None:
                self.trash(path)
            elif not Gio.File.new_for_path(str(path)).trash(self.cancel):
                raise ManagementError(_("The launcher could not be moved to Trash."))
        except GLib.Error as error:
            if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                raise OperationCancelled(_("Web app removal was cancelled.")) from error
            return OperationResult(
                Outcome.FAILED, _("The web app could not be uninstalled."), errors=(str(error),)
            )
        except Exception as error:
            return OperationResult(
                Outcome.FAILED, _("The web app could not be uninstalled."), errors=(str(error),)
            )
        return OperationResult(
            Outcome.SUCCESS,
            _("The web app was uninstalled. Its launcher is in Trash; browser data is kept."),
            (str(path),),
        )
