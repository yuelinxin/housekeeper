#!/usr/bin/python3
"""Write the desktop entry, offering only the native package files this build installs."""

import sys

PACKAGE_TYPES = {
    "rpm": "application/x-rpm",
    "deb": "application/vnd.debian.binary-package",
}


def main():
    source, output, package_format = sys.argv[1:]
    dropped = {
        mime for family, mime in PACKAGE_TYPES.items() if package_format not in {"all", family}
    }
    lines = []
    with open(source, encoding="utf-8") as stream:
        for line in stream.read().splitlines():
            if line.startswith("MimeType="):
                kept = [mime for mime in line[9:].split(";") if mime and mime not in dropped]
                line = "MimeType=" + "".join(mime + ";" for mime in kept)
            lines.append(line)
    with open(output, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
