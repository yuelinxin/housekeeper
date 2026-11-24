#!/usr/bin/python3
"""Print one version's CHANGELOG section followed by installation notes."""

import argparse
import re
import sys
from pathlib import Path

INSTALL = """\
## Installation

- **Fedora 43, 44 and 45:** `sudo dnf copr enable yuelinxin/housekeeper` and
  `sudo dnf install housekeeper`. The attached RPMs match the COPR packages.
- **Ubuntu 24.04 / Debian 13:** download the `.deb` for your distribution and run
  `sudo apt install ./housekeeper_{version}-1+<distribution>_all.deb`.
- **Arch Linux:** after `sudo pacman -Syu`, run
  `sudo pacman -U ./housekeeper-{version}-1-any.pkg.tar.zst`, or build it with the
  attached `PKGBUILD` and `housekeeper-{version}-arch.tar.gz` using `makepkg -si`.

Ubuntu, Debian and Arch packages do not add a repository; install a newer release the
same way to update. Verify downloads with `sha256sum -c SHA256SUMS --ignore-missing`.
"""


def section(changelog, version):
    match = re.search(
        rf"^## {re.escape(version)}(?:[ \t][^\n]*)?\n(.*?)(?=^## |\Z)", changelog, re.M | re.S
    )
    if match is None or not match[1].strip():
        raise ValueError(f"CHANGELOG.md has no entry for {version}")
    return match[1].strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    args = parser.parse_args()
    try:
        notes = section(args.changelog.read_text(encoding="utf-8"), args.version)
    except ValueError as error:
        sys.exit(str(error))
    print(notes + "\n\n" + INSTALL.format(version=args.version), end="")


if __name__ == "__main__":
    main()
