"""Uninstall an isolated local Flatpak fixture without downloading a runtime."""

import os
import subprocess
from pathlib import Path

from housekeeper.models import Action, ManagementError, Outcome, Source
from housekeeper.providers.flatpak import FlatpakIndex, FlatpakProvider
from housekeeper.services import collect

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
desktops = build / "files/share/applications"
desktops.mkdir(parents=True)
(desktops / "org.example.HousekeeperFixture.desktop").write_text(
    "[Desktop Entry]\nType=Application\nName=Housekeeper Flatpak Fixture\nExec=example %U\n"
    "DBusActivatable=true\n"
)
services = build / "files/share/dbus-1/services"
services.mkdir(parents=True)
(services / "org.example.HousekeeperFixture.service").write_text(
    "[D-BUS Service]\nName=org.example.HousekeeperFixture\nExec=/app/bin/example\n"
)
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
export = Path(app.metadata["installation"]) / "exports/share/applications"
records = collect(roots=[export])[0]
assert len(records) == 1, "An exported D-Bus launcher was duplicated as an installation row"
app = records[0]
assert app.entries and app.action == Action.UNINSTALL and app.installation
assert app.entries[0].dbus_activatable
provider = FlatpakProvider()
# A custom copy must not retain management permission after its command changes.
custom = home / "audit-launchers"
custom.mkdir()
launcher = custom / app.entries[0].desktop_id
launcher.write_text(app.entries[0].path.read_text() + "\nIcon=/tmp/custom-icon.png\n")
copied = next(a for a in collect(roots=[custom])[0] if a.entries and a.source == Source.FLATPAK)
provider.prepare(copied, [copied])
launcher.write_text(
    "[Desktop Entry]\nType=Application\nName=Unrelated\nExec=/usr/bin/true\nX-Flatpak=org.example.HousekeeperFixture\n"
)
try:
    provider.prepare(copied, [copied])
except ManagementError:
    pass
else:
    raise AssertionError("A changed launcher retained direct management permission")
spoofed = next(a for a in collect(roots=[custom])[0] if a.entries)
assert spoofed.source != Source.FLATPAK and spoofed.action != Action.UNINSTALL
print(
    "PASS: real D-Bus export deduplication, icon override, and changed/tag-only launcher rejection"
)
plan = provider.prepare(app, index.apps)
assert data.exists()
assert any(
    a.identity == app.identity and a.metadata["installation"] == app.metadata["installation"]
    for a in FlatpakIndex().apps
)
result = provider.execute(app, plan, lambda *_: None)
assert result.outcome == Outcome.SUCCESS
assert data.read_text() == "Personal data must survive uninstall."
assert all(
    a.identity != app.identity or a.metadata["installation"] != app.metadata["installation"]
    for a in FlatpakIndex().apps
)
print("PASS: local Flatpak preview, uninstall, and data preservation")
