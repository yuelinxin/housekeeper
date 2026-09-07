"""Uninstall an isolated local Flatpak fixture without downloading a runtime."""

import os
import subprocess
from pathlib import Path

from housekeeper.models import Outcome
from housekeeper.providers.flatpak import FlatpakIndex, FlatpakProvider

if (
    os.environ.get("HOUSEKEEPER_DISPOSABLE_TEST") != "1"
    or not (Path("/run/.containerenv").exists() or Path("/.dockerenv").exists())
    or os.geteuid() == 0
):
    raise SystemExit("This script requires an unprivileged user in the disposable test container.")
home = Path.home()
build = home / "fixture-flatpak"
(build / "files/bin").mkdir(parents=True, exist_ok=True)
(build / "files/bin/example").write_text("#!/bin/sh\nexit 0\n")
(build / "files/bin/example").chmod(0o755)
(build / "metadata").write_text(
    "[Application]\nname=org.example.HousekeeperFixture\n"
    "runtime=org.example.TestPlatform/x86_64/1\nsdk=org.example.TestPlatform/x86_64/1\n"
    "command=example\n"
)
repository = home / "fixture-repository"


def run(*args):
    subprocess.run(args, check=True)


run("flatpak", "build-finish", str(build))
run("flatpak", "build-export", str(repository), str(build))
run("flatpak", "--user", "remote-add", "--no-gpg-verify", "fixture", str(repository))
run(
    "flatpak",
    "--user",
    "install",
    "-y",
    "--no-deps",
    "--no-related",
    "fixture",
    "org.example.HousekeeperFixture",
)
data = home / ".var/app/org.example.HousekeeperFixture/keep-me"
data.parent.mkdir(parents=True, exist_ok=True)
data.write_text("Personal data must survive uninstall.")
index = FlatpakIndex()
app = next(a for a in index.apps if a.metadata["app_id"] == "org.example.HousekeeperFixture")
assert app.scope == "User"
provider = FlatpakProvider()
plan = provider.prepare(app, index.apps)
assert data.exists()
assert any(a.key == app.key for a in FlatpakIndex().apps)
result = provider.execute(app, plan, lambda *_: None)
assert result.outcome == Outcome.SUCCESS
assert data.read_text() == "Personal data must survive uninstall."
assert all(a.key != app.key for a in FlatpakIndex().apps)
print("PASS: local Flatpak preview, uninstall, and data preservation")
