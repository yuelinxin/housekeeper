"""Format-identifiable ELF fixture bytes. Never execute these test files."""

import struct


def image_bytes(kind=2):
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    header[8:11] = b"AI" + bytes([kind])
    struct.pack_into("<HHI", header, 16, 2, 62, 1)
    struct.pack_into("<HHH", header, 52, 64, 56, 0)
    return bytes(header)
