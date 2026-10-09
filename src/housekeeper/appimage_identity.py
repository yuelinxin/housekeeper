"""Read desktop identity from type-2 AppImages without executing their runtime."""

import os
import re
import selectors
import shutil
import stat
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from gi.repository import GLib

from housekeeper.appimage_format import appimage_format

LIMIT = 64 * 1024
TIMEOUT = 5
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")


@dataclass(frozen=True)
class AppImageIdentity:
    desktop_id: str
    wm_class: str


def _read_command(argv):
    """Bound both output and elapsed time; never extract files onto the host."""
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            result = bytearray()
            deadline = time.monotonic() + TIMEOUT
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise ValueError("AppImage metadata read timed out")
                    chunk = os.read(process.stdout.fileno(), 8192)
                    if not chunk:
                        break
                    result.extend(chunk)
                    if len(result) > LIMIT:
                        raise ValueError("AppImage metadata is too large")
            if process.wait(timeout=max(0.01, deadline - time.monotonic())):
                raise ValueError("Cannot read AppImage metadata")
            return result.decode("utf-8")
        finally:
            if process.poll() is None:
                process.kill()


def read_appimage_identity(path):
    """Return one unambiguous embedded launcher, or None when unavailable.

    Only the filename and StartupWMClass are used. Embedded Exec, activation,
    actions and permissions are never imported. unsquashfs is optional.
    """
    path = Path(path)
    if appimage_format(path) != 2:
        return None
    reader = shutil.which("unsquashfs")
    if not reader:
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return None
            header = stream.read(64)
            if len(header) < 64:
                return None
            bits64 = header[4] == 2
            endian = "<" if header[5] == 1 else ">"
            offset = struct.unpack_from(
                endian + ("Q" if bits64 else "I"), header, 40 if bits64 else 32
            )[0]
            size, count = struct.unpack_from(endian + "HH", header, 58 if bits64 else 46)
            if not count or size != (64 if bits64 else 40):
                return None
            offset += size * count
            if not 64 <= offset <= info.st_size - 96:
                return None
            stream.seek(offset)
            if stream.read(4) != b"hsqs":
                return None
        command = [reader, "-processors", "1", "-offset", str(offset)]
        listing = _read_command([*command, "-ll", "-max-depth", "1", str(path)])
        names = []
        for line in listing.splitlines():
            # Accept only a regular root desktop file, without symlink traversal.
            if line.startswith("-") and " squashfs-root/" in line:
                name = line.split(" squashfs-root/", 1)[1]
                if name.endswith(".desktop") and NAME.fullmatch(name):
                    names.append(name)
        if len(names) != 1:
            return None
        contents = _read_command([*command, "-cat", str(path), names[0]])
        keyfile = GLib.KeyFile()
        keyfile.load_from_data(contents, len(contents.encode()), GLib.KeyFileFlags.NONE)
        if keyfile.get_string("Desktop Entry", "Type") != "Application":
            return None
        try:
            wm_class = keyfile.get_string("Desktop Entry", "StartupWMClass")
        except GLib.Error:
            wm_class = ""
        # An empty key is the same as a missing one.
        wm_class = wm_class or names[0].removesuffix(".desktop")
        if len(wm_class) > 200 or any(ord(c) < 32 for c in wm_class):
            return None
        return AppImageIdentity(names[0], wm_class)
    except (OSError, ValueError, struct.error, GLib.Error, subprocess.SubprocessError):
        return None
