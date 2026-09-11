"""Identify processes running the files a transaction would replace.

Only paths are compared. Process memory, command lines and environments are never
read, and processes belonging to other users stay invisible when procfs denies them.
"""

import os
from pathlib import Path

PROC = Path("/proc")


def _pids():
    try:
        return [entry for entry in os.listdir(PROC) if entry.isdigit()]
    except OSError:
        return []


def _executable(pid):
    try:
        target = os.readlink(PROC / pid / "exe")
    except OSError:
        return ""  # A denied or exited process cannot be attributed to a package.
    # An already replaced binary keeps running from its unlinked inode.
    return target.removesuffix(" (deleted)")


def _mapped(pid):
    paths = set()
    try:
        with (PROC / pid / "maps").open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                fields = line.rstrip("\n").split(" ", 5)
                if len(fields) != 6:
                    continue
                path = fields[5].strip().removesuffix(" (deleted)")
                if path.startswith("/"):
                    paths.add(path)
    except OSError:
        return paths
    return paths


def running_files(*, mapped=True):
    """Executables and mapped files of every readable process, with their process ids."""
    executing: dict[str, set[str]] = {}
    resident: dict[str, set[str]] = {}
    for pid in _pids():
        executable = _executable(pid)
        if executable:
            executing.setdefault(executable, set()).add(pid)
        if not mapped:
            continue
        for path in _mapped(pid):
            if path != executable:
                resident.setdefault(path, set()).add(pid)
    return executing, resident


def affected(paths, *, mapped=True, files=None):
    """Split the transaction's paths into executing binaries and other in-use files."""
    paths = set(paths)
    if not paths:
        return (), ()
    executing, resident = running_files(mapped=mapped) if files is None else files
    running = sorted(paths & executing.keys())
    in_use = sorted((paths & resident.keys()) - set(running))
    return tuple(running), tuple(in_use)


def process_count(paths, files):
    """How many distinct processes hold any of these paths open."""
    executing, resident = files
    pids: set[str] = set()
    for path in paths:
        pids |= executing.get(path, set()) | resident.get(path, set())
    return len(pids)
