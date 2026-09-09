"""Read-only Flatpak launch relationships, independent of transaction APIs."""

import os
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from housekeeper.identity import digest
from housekeeper.launch import parse_launch as parse_command
from housekeeper.models import AppRecord, DesktopEntry

FIELDS = {"%u", "%U", "%f", "%F", "%i", "%c", "%k"}
MANAGER_SEARCH_PATH = os.defpath


@dataclass(frozen=True)
class FlatpakLaunch:
    app_id: str
    branch: str = ""
    arch: str = ""
    scope: str = ""
    installation: str = ""
    command: str = ""
    arguments: tuple[str, ...] = ()
    options: tuple[tuple[str, str], ...] = ()


def parse_launch(entry: DesktopEntry) -> FlatpakLaunch | None:
    spec = entry.launch or parse_command(entry.argv)
    if spec.reason:
        return None
    argv = spec.argv
    if len(argv) < 3 or Path(argv[0]).name != "flatpak" or argv[1] != "run":
        return None
    values: dict[str, str] = {}
    flags = {"--user", "-u", "--system", "--file-forwarding"}
    options = {"--branch", "--arch", "--installation", "--command"}
    index = 2
    while index < len(argv) and argv[index].startswith("-"):
        key, equal, value = argv[index].partition("=")
        if key == "--":
            index += 1
            break
        if key == "-u":
            key = "--user"
        if key in flags and not equal:
            value = "true"
        elif key in options:
            if not equal:
                index += 1
                if index == len(argv):
                    return None
                value = argv[index]
            if not value or value.startswith("-"):
                return None
        else:
            return None
        if key in values and values[key] != value:
            return None
        values[key] = value
        index += 1
    if index == len(argv):
        return None
    selectors = [k for k in ("--user", "--system", "--installation") if k in values]
    if len(selectors) > 1:
        return None
    ref = argv[index].removeprefix("app/").split("/")
    if len(ref) not in {1, 3} or not ref[0] or "." not in ref[0]:
        return None
    arch, branch = values.get("--arch", ""), values.get("--branch", "")
    if len(ref) == 3:
        if (arch and arch != ref[1]) or (branch and branch != ref[2]):
            return None
        arch, branch = ref[1:]
    return FlatpakLaunch(
        ref[0],
        branch,
        arch,
        "User" if "--user" in values else "System" if "--system" in values else "",
        values.get("--installation", ""),
        values.get("--command", ""),
        argv[index + 1 :],
        tuple(sorted(values.items())),
    )


def select_installation(launch: FlatpakLaunch, apps: list[AppRecord]) -> AppRecord | None:
    """Resolve desktop and activation commands with the same installation rules."""
    matches = [a for a in apps if a.metadata["app_id"] == launch.app_id]
    if launch.branch:
        matches = [a for a in matches if a.identity.split("/")[-1] == launch.branch]
    if launch.arch:
        matches = [a for a in matches if a.identity.split("/")[-2] == launch.arch]
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
    elif any("current" in a.metadata for a in matches):
        matches = [a for a in matches if a.metadata.get("current") == "true"]
    if launch.arch:
        matches = [a for a in matches if a.identity.split("/")[-2] == launch.arch]
    if len(matches) != 1:
        return None
    return matches[0]


