#!/usr/bin/python3
"""Build a clean, reproducible source archive from tracked and non-ignored project files.

Release jobs build the archive independently, so its bytes must depend only on the
file contents: timestamps come from the last commit (or SOURCE_DATE_EPOCH), and
ownership, permissions and the gzip header are normalized.
"""

import argparse
import gzip
import io
import os
import runpy
import stat
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
epoch = os.environ.get("SOURCE_DATE_EPOCH") or subprocess.check_output(
    ["git", "log", "-1", "--format=%ct"], cwd=root, text=True
)
mtime = int(epoch.strip())


def normalized(info):
    info.mtime = mtime
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o755 if info.mode & stat.S_IXUSR else 0o644
    return info


args.output.parent.mkdir(parents=True, exist_ok=True)
buffer = io.BytesIO()
with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
    for relative in sorted(set(filter(None, paths))):
        path = root / relative
        if path.is_file() and path.resolve() != args.output.resolve():
            archive.add(
                path,
                arcname=f"housekeeper-{version}/" + relative,
                recursive=False,
                filter=normalized,
            )
with args.output.open("wb") as stream:
    with gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0, compresslevel=9) as gz:
        gz.write(buffer.getvalue())
print(args.output)
