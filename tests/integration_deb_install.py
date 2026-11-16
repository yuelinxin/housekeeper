"""Exercise APT/PackageKit using a signed offline repo in a disposable container."""

import os
import subprocess
import sys
from pathlib import Path

if os.environ.get("HOUSEKEEPER_DISPOSABLE_TEST") != "1" or not (
    Path("/run/.containerenv").exists() or Path("/.dockerenv").exists()
):
    raise SystemExit("This script requires the disposable test container.")

ROOT = Path(__file__).resolve().parents[1]
TOP = Path("/tmp/housekeeper-apt-fixture")
PACKAGE = "housekeeper-apt-fixture"
DEPENDENCY = "housekeeper-apt-dependency"
MODE = sys.argv[1]
assert MODE in {"allow", "deny"}


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def installed(name):
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Status} ${Version}", name], capture_output=True, text=True
    )
    return result.returncode == 0 and result.stdout == "install ok installed 1.0"


if os.geteuid() != 0:
    from gi.repository import GLib

    from housekeeper.attribution import attribute
    from housekeeper.discovery import read_entry
    from housekeeper.identity import classify
    from housekeeper.installations import Installer, InstallRequest
    from housekeeper.models import Action, ManagementError, Outcome, Source, UpdateAction
    from housekeeper.providers.deb import DebIndex
    from housekeeper.updates import assign_update_action

    installer = Installer()
    matches = installer.search("deb", "Housekeeper Apt Fixture")
    candidate = next(item for item in matches if item.name == PACKAGE)
    assert not candidate.installed and candidate.title == "Housekeeper Apt Fixture", candidate
    try:
        result = installer.install(InstallRequest("deb", candidate=candidate), lambda *_: None)
    except (GLib.Error, ManagementError) as error:
        assert MODE == "deny", error
        assert any(word in str(error).lower() for word in ("auth", "permission", "denied")), error
        assert not installed(PACKAGE) and not installed(DEPENDENCY)
        print("PASS: APT installation denied without changing dpkg state")
        raise SystemExit(0) from None
    assert MODE == "allow" and result.outcome == Outcome.SUCCESS, result
    assert installed(PACKAGE) and installed(DEPENDENCY)
    applications = Path("/usr/share/applications")
    entry = read_entry(applications / f"{PACKAGE}.desktop", applications)
    app = attribute(classify(entry), (DebIndex(),))
    assert app.source == Source.DEB and app.version == "1.0", app
    assert app.action == Action.NONE
    assign_update_action(app, {})
    assert app.update_action == UpdateAction.INSTRUCTIONS
    print("PASS: signed APT search/install, dependency installation and DEB attribution")
    raise SystemExit(0)

# All changes below are restricted to the disposable container's filesystem.
os.environ["GIO_USE_NETWORK_MONITOR"] = "base"
repo = TOP / "repo"
repo.mkdir(parents=True)
for name in (PACKAGE, DEPENDENCY):
    directory = TOP / name
    (directory / "DEBIAN").mkdir(parents=True)
    dependency = f"Depends: {DEPENDENCY} (= 1.0)\n" if name == PACKAGE else ""
    (directory / "DEBIAN/control").write_text(
        f"Package: {name}\nVersion: 1.0\nArchitecture: all\nSection: x11\n"
        "Priority: optional\nMaintainer: Housekeeper Test <test@example.invalid>\n"
        f"Installed-Size: 1\n{dependency}Description: Disposable Housekeeper APT fixture\n"
    )
    if name == PACKAGE:
        binary = directory / "usr/bin" / name
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        desktop = directory / "usr/share/applications" / f"{name}.desktop"
        desktop.parent.mkdir(parents=True)
        desktop.write_text(
            "[Desktop Entry]\nType=Application\nName=Housekeeper Apt Fixture\n"
            f"Exec=/usr/bin/{name}\nIcon=application-x-executable\nCategories=Utility;\n"
        )
    else:
        marker = directory / "usr/share" / name / "marker"
        marker.parent.mkdir(parents=True)
        marker.write_text("dependency\n")
    run("dpkg-deb", "--build", "--root-owner-group", str(directory), str(repo / f"{name}.deb"))
with (repo / "Packages").open("wb") as output:
    run("apt-ftparchive", "packages", ".", cwd=repo, stdout=output)
with (repo / "Release").open("wb") as output:
    run("apt-ftparchive", "release", ".", cwd=repo, stdout=output)
key_home = TOP / "keys"
key_home.mkdir(mode=0o700)
gpg = (
    "gpg",
    "--batch",
    "--homedir",
    str(key_home),
    "--pinentry-mode",
    "loopback",
    "--passphrase",
    "",
)
run(
    *gpg,
    "--quick-generate-key",
    "Housekeeper Fixture <fixture@example.invalid>",
    "ed25519",
    "sign",
    "0",
)
keyring = Path("/usr/share/keyrings/housekeeper-fixture.gpg")
with keyring.open("wb") as output:
    run(*gpg, "--export", stdout=output)
run(*gpg, "--clearsign", "--output", str(repo / "InRelease"), str(repo / "Release"))
for source in Path("/etc/apt/sources.list.d").iterdir():
    if source.suffix in {".list", ".sources"}:
        source.unlink()
Path("/etc/apt/sources.list").write_text(f"deb [signed-by={keyring}] file:{repo} ./\n")
run("apt-get", "update")
catalogue = Path("/usr/share/swcatalog/xml/housekeeper-fixture.xml")
catalogue.parent.mkdir(parents=True, exist_ok=True)
catalogue.write_text(
    '<?xml version="1.0"?><components version="1.0" origin="housekeeper-fixture">'
    '<component type="desktop-application"><id>org.example.HousekeeperFixture</id>'
    "<name>Housekeeper Apt Fixture</name><summary>Test native installation</summary>"
    f"<pkgname>{PACKAGE}</pkgname>"
    f'<launchable type="desktop-id">{PACKAGE}.desktop</launchable>'
    "</component></components>"
)
run("useradd", "--create-home", "hk-test")
Path("/run/dbus").mkdir(exist_ok=True)
run("dbus-uuidgen", "--ensure")
run("dbus-daemon", "--system", "--fork", "--nopidfile")
rules = Path("/etc/polkit-1/rules.d/00-housekeeper-fixture.rules")
rules.parent.mkdir(parents=True, exist_ok=True)
rules.write_text(
    "polkit.addRule(function(action, subject) {\n"
    ' if (subject.user == "hk-test" && action.id.indexOf("org.freedesktop.packagekit.") == 0)\n'
    f"  return polkit.Result.{'YES' if MODE == 'allow' else 'NO'};\n"
    "});\n"
)
daemons = []
try:
    for command, bus_name in (
        (["/usr/lib/polkit-1/polkitd", "--no-debug"], "org.freedesktop.PolicyKit1"),
        (["/usr/libexec/packagekitd", "--keep-environment"], "org.freedesktop.PackageKit"),
    ):
        daemons.append(subprocess.Popen(command))
        run("gdbus", "wait", "--system", "--timeout=15", bus_name)
    run(
        "runuser",
        "-u",
        "hk-test",
        "--",
        "env",
        "HOUSEKEEPER_DISPOSABLE_TEST=1",
        f"PYTHONPATH={ROOT / 'src'}",
        "GIO_USE_VFS=local",
        "dbus-run-session",
        f"--config-file={ROOT / 'tests/session.conf'}",
        "--",
        "/usr/bin/python3",
        __file__,
        MODE,
    )
finally:
    for daemon in reversed(daemons):
        daemon.terminate()
        daemon.wait(timeout=10)
