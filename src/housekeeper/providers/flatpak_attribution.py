"""Read-only Flatpak launch relationships, independent of transaction APIs."""

from dataclasses import dataclass
from pathlib import Path

from housekeeper.identity import digest
from housekeeper.launch import parse_launch as parse_command
from housekeeper.models import AppRecord, DesktopEntry

FIELDS = {"%u", "%U", "%f", "%F", "%i", "%c", "%k"}


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


def exported_signature(entry: DesktopEntry, app: AppRecord, launch: FlatpakLaunch) -> str:
    """Only a matching current deployment export can authorize custom app arguments."""
    from housekeeper.appearance import verified_icon_source
    from housekeeper.discovery import read_entry

    if not app.location:
        return ""
    root = Path(app.location) / "export/share/applications"
    try:
        for path in sorted(root.rglob("*.desktop")):
            if not path.resolve().is_relative_to(root.resolve()):
                continue
            exported = read_entry(path, root)
            if parse_launch(exported) != launch:
                continue
            # The environment and executable are part of the launch, too.
            if (
                exported.argv != entry.argv
                or exported.resolved_executable != entry.resolved_executable
            ):
                continue
            source = verified_icon_source(entry.path)
            return digest(str(path), path.read_bytes().hex(), str(source))
    except (OSError, ValueError, RuntimeError):
        return ""
    return ""


def binding(entry: DesktopEntry, app: AppRecord, launch: FlatpakLaunch) -> str:
    exported = exported_signature(entry, app, launch)
    if not exported and (launch.command or any(a not in FIELDS for a in launch.arguments)):
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
    )
