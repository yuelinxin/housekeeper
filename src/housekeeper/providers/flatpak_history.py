"""Read Flatpak deployment dates from its structured local journal records."""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from collections import defaultdict

from housekeeper.sorting import package_timestamp

LOG = logging.getLogger(__name__)
MESSAGE_ID = "c7b39b1e006b464599465e105b361485"
OPERATIONS = ("deploy install", "deploy update", "uninstall")
MAX_RECORDS = 5000
MAX_BYTES = 8 * 1024 * 1024


def read_history():
    """One bounded, offline query per inventory; no elevated journal access."""
    command = shutil.which("journalctl")
    if command is None:
        return []
    try:
        # Do not capture an arbitrarily large journal response in memory.
        with tempfile.TemporaryFile() as output:
            subprocess.run(
                [
                    command,
                    "--no-pager",
                    "--quiet",
                    "--output=json",
                    "--reverse",
                    f"--lines={MAX_RECORDS}",
                    "--output-fields=__REALTIME_TIMESTAMP,OPERATION,REF,INSTALLATION,COMMIT,_UID",
                    f"MESSAGE_ID={MESSAGE_ID}",
                    *(f"OPERATION={operation}" for operation in OPERATIONS),
                ],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=3,
                env={**os.environ, "LC_ALL": "C"},
            )
            output.seek(0)
            payload = output.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            return []
        records = []
        for line in payload.splitlines():
            try:
                record = json.loads(line)
                if isinstance(record, dict):
                    records.append(record)
            except (ValueError, UnicodeError, RecursionError):
                continue
        return records
    except (OSError, subprocess.SubprocessError):
        LOG.debug("Flatpak update history unavailable", exc_info=True)
        return []


def journal_installation(installation):
    """Mirror Flatpak's journal names, which differ from libflatpak IDs."""
    if installation.get_is_user():
        return "user"
    identity = installation.get_id()
    if not identity or identity == "default":
        return "system"
    return f"system ({identity})"


def enrich_history(apps, installations):
    if not apps:
        return
    for app in apps:
        app.updated_at = None
    paths = defaultdict(list)
    for path, installation in installations.items():
        try:
            paths[journal_installation(installation)].append(path)
        except Exception:
            LOG.debug("Flatpak journal installation identity unavailable", exc_info=True)
    targets = {}
    for name, matches in paths.items():
        if len(matches) != 1:
            continue
        for app in apps:
            if app.metadata.get("installation") == matches[0]:
                targets[name, app.identity] = app
    if not targets:
        return
    seen = set()
    for event in read_history():
        name, ref = event.get("INSTALLATION"), event.get("REF")
        if not isinstance(name, str) or not isinstance(ref, str):
            continue
        if name == "user" and event.get("_UID") != str(os.getuid()):
            continue
        key = name, ref
        app = targets.get(key)
        operation = event.get("OPERATION")
        if app is None or key in seen or operation not in OPERATIONS:
            continue
        # Newest event wins, including removals or a different commit. Never fall
        # back to a stale deployment of the same commit before an uninstall.
        seen.add(key)
        app.updated_at = None
        if operation == "uninstall" or not app.metadata.get("commit"):
            continue
        if event.get("COMMIT") != app.metadata["commit"]:
            continue
        timestamp = event.get("__REALTIME_TIMESTAMP")
        if (
            isinstance(timestamp, str)
            and timestamp.isascii()
            and timestamp.isdecimal()
            and len(timestamp) <= 18
        ):
            app.updated_at = package_timestamp(int(timestamp) // 1_000_000)
