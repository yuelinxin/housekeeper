"""Application-wide desktop icons with a reversible, per-user icon theme layer.

The desktop files cover Shell.App consumers. Named aliases cover consumers which
look up an app ID or window class directly (including Chromium task buttons).
The small theme inherits the user's theme; installed themes are never modified.
"""

import fcntl
import hashlib
import json
import re
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from gi.repository import Gio, GLib

from housekeeper.appearance import (
    ORIGINAL,
    _atomic_write,
    _get,
    _load,
    _target,
    can_reset_icon,
    data_home,
    save_icon,
    store_icon_image,
)
from housekeeper.i18n import _
from housekeeper.models import ManagementError

THEME_PREFIX = "housekeeper-icons-"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,239}\Z")
# Never replace common fallback/category icons for other applications.
GENERIC = {
    "application-x-executable",
    "application-default-icon",
    "web-browser",
    "image-missing",
    "folder",
    "folder-open",
    "text-x-generic",
    "image-x-generic",
    "audio-x-generic",
    "video-x-generic",
    "application-x-addon",
    "system-run",
    "applications-other",
    "utilities-terminal",
    "accessories-text-editor",
    "preferences-system",
}


class IconUpdateBusy(ManagementError):
    pass


def _settings():
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup("org.gnome.desktop.interface", True) if source else None
    return Gio.Settings.new_full(schema, None, None) if schema else None


def _state_path():
    return data_home() / "housekeeper/app-icons.json"


@contextmanager
def _lock():
    path = data_home() / "housekeeper/app-icons.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise IconUpdateBusy(
                _("Application icons are being updated. Try again shortly.")
            ) from None
        yield


def _read_state():
    path = _state_path()
    if not path.exists():
        return {"version": 1, "theme": "", "base": "", "apps": {}}
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Too many overrides")
        state = json.loads(path.read_text())
        if state["version"] != 1 or not isinstance(state["apps"], dict):
            raise ValueError("Invalid icon state")
        for key in ("theme", "base"):
            if not isinstance(state[key], str):
                raise ValueError("Invalid theme")
        for app in state["apps"].values():
            if not isinstance(app["ids"], list) or not all(isinstance(v, str) for v in app["ids"]):
                raise ValueError("Invalid launcher IDs")
            if not isinstance(app["aliases"], list) or not all(
                NAME.fullmatch(v) for v in app["aliases"]
            ):
                raise ValueError("Invalid icon aliases")
            icon = Path(app["icon"])
            if icon.parent != data_home() / "housekeeper/icons" or not re.fullmatch(
                r"[0-9a-f]{64}\.png", icon.name
            ):
                raise ValueError("Invalid icon file")
        return state
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ManagementError(
            _("The saved application icon settings could not be read.")
        ) from error


def _prune_removed(state):
    """Drop overrides whose launchers are all gone, so reinstalls start clean."""
    from housekeeper.discovery import application_roots

    roots = application_roots()
    removed = [
        key
        for key, value in state["apps"].items()
        if not any((root / desktop_id).exists() for root in roots for desktop_id in value["ids"])
    ]
    for key in removed:
        del state["apps"][key]
    return bool(removed)


def has_app_icon(app):
    """Saved named-icon aliases can outlive the launcher markers that record them."""
    ids = {entry.desktop_id for entry in app.entries}
    try:
        return any(ids & set(value["ids"]) for value in _read_state()["apps"].values())
    except ManagementError:
        return False


def _aliases(app, missing_ok=False):
    names = set()
    for entry in app.entries:
        if missing_ok and not entry.path.exists():
            # Removed since the last scan; its names are no longer in use.
            continue
        keyfile = _load(entry.path)
        names.update((entry.desktop_id.removesuffix(".desktop"), _get(keyfile, "StartupWMClass")))
        names.add(_get(keyfile, ORIGINAL, _get(keyfile, "Icon")))
        names.add(entry.flatpak_id)
    names = {
        name
        for name in names
        if NAME.fullmatch(name) and name.removesuffix("-symbolic") not in GENERIC
    }
    # Panels can request symbolic variants even when the launcher uses a color icon.
    variants = {name + "-symbolic" for name in names if not name.endswith("-symbolic")}
    return names | {name for name in variants if NAME.fullmatch(name)}


