"""Bounded launch parsing. No expansion, shell evaluation, or application execution."""

import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path

from housekeeper.models import LaunchSpec

ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CONTEXT = {
    "HOME",
    "XDG_DATA_HOME",
    "XDG_DATA_DIRS",
    "XDG_RUNTIME_DIR",
    "FLATPAK_USER_DIR",
    "FLATPAK_SYSTEM_DIR",
    "FLATPAK_SYSTEM_CACHE_DIR",
    "FLATPAK_CONFIG_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
}


def parse_launch(argv: tuple[str, ...]) -> LaunchSpec:
    if not argv:
        return LaunchSpec((), reason="The launch command is missing or invalid.")
    if Path(argv[0]).name != "env":
        reason = (
            "Shell command evaluation is not supported."
            if Path(argv[0]).name in {"sh", "bash", "dash", "zsh", "fish"}
            and any(a in {"-c", "-lc"} for a in argv[1:])
            else ""
        )
        return LaunchSpec(argv, reason=reason)
    index, clear = 1, False
    unset: set[str] = set()
    values: dict[str, str] = {}
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            index += 1
            break
        if arg in {"-i", "--ignore-environment"}:
            clear = True
        elif arg == "-u" or arg == "--unset" or arg.startswith("--unset="):
            if arg.startswith("--unset="):
                name = arg.split("=", 1)[1]
            else:
                index += 1
                if index == len(argv):
                    return LaunchSpec(
                        (), wrappers=("env",), reason="The env unset option needs a variable name."
                    )
                name = argv[index]
            if not NAME.fullmatch(name):
                return LaunchSpec((), wrappers=("env",), reason="The env variable name is invalid.")
            unset.add(name)
        elif arg.startswith("-"):
            return LaunchSpec((), wrappers=("env",), reason="This env option is not supported.")
        else:
            break
        index += 1
    while index < len(argv) and ASSIGNMENT.match(argv[index]):
        key, value = argv[index].split("=", 1)
        values[key] = value
        index += 1
    effective = argv[index:]
    reason = ""
    if not effective or effective[0].startswith("-"):
        reason = "The env command is missing or unsupported."
        effective = ()
    elif Path(effective[0]).name == "env":
        reason = "Nested env commands are not supported."
    elif (set(values) | unset) & CONTEXT:
        reason = "This environment changes the installation or activation context."
    elif clear and Path(effective[0]).name in {"flatpak", "gapplication", "snap"}:
        reason = "The cleared environment does not establish an installation or activation context."
    elif "PATH" in values and any(
        not Path(part).is_absolute() for part in values["PATH"].split(os.pathsep)
    ):
        reason = "A relative PATH entry needs a working-directory context."
    return LaunchSpec(
        effective, tuple(sorted(values.items())), tuple(sorted(unset)), clear, ("env",), reason
    )


def effective_environment(
    spec: LaunchSpec, environment: Mapping[str, str] | None = None
) -> dict[str, str]:
    result = (
        {} if spec.clear_environment else dict(os.environ if environment is None else environment)
    )
    for key in spec.unset:
        result.pop(key, None)
    result.update(spec.environment)
    return result


def resolve_executable(spec: LaunchSpec, environment: Mapping[str, str] | None = None) -> str:
    if not spec.argv:
        return ""
    binary = spec.argv[0]
    if Path(binary).is_absolute():
        return binary
    path = effective_environment(spec, environment).get("PATH", os.defpath)
    return shutil.which(binary, path=path) or ""