def exported_signature(entry: DesktopEntry, app: AppRecord, launch: FlatpakLaunch) -> str:
    """Only a matching current deployment export can authorize custom app arguments."""
    from housekeeper.appearance import equivalent_launcher
    from housekeeper.discovery import read_entry

    if not app.location:
        return ""
    root = Path(app.location) / "export/share/applications"
    try:
        for path in sorted(root.rglob("*.desktop")):
            if not path.resolve().is_relative_to(root.resolve()):
                continue
            exported = read_entry(path, root)
            if (
                parse_launch(exported) != launch
                or exported.dbus_activatable != entry.dbus_activatable
            ):
                continue
            # Exec is only the fallback for D-Bus activation. Establish ownership
            # from the installed export, including the desktop ID (the bus name)
            # and all other semantics, rather than interpreting that fallback as
            # the actual launch. Icon-only XDG overrides retain this evidence.
            if entry.dbus_activatable and (
                exported.desktop_id != entry.desktop_id or not equivalent_launcher(entry.path, path)
            ):
                continue
            # The environment and executable are part of the launch, too.
            if (
                exported.argv != entry.argv
                or exported.resolved_executable != entry.resolved_executable
            ):
                continue
            return digest(str(path), path.read_bytes().hex())
    except (OSError, ValueError, RuntimeError):
        return ""
    return ""


def activation_signature(entry: DesktopEntry, app: AppRecord, apps: list[AppRecord]) -> str:
    """Bind the effective service, its exported entry point and selected installation."""
    import configparser

    from gi.repository import GLib

    from housekeeper.dbus_services import effective_service, read_service, service_roots

    bus_name = entry.desktop_id.removesuffix(".desktop")
    try:
        effective = effective_service(bus_name)
        if effective is None:
            return ""
        root = Path(app.location) / "export/share/dbus-1/services"
        exported = read_service(root / (bus_name + ".service"))
        if not exported.resolved.is_relative_to(root.resolve()):
            return ""
        parsed = []
        for service in (effective, exported):
            values = dict(service.values)
            if values.get("Name") != bus_name or "SystemdService" in values or "User" in values:
                return ""
            argv = tuple(GLib.shell_parse_argv(values.get("Exec", ""))[1])
            manager = shutil.which("flatpak", path=MANAGER_SEARCH_PATH)
            if not argv or not Path(argv[0]).is_absolute() or not manager:
                return ""
            if Path(argv[0]).resolve() != Path(manager).resolve():
                return ""
            launch = parse_launch(replace(entry, argv=argv, launch=parse_command(argv)))
            if launch is None:
                return ""
            target = select_installation(launch, apps)
            if target is None or (target.metadata["installation"], target.identity) != (
                app.metadata["installation"],
                app.identity,
            ):
                return ""
            # Scope/ref spelling may differ, but custom commands, guest arguments,
            # or extra activation fields cannot replace the installed entry point.
            parsed.append(
                (
                    launch.command,
                    launch.arguments,
                    tuple(
                        (k, v)
                        for k, v in launch.options
                        if k not in {"--user", "--system", "--installation", "--arch", "--branch"}
                    ),
                    {k: v for k, v in values.items() if k != "Exec"},
                )
            )
        if parsed[0] != parsed[1]:
            return ""
        return digest(
            bus_name,
            service_roots(),
            effective,
            exported,
            app.metadata["installation"],
            app.identity,
        )
    except (OSError, ValueError, RuntimeError, UnicodeError, configparser.Error, GLib.Error):
        return ""


def binding(
    entry: DesktopEntry, app: AppRecord, launch: FlatpakLaunch, apps: list[AppRecord]
) -> str:
    manager = shutil.which("flatpak", path=MANAGER_SEARCH_PATH)
    if (
        not manager
        or not entry.resolved_executable
        or Path(entry.resolved_executable) != Path(manager).resolve()
    ):
        return ""
    exported = exported_signature(entry, app, launch)
    if not exported and (
        entry.dbus_activatable or launch.command or any(a not in FIELDS for a in launch.arguments)
    ):
        return ""
    activation = activation_signature(entry, app, apps) if entry.dbus_activatable else ""
    if entry.dbus_activatable and not activation:
        return ""
    # A path inside another installation is contradictory evidence, not a selector.
    return digest(
        entry.argv,
        entry.executable,
        entry.resolved_executable,
        entry.flatpak_id,
        entry.dbus_activatable,
        app.metadata["installation"],
        app.identity,
        exported,
        activation,
    )