def _make_theme(state, current):
    """Publish an immutable theme so Shell/GTK invalidate old named-icon caches."""
    base = state["base"] if current == state["theme"] else current
    if not base or base.startswith(THEME_PREFIX):
        base = "Adwaita"
    # Inherits is a list; accept one literal theme, never desktop-file syntax.
    if any(c in base for c in ",;\n\r\\/"):
        raise ManagementError(_("The current icon theme has an unsupported name."))
    state["base"] = base
    if not state["apps"]:
        state["theme"] = ""
        return base, None
    payload = json.dumps({"base": base, "apps": state["apps"]}, sort_keys=True).encode()
    name = THEME_PREFIX + hashlib.sha256(payload).hexdigest()[:24]
    root = data_home() / "icons"
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    if target.exists() or target.is_symlink():
        # A generation's exact manifest also prevents reusing unrelated directories.
        if target.is_symlink() or (target / "housekeeper.json").read_bytes() != payload:
            raise ManagementError(_("The application icon theme has changed unexpectedly."))
        state["theme"] = name
        return name, None
    staging = Path(tempfile.mkdtemp(prefix=".housekeeper-icons-", dir=root))
    try:
        icons = staging / "scalable/apps"
        icons.mkdir(parents=True)
        for app in state["apps"].values():
            for alias in app["aliases"]:
                (icons / (alias + ".png")).symlink_to(app["icon"])
        (staging / "index.theme").write_text(
            "[Icon Theme]\nName=Housekeeper Custom Icons\n"
            f"Inherits={base}\nDirectories=scalable/apps\n\n"
            "[scalable/apps]\nSize=512\nType=Scalable\nMinSize=1\nMaxSize=512\n"
            "Context=Applications\n",
            encoding="utf-8",
        )
        (staging / "housekeeper.json").write_bytes(payload)
        staging.chmod(0o755)
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    state["theme"] = name
    return name, target


def _prune_theme(name, keep):
    if name == keep or not re.fullmatch(THEME_PREFIX + r"[0-9a-f]{24}", name):
        return
    path = data_home() / "icons" / name
    # Remove only generated directories with a matching manifest digest.
    try:
        manifest = (path / "housekeeper.json").read_bytes()
        if (
            not path.is_symlink()
            and name == THEME_PREFIX + hashlib.sha256(manifest).hexdigest()[:24]
        ):
            shutil.rmtree(path)
    except OSError:
        pass


def save_app_icon(app, image, inventory, *, settings=None):
    """Change every launcher and unshared named icon of one application together.

    On failure, restore launcher bytes, the registry and the selected theme. Tests
    inject settings, and the UI runs this on its existing serialized worker.
    """
    with _lock():
        return _save_app_icon(app, image, inventory, settings=settings)


