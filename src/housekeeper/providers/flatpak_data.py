"""Current-user Flatpak data, scoped by the app ID supplied by libflatpak."""

import os
import shutil
import stat
import subprocess
from contextlib import contextmanager

from housekeeper.i18n import _
from housekeeper.models import ManagementError


def app_id(app):
    from housekeeper.providers.flatpak import load_flatpak

    fp = load_flatpak()
    ref = fp.Ref.parse(app.identity)
    name = ref.get_name()
    if ref.get_kind() != fp.RefKind.APP or app.metadata.get("app_id") != name:
        raise ManagementError(_("The Flatpak application ID could not be verified."))
    return name


@contextmanager
def data_parent():
    """Pin the data parent and reject redirected parents, even during cleanup."""
    from gi.repository import GLib

    descriptor = os.open(GLib.get_home_dir(), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in (".var", "app"):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def cleanup_command(app):
    """Validate cleanup prerequisites before uninstalling anything."""
    name = app_id(app)
    executable = shutil.which("flatpak")
    if executable is None:
        raise ManagementError(_("Flatpak is required to delete application data and permissions."))
    return (executable, "permission-reset", "--", name)


def _remove_directory(parent, name):
    """Remove one tree using directory descriptors; never follow symbolic links."""
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        with os.scandir(descriptor) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    _remove_directory(descriptor, entry.name)
                else:
                    os.unlink(entry.name, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent)


def delete_user_data(app, command):
    """Match --delete-data semantics after the verified libflatpak uninstall.

    libflatpak exposes neither data deletion nor a transaction delete-data option.
    Re-running `uninstall --delete-data` would fail for the now absent ref (or
    uninstall a concurrent replacement). Remove only its data, then ask Flatpak
    to reset its dynamic permissions.
    """
    name = app_id(app)
    try:
        with data_parent() as parent:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                os.unlink(name, dir_fd=parent)
            elif stat.S_ISDIR(info.st_mode):
                try:
                    _remove_directory(parent, name)
                except FileNotFoundError as error:
                    # A disappearing child must not turn an incomplete cleanup into success.
                    raise ManagementError(
                        _("The Flatpak data directory changed during cleanup.")
                    ) from error
            else:
                raise ManagementError(_("The Flatpak data directory is not a directory."))
    except FileNotFoundError:
        pass
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    if result.returncode:
        raise ManagementError(
            result.stderr.strip() or _("Flatpak could not reset the application's permissions.")
        )
