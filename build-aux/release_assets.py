#!/usr/bin/python3
"""Check that a release directory holds exactly the validated packages for one version."""

import argparse
import hashlib
import sys
from pathlib import Path

FEDORA = (43, 44, 45)
DEBIAN = ("ubuntu24.04", "debian13")


def expected_assets(version, revision="1"):
    names = {f"housekeeper-{version}.tar.gz"}
    for fedora in FEDORA:
        names.add(f"housekeeper-{version}-{revision}.fc{fedora}.noarch.rpm")
        names.add(f"housekeeper-{version}-{revision}.fc{fedora}.src.rpm")
    for target in DEBIAN:
        names.add(f"housekeeper_{version}-{revision}+{target}_all.deb")
    names |= {
        f"housekeeper-{version}-{revision}-any.pkg.tar.zst",
        f"housekeeper-{version}-arch.tar.gz",
        "PKGBUILD",
    }
    return names


def check(directory, version):
    present = {path.name for path in directory.iterdir() if path.is_file()} - {"SHA256SUMS"}
    expected = expected_assets(version)
    problems = []
    if missing := sorted(expected - present):
        problems.append("Missing release assets: " + ", ".join(missing))
    if unexpected := sorted(present - expected):
        problems.append("Unexpected release assets: " + ", ".join(unexpected))
    if not problems:
        pkgbuild = (directory / "PKGBUILD").read_text()
        if f"pkgver={version}\n" not in pkgbuild:
            problems.append("PKGBUILD does not build this version")
        # Every job builds the same reproducible archive; the PKGBUILD names its own copy.
        source = (directory / f"housekeeper-{version}.tar.gz").read_bytes()
        arch = (directory / f"housekeeper-{version}-arch.tar.gz").read_bytes()
        if source != arch:
            problems.append("The Arch source archive differs from the release source archive")
        digest = hashlib.sha256(arch).hexdigest()
        if f"sha256sums=('{digest}')" not in pkgbuild:
            problems.append("PKGBUILD checksum does not match its source archive")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("version")
    args = parser.parse_args()
    problems = check(args.directory, args.version)
    for problem in problems:
        print(f"::error::{problem}", file=sys.stderr)
    if problems:
        sys.exit(1)
    for name in sorted(expected_assets(args.version)):
        print(name)


if __name__ == "__main__":
    main()
