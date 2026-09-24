"""Reuse a system Flatpak runtime for a user app, only in a disposable container."""

import os
import subprocess
import sys
from pathlib import Path

from housekeeper.installations import Installer, InstallRequest
from housekeeper.models import Outcome
from housekeeper.providers.flatpak import load_flatpak

if os.environ.get("HOUSEKEEPER_DISPOSABLE_TEST") != "1" or not (
    Path("/run/.containerenv").exists() or Path("/.dockerenv").exists()
):
    raise SystemExit("This script requires the disposable test container.")

root = Path(__file__).resolve().parents[1]
top = Path("/tmp/housekeeper-install-system-runtime")
app_id = "org.example.HousekeeperSharedRuntimeApp"
runtime_id = "org.example.HousekeeperSharedPlatform"
arch = os.uname().machine


def run(*args):
    subprocess.run(args, check=True)


if os.geteuid() != 0:
    fp = load_flatpak()
    system = fp.Installation.new_system(None)
    before = system.get_installed_ref(fp.RefKind.RUNTIME, runtime_id, arch, "stable", None)
    run("flatpak", "--user", "remote-add", "--no-gpg-verify", "app-fixture", str(top / "apps"))
    installer = Installer()
    callback_errors = []

    def record_error(kind, value, trace):
        callback_errors.append(str(value))
        sys.__excepthook__(kind, value, trace)

    sys.excepthook = record_error
    candidates = installer.search("flatpak", "HousekeeperSharedRuntimeApp")
    assert len(candidates) == 1, candidates
    candidate = candidates[0]
    assert candidate.remote == "app-fixture" and candidate.name == app_id
    result = installer.install(InstallRequest("flatpak", candidate=candidate), lambda *_: None)
    assert result.outcome == Outcome.SUCCESS, result
    assert not callback_errors, callback_errors
    installation = fp.Installation.new_user(None)
    refs = {ref.format_ref() for ref in installation.list_installed_refs(None)}
    assert refs == {candidate.target}, refs
    assert (Path(candidate.context) / "exports/share/applications" / (app_id + ".desktop")).exists()
    after = system.get_installed_ref(fp.RefKind.RUNTIME, runtime_id, arch, "stable", None)
    assert before.get_commit() == after.get_commit()
    print("PASS: user app reuses the unchanged system runtime without installing a user copy")
    raise SystemExit(0)

for identity, runtime in ((runtime_id, True), (app_id, False)):
    build = top / identity
    content = build / ("usr" if runtime else "files")
    (content / "bin").mkdir(parents=True)
    (build / "files").mkdir(exist_ok=True)
    (content / "bin/example").write_text("#!/bin/sh\nexit 0\n")
    (content / "bin/example").chmod(0o755)
    (build / "metadata").write_text(
        f"[{'Runtime' if runtime else 'Application'}]\nname={identity}\n"
        f"runtime={runtime_id}/{arch}/stable\nsdk={runtime_id}/{arch}/stable\n"
        + ("" if runtime else "command=example\n")
    )
    if not runtime:
        desktop = content / "share/applications" / (app_id + ".desktop")
        desktop.parent.mkdir(parents=True)
        desktop.write_text("[Desktop Entry]\nType=Application\nName=Shared Runtime\nExec=example\n")
    run("flatpak", "build-finish", str(build))
    run(
        "flatpak",
        "build-export",
        *(("--runtime",) if runtime else ()),
        str(top / ("runtimes" if runtime else "apps")),
        str(build),
        "stable",
    )
run(
    "flatpak", "--system", "remote-add", "--no-gpg-verify", "runtime-fixture", str(top / "runtimes")
)
run("flatpak", "--system", "install", "-y", "runtime-fixture", runtime_id)
run("useradd", "--create-home", "hk-install-runtime")
run(
    "runuser",
    "-u",
    "hk-install-runtime",
    "--",
    "env",
    "HOUSEKEEPER_DISPOSABLE_TEST=1",
    f"PYTHONPATH={root / 'src'}",
    "GIO_USE_VFS=local",
    "dbus-run-session",
    f"--config-file={root / 'tests/session.conf'}",
    "--",
    "/usr/bin/python3",
    __file__,
)
