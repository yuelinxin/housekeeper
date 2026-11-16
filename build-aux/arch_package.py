#!/usr/bin/python3
"""Prepare a PKGBUILD with an exact checksum for a clean local source archive."""

import argparse
import hashlib
import runpy
import shutil
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    version = runpy.run_path(str(root / "src/housekeeper/__init__.py"))["VERSION"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "dist/arch")
    args = parser.parse_args()
    source = root / "dist/sources" / f"housekeeper-{version}.tar.gz"
    if not source.is_file():
        parser.error("Run python3 build-aux/source_archive.py first.")
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    template = (root / "packaging/arch/PKGBUILD.in").read_text()
    pkgbuild = template.replace("@VERSION@", version).replace("@SOURCE_SHA256@", checksum)
    args.output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, args.output / f"housekeeper-{version}-arch.tar.gz")
    (args.output / "PKGBUILD").write_text(pkgbuild)
    print(args.output)


if __name__ == "__main__":
    main()
