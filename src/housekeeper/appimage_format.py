"""Read AppImage type markers from a structurally valid ELF header, without running it."""

import os
import stat
import struct
from pathlib import Path


def appimage_format(path: Path) -> int | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return None
            header = stream.read(64)
        if (
            len(header) < 52
            or header[:4] != b"\x7fELF"
            or header[4:7]
            not in {
                b"\x01\x01\x01",
                b"\x01\x02\x01",
                b"\x02\x01\x01",
                b"\x02\x02\x01",
            }
            or header[8:11] not in {b"AI\x01", b"AI\x02"}
        ):
            return None
        endian = "<" if header[5] == 1 else ">"
        bits64 = header[4] == 2
        size = 64 if bits64 else 52
        if len(header) < size:
            return None
        kind, machine, version = struct.unpack_from(endian + "HHI", header, 16)
        offset = 52 if bits64 else 40
        ehsize, phsize, phnum = struct.unpack_from(endian + "HHH", header, offset)
        phoff = struct.unpack_from(endian + ("Q" if bits64 else "I"), header, 32 if bits64 else 28)[
            0
        ]
        if kind not in {2, 3} or not machine or version != 1 or ehsize != size:
            return None
        if phnum and (
            phsize != (56 if bits64 else 32)
            or phoff < size
            or phoff + phsize * phnum > info.st_size
        ):
            return None
        return header[10]
    except (OSError, ValueError, struct.error):
        return None
