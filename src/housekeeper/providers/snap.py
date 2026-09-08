"""Query installed Snap metadata through snapd's local read-only HTTP API."""

import http.client
import json
import socket
from pathlib import Path

from housekeeper.models import Source
from housekeeper.providers.packages import InstalledPackage, byte_size
from housekeeper.sorting import package_timestamp

SNAP_SOCKET = Path("/run/snapd.socket")


class SnapConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(SNAP_SOCKET))


def snap_packages():
    if not SNAP_SOCKET.exists():
        return []
    connection = SnapConnection("localhost", timeout=3)
    try:
        connection.request("GET", "/v2/snaps")
        response = connection.getresponse()
        payload = response.read(16 * 1024 * 1024 + 1)
        if response.status != 200 or len(payload) > 16 * 1024 * 1024:
            raise ValueError("Snap metadata is unavailable")
        body = json.loads(payload)
        if (
            not isinstance(body, dict)
            or body.get("type") != "sync"
            or not isinstance(body.get("result"), list)
        ):
            raise ValueError("Invalid Snap metadata response")
    finally:
        connection.close()
    result = []
    for snap in body["result"]:
        if not isinstance(snap, dict) or not isinstance(snap.get("apps", []), list):
            continue
        if snap.get("status") != "active" or snap.get("type") != "app":
            continue
        if not all(
            isinstance(snap.get(key), str) and snap[key] for key in ("name", "version", "revision")
        ):
            continue
        desktops = tuple(
            app["desktop-file"]
            for app in snap.get("apps", [])
            if isinstance(app, dict)
            and isinstance(app.get("desktop-file"), str)
            and Path(app["desktop-file"]).is_absolute()
            and app["desktop-file"].endswith(".desktop")
        )
        result.append(
            InstalledPackage(
                Source.SNAP,
                snap["name"],
                snap["version"],
                "",
                str(SNAP_SOCKET),
                byte_size(snap.get("installed-size")),
                desktops,
                snap["revision"],
                package_timestamp(snap.get("install-date")),
            )
        )
    return result
