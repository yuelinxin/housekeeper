#!/usr/bin/python3
"""Download one published release SRPM and verify its release checksum."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


def select_srpm(release, tag):
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("Expected a stable release tag such as v0.1.16")
    if release.get("tagName") != tag:
        raise ValueError("Release tag does not match the requested tag")
    if release.get("isDraft", True) or release.get("isPrerelease", True):
        raise ValueError("COPR submission requires a published, non-prerelease release")
    names = [asset["name"] for asset in release.get("assets", [])]
    if names.count("SHA256SUMS") != 1:
        raise ValueError("Release must include SHA256SUMS from the packaging workflow")
    pattern = re.compile(rf"housekeeper-{re.escape(tag[1:])}-([0-9]+)\.fc([0-9]+)\.src\.rpm")
    candidates = [(name, pattern.fullmatch(name)) for name in names]
    candidates = [(name, match) for name, match in candidates if match is not None]
    if not candidates:
        raise ValueError(
            "Release has no matching SRPM; attach the validated release packages first"
        )
    if len({match[1] for _, match in candidates}) != 1:
        raise ValueError("Release contains multiple RPM revisions; remove superseded assets first")
    # One SRPM builds all project chroots; prefer the newest Fedora source build.
    return max(candidates, key=lambda item: int(item[1][2]))[0]


def verify_srpm(path, manifest):
    expected = []
    for line in manifest.read_text().splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) == 2 and fields[1].lstrip("*") == path.name:
            expected.append(fields[0])
    if len(expected) != 1 or not re.fullmatch(r"[0-9a-fA-F]{64}", expected[0]):
        raise ValueError("SRPM must have exactly one valid SHA-256 entry in SHA256SUMS")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected[0].lower():
        raise ValueError("SRPM checksum does not match SHA256SUMS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    release = json.loads(
        subprocess.check_output(
            [
                "gh",
                "release",
                "view",
                args.tag,
                "--repo",
                args.repo,
                "--json",
                "tagName,isDraft,isPrerelease,assets",
            ],
            text=True,
        )
    )
    name = select_srpm(release, args.tag)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "gh",
            "release",
            "download",
            args.tag,
            "--repo",
            args.repo,
            "--pattern",
            name,
            "--pattern",
            "SHA256SUMS",
            "--dir",
            str(args.output_dir),
        ],
        check=True,
        stdout=sys.stderr,
    )
    path = args.output_dir / name
    verify_srpm(path, args.output_dir / "SHA256SUMS")
    print(path.resolve())


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