def _save_app_icon(app, image, inventory, *, settings=None):
    if not app.entries:
        raise ManagementError(_("No desktop launcher is available to edit."))
    settings = _settings() if settings is None else settings
    # Without a writable theme setting (no schema, or locked by an administrator),
    # still change the launchers; only the named-icon layer is unavailable.
    layer = settings is not None and settings.is_writable("icon-theme")
    if layer:
        current = settings.get_string("icon-theme")
        state = _read_state()
        previous_theme = state["theme"]
        _prune_removed(state)
    ids = sorted({entry.desktop_id for entry in app.entries})
    if layer:
        # Desktop IDs survive provider reclassification and icon-only user overrides.
        owner = hashlib.sha256("\n".join(ids).encode()).hexdigest()
        for key, value in list(state["apps"].items()):
            if set(value["ids"]) & set(ids):
                del state["apps"][key]
    if image is not None:
        icon = store_icon_image(image)
    targets = {}
    for entry in app.entries:
        target = _target(entry)
        targets[target] = (
            (target.read_bytes(), stat.S_IMODE(target.stat().st_mode)) if target.exists() else None
        )
    state_path = _state_path()
    before_state = state_path.read_bytes() if state_path.exists() else None
    changed = {}
    generated = None
    try:
        for entry in app.entries:
            if image is not None or can_reset_icon(entry):
                save_icon(entry, icon if image is not None else None)
                target = _target_path(entry)
                changed[target] = target.read_bytes() if target.exists() else None
        if layer and image is not None:
            saved = replace(app, entries=[replace(e, path=_target_path(e)) for e in app.entries])
            aliases = _aliases(saved)
            shared = set()
            # Shared names must keep the inherited icon for unrelated apps.
            for other in inventory:
                if not set(ids).intersection(entry.desktop_id for entry in other.entries):
                    try:
                        shared |= aliases.intersection(_aliases(other, missing_ok=True))
                    except (GLib.Error, OSError):
                        raise ManagementError(
                            _("An application changed. Refresh and try again.")
                        ) from None
            for value in state["apps"].values():
                shared |= aliases.intersection(value["aliases"])
            aliases -= shared
            for value in state["apps"].values():
                value["aliases"] = sorted(set(value["aliases"]) - shared)
            state["apps"][owner] = {"ids": ids, "aliases": sorted(aliases), "icon": str(icon)}
        if layer:
            theme, generated = _make_theme(state, current)
            if settings.get_string("icon-theme") != current:
                raise ManagementError(_("The icon theme changed while saving. Try again."))
            _atomic_write(state_path, json.dumps(state, sort_keys=True).encode())
            if not settings.set_string("icon-theme", theme):
                raise ManagementError(_("The desktop icon theme could not be updated."))
    except Exception:
        for target in reversed(changed):
            actual = target.read_bytes() if target.exists() else None
            if actual != changed[target]:
                # A concurrent editor owns these newer bytes; do not undo its work.
                continue
            before = targets[target]
            if before is None:
                target.unlink(missing_ok=True)
            else:
                _atomic_write(target, before[0], before[1])
        if before_state is None:
            state_path.unlink(missing_ok=True)
        else:
            _atomic_write(state_path, before_state)
        if generated:
            _prune_theme(generated.name, current)
        raise
    if layer:
        _prune_theme(previous_theme, theme)
    return tuple(targets)


def _target_path(entry):
    root = data_home() / "applications"
    return entry.path if entry.path.is_relative_to(root) else root / entry.desktop_id


def refresh_icon_theme(settings=None):
    """Rebase saved overrides after a theme change or when Housekeeper starts.

    No daemon is installed. Launcher overrides persist independently; direct
    name consumers regain the theme layer on the next Housekeeper activation.
    """
    if not _state_path().exists():
        return
    settings = _settings() if settings is None else settings
    if settings is None or not settings.is_writable("icon-theme"):
        return
    with _lock():
        state = _read_state()
        current = settings.get_string("icon-theme")
        before = _state_path().read_bytes()
        previous_theme = state["theme"]
        # Uninstalled apps release their names at the next activation.
        if not _prune_removed(state) and (not state["apps"] or current == state["theme"]):
            return
        theme, generated = _make_theme(state, current)
        try:
            if settings.get_string("icon-theme") != current:
                raise ManagementError(_("The icon theme changed while saving. Try again."))
            _atomic_write(_state_path(), json.dumps(state, sort_keys=True).encode())
            if not settings.set_string("icon-theme", theme):
                raise ManagementError(_("The desktop icon theme could not be updated."))
        except Exception:
            _atomic_write(_state_path(), before)
            if generated:
                _prune_theme(generated.name, current)
            raise
        _prune_theme(previous_theme, theme)
