"""Conservative, reversible removal of individually owned AppImage files."""

import os
import stat
from pathlib import Path

from housekeeper.identity import digest
from housekeeper.models import (
    FileSnapshot,
    ManagementError,
    OperationResult,
    Outcome,
    ProviderCapabilities,
    RemovalPlan,
)


def snapshot(path):
    s = path.lstat()
    return FileSnapshot(
        str(path), s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_mode, s.st_uid
    )


class AppImageProvider:
    def __init__(self, home=None, ownership=None, trash=None):
        self.home = (Path.home() if home is None else home).resolve()
        self.ownership = ownership
        self.trash = trash

    def capabilities(self):
        return ProviderCapabilities(True, True, True)

    def _owners(self, path):
        if self.ownership is not None:
            return self.ownership(path)
        from housekeeper.providers.rpm import RpmIndex

        index = RpmIndex()
        if not index.available:
            raise ManagementError(
                "Package ownership cannot be verified on this system. "
                "Use the file manager to review this file."
            )
        return index.owners(path)

    def _validate(self, path):
        if not path.is_absolute() or not path.resolve().is_relative_to(self.home):
            raise ManagementError("Only files inside your home directory can be moved to Trash.")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ManagementError(
                "The file must be a regular file owned by you, without symbolic links."
            )
        # Reject symlinked parent directories too, so the preview names the actual files.
        if path.absolute() != path.resolve():
            raise ManagementError("A symbolic link makes this file's ownership ambiguous.")
        if self._owners(path):
            raise ManagementError(
                "This file belongs to a software package. Use its package manager."
            )
        return snapshot(path)

    def prepare(self, app, inventory):
        target = Path(app.location)
        if target.suffix.casefold() != ".appimage":
            raise ManagementError("The target is not an individually identified AppImage.")
        files = [self._validate(target)]
        if not app.entries:
            raise ManagementError("The application has no verified desktop entry.")
        for entry in app.entries:
            # Only direct launches are removable; wrappers and symlink aliases remain manual.
            if not entry.argv or entry.argv[0] != str(target):
                raise ManagementError(
                    "The launcher uses a wrapper or symbolic link. Review it manually."
                )
            files.append(self._validate(entry.path))
        # Different launch arguments can give the same file multiple distinct records.
        other = [a for a in inventory if a.key != app.key and a.location == str(target)]
        if other:
            raise ManagementError(
                "Other application entries share this AppImage. Review them in the file manager."
            )
        return RemovalPlan(
            app.key,
            "appimage",
            str(target),
            tuple(f.path for f in files),
            "These files will be moved to Trash. Configuration, caches, icons, and other files are kept.",
            digest(files),
            tuple(files),
        )

    def execute(self, app, plan, progress):
        fresh = self.prepare(app, [app])
        if fresh.files != plan.files:
            raise ManagementError("The files changed since the preview. Review a new plan.")
        completed, errors = [], []
        for index, item in enumerate(plan.files):
            path = Path(item.path)
            progress("Moving files to Trash…", index / len(plan.files), False)
            try:
                if self._validate(path) != item:
                    raise ManagementError("The file changed during removal.")
                if self.trash is not None:
                    self.trash(path)
                else:
                    from gi.repository import Gio

                    Gio.File.new_for_path(str(path)).trash(None)
                completed.append(str(path))
            except Exception as error:
                errors.append(f"{path}: {error}")
                # Keep launchers intact if moving the executable itself failed.
                if index == 0:
                    break
        outcome = (
            Outcome.PARTIAL
            if completed and errors
            else Outcome.FAILED
            if errors
            else Outcome.SUCCESS
        )
        message = (
            "The selected files were moved to Trash. Restore them using your file manager."
            if not errors
            else "Some files could not be moved to Trash. Review the results below."
        )
        return OperationResult(outcome, message, tuple(completed), tuple(errors))
