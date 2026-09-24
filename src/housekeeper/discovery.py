"""Read desktop entries using XDG precedence without executing their commands."""

import os
import shutil
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from housekeeper.launch import parse_launch, resolve_executable
from housekeeper.models import DesktopEntry


def application_roots(env=None, home=None) -> list[Path]:
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    data_home = env.get("XDG_DATA_HOME") or str(home / ".local/share")
    data_dirs = (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    return list(
        dict.fromkeys(
            Path(p) / "applications" for p in [data_home, *data_dirs] if p and Path(p).is_absolute()
        )
    )


def read_entry(path: Path, root: Path, desktops: set[str] | None = None) -> DesktopEntry:
    desktops = (
        set(os.environ.get("XDG_CURRENT_DESKTOP", "").split(":")) if desktops is None else desktops
    )
    keyfile = GLib.KeyFile()
    keyfile.load_from_file(str(path), GLib.KeyFileFlags.NONE)

    def string(key, default=""):
        try:
            return keyfile.get_string("Desktop Entry", key)
        except GLib.Error:
            return default

    def boolean(key):
        try:
            return keyfile.get_boolean("Desktop Entry", key)
        except GLib.Error:
            return False

    if string("Type", "Application") != "Application":
        raise ValueError("Not an application entry")
    try:
        name = keyfile.get_locale_string("Desktop Entry", "Name", None)
    except GLib.Error:
        name = path.stem
    command = string("Exec")
    try:
        argv = tuple(GLib.shell_parse_argv(command)[1]) if command else ()
    except GLib.Error:
        argv = ()
    launch = parse_launch(argv)
    if launch.wrappers:
        wrapper = shutil.which(argv[0])
        expected = shutil.which("env", path=os.defpath)
        if not wrapper or not expected or Path(wrapper).resolve() != Path(expected).resolve():
            launch = replace(launch, reason="The env wrapper is not the system command.")
    executable = resolve_executable(launch)
    resolved = str(Path(executable).resolve()) if executable else ""
    reasons = []
    if boolean("Hidden"):
        reasons.append("Hidden by a desktop entry override")
    if boolean("NoDisplay"):
        reasons.append("Hidden or auxiliary entry")
    only = set(filter(None, string("OnlyShowIn").split(";")))
    excluded = set(filter(None, string("NotShowIn").split(";")))
    if only and not only.intersection(desktops):
        reasons.append("Not shown in this desktop environment")
    if excluded.intersection(desktops):
        reasons.append("Excluded from this desktop environment")
    if string("TryExec") and not shutil.which(string("TryExec")):
        reasons.append("Required executable is unavailable")
    dbus = boolean("DBusActivatable")
    if (not argv or not executable or not os.access(executable, os.X_OK)) and not dbus:
        reasons.append("Application executable is unavailable")
    try:
        info = Gio.DesktopAppInfo.new_from_filename(str(path))
    except TypeError:
        info = None
    if info is not None and not info.should_show() and not reasons:
        reasons.append("Not shown by the current desktop")
    desktop_id = str(path.relative_to(root)).replace(os.sep, "-")
    return DesktopEntry(
        desktop_id=desktop_id,
        path=path,
        root=root,
        name=name,
        command=command,
        argv=argv,
        icon=string("Icon", "application-x-executable"),
        visible=not reasons,
        reason="; ".join(reasons),
        executable=executable,
        resolved_executable=resolved,
        flatpak_id=string("X-Flatpak"),
        dbus_activatable=dbus,
        launch=launch,
        housekeeper_created=boolean("X-Housekeeper-Created"),
    )


def scan_entries(roots=None, desktops=None) -> tuple[list[DesktopEntry], list[str]]:
    roots = application_roots() if roots is None else roots
    entries, warnings, seen = [], [], set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            paths = sorted(root.rglob("*.desktop"))
        except OSError:
            warnings.append(f"Could not read application directory: {root}")
            continue
        for path in paths:
            desktop_id = str(path.relative_to(root)).replace(os.sep, "-")
            if desktop_id in seen:
                continue
            seen.add(desktop_id)
            try:
                entries.append(read_entry(path, root, desktops))
            except (GLib.Error, OSError, ValueError) as error:
                warnings.append(f"Could not read {path.name}: {error}")
    return entries, warnings
