"""Release publishing must select the intended source package and verify bytes."""

import hashlib
import runpy
from pathlib import Path

import pytest

helpers = runpy.run_path(str(Path(__file__).resolve().parents[1] / "build-aux/release_srpm.py"))
select_srpm = helpers["select_srpm"]
verify_srpm = helpers["verify_srpm"]


def release(*names, **overrides):
    return {
        "tagName": "v0.1.13",
        "isDraft": False,
        "isPrerelease": False,
        "assets": [{"name": name} for name in names],
        **overrides,
    }


def test_select_one_srpm_for_all_chroots():
    name = select_srpm(
        release(
            "SHA256SUMS",
            "housekeeper-0.1.13-2.fc43.src.rpm",
            "housekeeper-0.1.13-2.fc44.src.rpm",
            "housekeeper-0.1.13-2.fc44.noarch.rpm",
        ),
        "v0.1.13",
    )
    assert name == "housekeeper-0.1.13-2.fc44.src.rpm"


@pytest.mark.parametrize(
    "overrides",
    [
        {"isDraft": True},
        {"isPrerelease": True},
        {"tagName": "v0.1.12"},
    ],
)
def test_reject_unpublished_or_wrong_release(overrides):
    with pytest.raises(ValueError):
        select_srpm(
            release("SHA256SUMS", "housekeeper-0.1.13-2.fc44.src.rpm", **overrides), "v0.1.13"
        )


@pytest.mark.parametrize(
    "names",
    [
        ("Source code.zip",),
        ("SHA256SUMS", "housekeeper-0.1.12-1.fc44.src.rpm"),
        ("SHA256SUMS", "housekeeper-0.1.13-1.fc43.src.rpm", "housekeeper-0.1.13-2.fc44.src.rpm"),
        ("housekeeper-0.1.13-2.fc44.src.rpm",),
    ],
)
def test_reject_missing_or_ambiguous_assets(names):
    with pytest.raises(ValueError):
        select_srpm(release(*names), "v0.1.13")


def test_checksum_accepts_only_matching_bytes(tmp_path):
    srpm = tmp_path / "housekeeper-0.1.13-2.fc44.src.rpm"
    srpm.write_bytes(b"source package")
    manifest = tmp_path / "SHA256SUMS"
    checksum = hashlib.sha256(srpm.read_bytes()).hexdigest()
    entry = f"{checksum}  {srpm.name}\n"
    manifest.write_text(entry + f"{'0' * 64}  other.rpm\n")
    verify_srpm(srpm, manifest)
    srpm.write_bytes(b"changed package")
    with pytest.raises(ValueError, match="checksum"):
        verify_srpm(srpm, manifest)
    manifest.write_text(entry * 2)
    with pytest.raises(ValueError, match="exactly one"):
        verify_srpm(srpm, manifest)
    manifest.write_text(f"{checksum}  other.rpm\n")
    with pytest.raises(ValueError, match="exactly one"):
        verify_srpm(srpm, manifest)
