#!/usr/bin/python3
"""Build a clean source archive from tracked and non-ignored project files."""

import argparse
import runpy
import subprocess
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = runpy.run_path(str(root / "src/housekeeper/__init__.py"))["VERSION"]
parser = argparse.ArgumentParser()
parser.add_argument(
    "output", type=Path, nargs="?", default=Path(f"dist/sources/housekeeper-{version}.tar.gz")
)
args = parser.parse_args()
paths = (
    subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
    )
    .decode()
    .split("\0")
)
args.output.parent.mkdir(parents=True, exist_ok=True)
with tarfile.open(args.output, "w:gz") as archive:
    for relative in sorted(set(filter(None, paths))):
        path = root / relative
        if path.is_file() and path.resolve() != args.output.resolve():
            archive.add(path, arcname=f"housekeeper-{version}/" + relative, recursive=False)
print(args.output)
