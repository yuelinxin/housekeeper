"""Read launcher theme overrides and manage per-user launcher icons."""

import hashlib
import os
import stat
import tempfile
from pathlib import Path

from gi.repository import GLib

from housekeeper.i18n import _
from housekeeper.models import ManagementError

GROUP = "Desktop Entry"
PREFIX = "X-Housekeeper-"
ORIGINAL = PREFIX + "OriginalIcon"
HAD_ICON = PREFIX + "HadIcon"
SOURCE = PREFIX + "IconSource"
MARKERS = {ORIGINAL, HAD_ICON, SOURCE}


def launcher_theme(entry):
    """Only interpret direct env assignments, never shell commands or wrappers."""
    if not entry.argv or Path(entry.argv[0]).name != "env":
        return ""
    theme = ""
    for arg in entry.argv[1:]:
        if "=" not in arg or arg.startswith("-"):
            break
        key, value = arg.split("=", 1)
        if key == "GTK_THEME":
            theme = value
    return theme


def _load(path):
    keyfile = GLib.KeyFile()
    keyfile.load_from_file(
        str(path), GLib.KeyFileFlags.KEEP_COMMENTS | GLib.KeyFileFlags.KEEP_TRANSLATIONS
    )
    return keyfile


def _get(keyfile, key, default=""):
    try:
        return keyfile.get_string(GROUP, key)
    except GLib.Error:
        return default


def _has(keyfile, key):
    return key in keyfile.get_keys(GROUP)[0]


def _values(keyfile):
    return {
        (group, key): keyfile.get_value(group, key)
        for group in keyfile.get_groups()[0]
        for key in keyfile.get_keys(group)[0]
        if not (group == GROUP and key in {*MARKERS, "Icon"})
    }


def _comments(keyfile):
    return [line for line in keyfile.to_data()[0].splitlines() if line.lstrip().startswith("#")]


def verified_icon_source(path):
    """An icon-only override may retain attribution to its unchanged source.

    Never trust a launcher-supplied source path without comparing every other key,
    including translations, launch arguments, visibility and desktop actions.
    """
    try:
        keyfile = _load(path)
        source = Path(_get(keyfile, SOURCE))
        if source.is_absolute() and source != path and _values(keyfile) == _values(_load(source)):
            return source
    except (GLib.Error, OSError, ValueError):
        pass
    return path


def data_home():
    value = os.environ.get("XDG_DATA_HOME", "")
    return Path(value) if value and Path(value).is_absolute() else Path.home() / ".local/share"


def _target(entry):
    root = data_home() / "applications"
    desktop_id = entry.desktop_id
    if (
        not desktop_id.endswith(".desktop")
        or Path(desktop_id).name != desktop_id
        or "\\" in desktop_id
        or "\n" in desktop_id
    ):
        raise ManagementError(_("This launcher has an invalid desktop ID."))
    # Preserve nested user launchers instead of introducing a colliding flattened ID.
    target = entry.path if entry.path.is_relative_to(root) else root / desktop_id
    if target.is_symlink():
        raise ManagementError(_("This launcher is a symbolic link and cannot be edited here."))
    if target.exists() and target != entry.path:
        raise ManagementError(_("The launcher has changed. Refresh the inventory and try again."))
    return target


def can_reset_icon(entry):
    try:
        return bool(_get(_load(entry.path), HAD_ICON))
    except (GLib.Error, OSError):
        return False


def _atomic_write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".housekeeper-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_icon(entry, image=None):
    """Set a normalized, durable image, or restore just Housekeeper's icon change."""
    target = _target(entry)
    before = entry.path.read_bytes()
    keyfile = _load(entry.path)
    if _get(keyfile, "Icon", "application-x-executable") != entry.icon:
        raise ManagementError(_("The icon has changed. Refresh the inventory and try again."))
    if image is None:
        if not _get(keyfile, HAD_ICON):
            raise ManagementError(_("There is no custom icon to restore."))
        source = verified_icon_source(entry.path)
        if source != entry.path and _comments(keyfile) == _comments(_load(source)):
            if entry.path.read_bytes() != before:
                raise ManagementError(_("The launcher changed while restoring its icon."))
            target.unlink()
            return source
        if _get(keyfile, HAD_ICON) == "true":
            keyfile.set_string(GROUP, "Icon", _get(keyfile, ORIGINAL))
        elif _has(keyfile, "Icon"):
            keyfile.remove_key(GROUP, "Icon")
        for key in MARKERS:
            if _has(keyfile, key):
                keyfile.remove_key(GROUP, key)
    else:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        image = Path(image)
        if not image.is_file() or image.stat().st_size > 10 * 1024 * 1024:
            raise ManagementError(_("Choose an image file smaller than 10 MB."))
        # Decode before changing any launcher. Store a PNG so the icon remains valid
        # after the selected image is moved, and SVG external resources aren't retained.
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(image), 512, 512, True)
        success, contents = pixbuf.save_to_bufferv("png", [], [])
        if not success:
            raise ManagementError(_("The selected image could not be read."))
        icon_path = (
            data_home() / "housekeeper/icons" / (hashlib.sha256(contents).hexdigest() + ".png")
        )
        _atomic_write(icon_path, contents)
        if not _get(keyfile, HAD_ICON):
            keyfile.set_string(GROUP, HAD_ICON, "true" if _has(keyfile, "Icon") else "false")
            keyfile.set_string(GROUP, ORIGINAL, _get(keyfile, "Icon"))
            if target != entry.path:
                keyfile.set_string(GROUP, SOURCE, str(entry.path))
        keyfile.set_string(GROUP, "Icon", str(icon_path))
    if entry.path.read_bytes() != before:
        raise ManagementError(_("The launcher changed while saving its icon. Try again."))
    _target(entry)
    mode = stat.S_IMODE(entry.path.stat().st_mode) if target == entry.path else 0o644
    _atomic_write(target, keyfile.to_data()[0].encode(), mode)
    return target
