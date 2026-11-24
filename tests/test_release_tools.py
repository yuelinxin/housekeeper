"""Release metadata stays consistent, and a release holds exactly the validated packages."""

import hashlib
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from housekeeper import VERSION

ROOT = Path(__file__).resolve().parents[1]
assets = runpy.run_path(str(ROOT / "build-aux/release_assets.py"))
notes = runpy.run_path(str(ROOT / "build-aux/release_notes.py"))


def test_every_release_record_names_the_application_version():
    meson = re.search(r"version: '([^']+)'", (ROOT / "meson.build").read_text())
    spec = (ROOT / "packaging/housekeeper.spec").read_text()
    spec_version = re.search(r"^Version:\s+(\S+)", spec, re.M)
    spec_changelog = re.search(r"^%changelog\n\*[^\n]* - ([^-\s]+)-\d+$", spec, re.M)
    # Distribution builds append a suffix such as +ubuntu24.04 to the revision.
    debian = re.match(r"housekeeper \(([^-)]+)-[^)]+\)", (ROOT / "debian/changelog").read_text())
    metainfo = ET.parse(ROOT / "data/io.github.yuelinxin.housekeeper.metainfo.xml")
    release = metainfo.getroot().find("releases/release")
    changelog = re.search(r"^## (\S+)", (ROOT / "CHANGELOG.md").read_text(), re.M)
    found = {
        "meson.build": meson and meson[1],
        "spec Version": spec_version and spec_version[1],
        "spec %changelog": spec_changelog and spec_changelog[1],
        "debian/changelog": debian and debian[1],
        "metainfo": release is not None and release.get("version"),
        "CHANGELOG.md": changelog and changelog[1],
    }
    assert found == dict.fromkeys(found, VERSION)


def test_debian_changelog_is_released():
    first = (ROOT / "debian/changelog").read_text().splitlines()[0]
    assert "UNRELEASED" not in first


def write_release(directory, version, *, arch_source=b"source"):
    for name in assets["expected_assets"](version):
        (directory / name).write_bytes(b"package")
    (directory / f"housekeeper-{version}.tar.gz").write_bytes(b"source")
    (directory / f"housekeeper-{version}-arch.tar.gz").write_bytes(arch_source)
    digest = hashlib.sha256(arch_source).hexdigest()
    (directory / "PKGBUILD").write_text(f"pkgver={version}\nsha256sums=('{digest}')\n")


def test_complete_release_passes(tmp_path):
    write_release(tmp_path, "1.2.3")
    (tmp_path / "SHA256SUMS").write_text("")
    assert assets["check"](tmp_path, "1.2.3") == []
    names = assets["expected_assets"]("1.2.3")
    assert {
        "housekeeper_1.2.3-1+ubuntu24.04_all.deb",
        "housekeeper_1.2.3-1+debian13_all.deb",
        "housekeeper-1.2.3-1-any.pkg.tar.zst",
        "housekeeper-1.2.3-1.fc45.src.rpm",
    } <= names


@pytest.mark.parametrize(
    "change",
    ["housekeeper_1.2.3-1+debian13_all.deb", "housekeeper-1.2.3-1-any.pkg.tar.zst", "PKGBUILD"],
)
def test_missing_native_package_fails(tmp_path, change):
    write_release(tmp_path, "1.2.3")
    (tmp_path / change).unlink()
    assert any(
        "Missing" in problem and change in problem for problem in assets["check"](tmp_path, "1.2.3")
    )


def test_wrong_version_or_extra_package_fails(tmp_path):
    write_release(tmp_path, "1.2.3")
    (tmp_path / "housekeeper_1.2.2-1+ubuntu24.04_all.deb").write_bytes(b"old")
    problems = assets["check"](tmp_path, "1.2.3")
    assert any("Unexpected" in problem for problem in problems)


def test_arch_source_must_match_release_source_and_pkgbuild(tmp_path):
    write_release(tmp_path, "1.2.3", arch_source=b"different")
    problems = assets["check"](tmp_path, "1.2.3")
    assert problems == ["The Arch source archive differs from the release source archive"]
    (tmp_path / "PKGBUILD").write_text("pkgver=1.2.3\nsha256sums=('0')\n")
    (tmp_path / "housekeeper-1.2.3-arch.tar.gz").write_bytes(b"source")
    assert assets["check"](tmp_path, "1.2.3") == [
        "PKGBUILD checksum does not match its source archive"
    ]


def test_release_notes_use_only_the_requested_version():
    changelog = "# Changelog\n\n## 1.1.0 — date\n\n- New.\n\n## 1.0.0 — date\n\n- Old.\n"
    assert notes["section"](changelog, "1.1.0") == "- New."
    assert notes["section"](changelog, "1.0.0") == "- Old."
    with pytest.raises(ValueError):
        notes["section"](changelog, "1.0")
    with pytest.raises(ValueError):
        notes["section"](changelog, "2.0.0")


def test_release_notes_for_current_version_include_installation():
    output = subprocess.check_output(
        [sys.executable, str(ROOT / "build-aux/release_notes.py"), VERSION], cwd=ROOT, text=True
    )
    assert f"housekeeper-{VERSION}-1-any.pkg.tar.zst" in output
    assert "## 0.3.6" not in output and "## Installation" in output


def test_source_archive_is_reproducible(tmp_path):
    if not (ROOT / ".git").exists() or shutil.which("git") is None:
        pytest.skip("The source archive is built from a git checkout")
    first, second = tmp_path / "first.tar.gz", tmp_path / "second.tar.gz"
    for output in (first, second):
        subprocess.run(
            [sys.executable, str(ROOT / "build-aux/source_archive.py"), str(output)],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first) as archive:
        members = archive.getmembers()
    assert members and all(m.uid == 0 and m.uname == "" for m in members)
    assert len({m.mtime for m in members}) == 1
