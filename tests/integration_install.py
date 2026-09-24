"""Install a local Flatpak app and runtime; only in an unprivileged disposable container."""

import base64
import os
import subprocess
import sys
from pathlib import Path

from housekeeper.installations import Installer, InstallRequest
from housekeeper.models import ManagementError, Outcome
from housekeeper.providers.flatpak import load_flatpak
from housekeeper.repositories import RepositoryManager

if (
    os.environ.get("HOUSEKEEPER_DISPOSABLE_TEST") != "1"
    or not (Path("/run/.containerenv").exists() or Path("/.dockerenv").exists())
    or os.geteuid() == 0
):
    raise SystemExit("Run as an unprivileged user in the disposable test container.")

home = Path.home()
repository = home / "install-repository"
app_id = "org.example.HousekeeperInstall"
runtime_id = "org.example.HousekeeperInstallPlatform"
arch = os.uname().machine


def run(*args):
    subprocess.run(args, check=True)


keys = home / "fixture-keys"
keys.mkdir(mode=0o700)
os.environ["GNUPGHOME"] = str(keys)
run(
    "gpg",
    "--batch",
    "--passphrase",
    "",
    "--quick-generate-key",
    "Housekeeper Sources <fixture@example.invalid>",
    "rsa2048",
    "sign",
    "0",
)
key_listing = subprocess.check_output(["gpg", "--with-colons", "--list-secret-keys"], text=True)
key_id = next(line.split(":")[9] for line in key_listing.splitlines() if line.startswith("fpr:"))
public_key = base64.b64encode(subprocess.check_output(["gpg", "--export", key_id])).decode()

for identity, runtime in ((runtime_id, True), (app_id, False)):
    build = home / identity
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
        desktop.write_text(
            "[Desktop Entry]\nType=Application\nName=Install Fixture\nExec=example\n"
        )
    run("flatpak", "build-finish", str(build))
    run(
        "flatpak",
        "build-export",
        f"--gpg-sign={key_id}",
        *(("--runtime",) if runtime else ()),
        str(repository),
        str(build),
        "stable",
    )
fp = load_flatpak()
installation = fp.Installation.new_user(None)
manager = RepositoryManager()
source_file = home / "install-fixture.flatpakrepo"
source_file.write_text(
    f"[Flatpak Repo]\nTitle=Install Fixture\nUrl={repository.as_uri()}\nGPGKey={public_key}\n"
)
plan = manager.prepare_flatpak_source(installation.get_path().get_path(), str(source_file))
assert plan.name == "install-fixture" and plan.url == repository.as_uri()
manager.add_flatpak_source(plan)
try:
    manager.add_flatpak_source(plan)
except ManagementError:
    pass
else:
    raise AssertionError("A duplicate repository was accepted")


def source():
    return next(
        item
        for group in manager.list_sources()
        if group.context == plan.context
        for item in group.sources
        if item.identifier == plan.name
    )


assert source().enabled and source().url == plan.url
assert source().verified == plan.verified is True
manager.set_enabled(source(), False)
assert not source().enabled
assert source().verified == plan.verified
manager.set_enabled(source(), True)
assert source().enabled
installer = Installer()
callback_errors = []
sys.excepthook = lambda kind, value, trace: callback_errors.append(str(value))
matches = installer.search("flatpak", "HousekeeperInstal")
assert len(matches) == 1, matches
candidate = matches[0]
assert candidate.name == app_id and candidate.remote == "install-fixture"
result = installer.install(InstallRequest("flatpak", candidate=candidate), lambda *_: None)
assert result.outcome == Outcome.SUCCESS, result
assert not callback_errors, callback_errors
installed = {ref.format_ref() for ref in installation.list_installed_refs(None)}
assert candidate.target in installed
assert f"runtime/{runtime_id}/{arch}/stable" in installed
assert (Path(candidate.context) / "exports/share/applications" / (app_id + ".desktop")).exists()
assert installer.search("flatpak", "HousekeeperInstal") == ()
manager.set_enabled(source(), False)
assert not source().enabled
assert candidate.target in {ref.format_ref() for ref in installation.list_installed_refs(None)}
print(
    "PASS: real Flatpak source import, duplicate rejection, enable/disable, fuzzy search, "
    "exact remote/ref install, runtime dependency, desktop export and installed-app preservation"
)
