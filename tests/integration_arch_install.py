"""Prove ALPM installation queues unrelated upgrades and Housekeeper refuses it.

Only disposable containers are allowed. The unsigned local fixture is used for
simulation, not as evidence of trusted package installation or desktop Polkit.
"""

import os
import shutil
import subprocess
from pathlib import Path

if os.environ.get("HOUSEKEEPER_DISPOSABLE_TEST") != "1" or not (
    Path("/run/.containerenv").exists() or Path("/.dockerenv").exists()
):
    raise SystemExit("This script requires the disposable test container.")

ROOT = Path(__file__).resolve().parents[1]
TOP = Path("/tmp/housekeeper-alpm-fixture")
PACKAGE = "housekeeper-alpm-target"
EXISTING = "housekeeper-alpm-existing"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def version(name):
    result = subprocess.run(["pacman", "-Q", name], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


if os.geteuid() != 0:
    import gi

    gi.require_version("PackageKitGlib", "1.0")
    from gi.repository import PackageKitGlib as Pk

    from housekeeper.installations import Installer, InstallRequest
    from housekeeper.models import ManagementError
    from housekeeper.providers.install import checked_packages

    installer = Installer()
    candidate = next(
        c for c in installer.search("pacman", "Housekeeper Alpm Fixture") if c.name == PACKAGE
    )
    assert candidate.title == "Housekeeper Alpm Fixture" and not candidate.installed
    # Exercise the real backend directly, without committing anything.
    client = Pk.Client()
    client.set_interactive(True)
    flags = (1 << int(Pk.TransactionFlagEnum.SIMULATE)) | (
        1 << int(Pk.TransactionFlagEnum.ONLY_TRUSTED)
    )
    result = client.install_packages(flags, [candidate.target], None, lambda *_: None, None)
    targets = {p.get_name(): p.get_version() for p in checked_packages(result, Pk)}
    assert targets[PACKAGE] == "1.0-1" and targets[EXISTING] == "2.0-1", targets
    print(f"ALPM single-package simulation also includes unrelated upgrade: {targets}")
    try:
        installer.install(InstallRequest("pacman", candidate=candidate), lambda *_: None)
    except ManagementError as error:
        assert "sudo pacman -Syu PACKAGE" in str(error), error
    else:
        raise AssertionError("Housekeeper must refuse an implicit system upgrade")
    assert version(EXISTING) == f"{EXISTING} 1.0-1" and not version(PACKAGE)
    print("PASS: Arch AppStream search and installation handoff; no packages changed")
    raise SystemExit(0)

# All configuration and package changes below live inside the container.
repo = TOP / "repo"
repo.mkdir(parents=True)
run("chown", "-R", "hk-build:hk-build", str(TOP))


def build(name, release):
    directory = TOP / f"{name}-{release}"
    directory.mkdir()
    (directory / "PKGBUILD").write_text(
        f"pkgname={name}\npkgver={release}\npkgrel=1\narch=('any')\n"
        "pkgdesc='Disposable ALPM transaction fixture'\nlicense=('MIT')\n"
        "options=('!debug')\npackage() {\n"
        f' install -Dm644 /dev/null "$pkgdir/usr/share/{name}/marker"\n'
        "}\n"
    )
    run("chown", "-R", "hk-build:hk-build", str(directory))
    run("runuser", "-u", "hk-build", "--", "makepkg", "--noconfirm", cwd=directory)
    return next(directory.glob("*.pkg.tar.zst"))


run("pacman", "-U", "--noconfirm", str(build(EXISTING, "1.0")))
for package in (build(EXISTING, "2.0"), build(PACKAGE, "1.0")):
    shutil.copyfile(package, repo / package.name)
run("repo-add", str(repo / "fixture.db.tar.gz"), *map(str, repo.glob("*.pkg.tar.zst")))
Path("/etc/pacman.conf").write_text(
    f"[options]\nArchitecture = auto\nSigLevel = Never\n[fixture]\nServer = file://{repo}\n"
)
# Only fixture packages exist in this isolated sync repository. This is not a
# user installation recipe: real Arch installations require a full -Syu.
run("pacman", "-Sy", "--noconfirm")
catalogue = Path("/usr/share/swcatalog/xml/housekeeper-fixture.xml")
catalogue.parent.mkdir(parents=True, exist_ok=True)
catalogue.write_text(
    '<?xml version="1.0"?><components version="1.0" origin="housekeeper-fixture">'
    '<component type="desktop-application"><id>org.example.HousekeeperFixture</id>'
    "<name>Housekeeper Alpm Fixture</name><summary>Test native installation scope</summary>"
    f"<pkgname>{PACKAGE}</pkgname>"
    f'<launchable type="desktop-id">{PACKAGE}.desktop</launchable>'
    "</component></components>"
)
Path("/run/dbus").mkdir(exist_ok=True)
run("dbus-uuidgen", "--ensure")
run("dbus-daemon", "--system", "--fork", "--nopidfile")
Path("/etc/polkit-1/rules.d/00-housekeeper-fixture.rules").write_text(
    "polkit.addRule(function(action, subject) {\n"
    ' if (subject.user == "hk-build" && action.id.indexOf("org.freedesktop.packagekit.") == 0)\n'
    "  return polkit.Result.YES;\n});\n"
)
daemons = []
try:
    for command, bus_name in (
        (["/usr/lib/polkit-1/polkitd", "--no-debug"], "org.freedesktop.PolicyKit1"),
        (["/usr/lib/packagekitd", "--keep-environment"], "org.freedesktop.PackageKit"),
    ):
        daemons.append(subprocess.Popen(command))
        run("gdbus", "wait", "--system", "--timeout=15", bus_name)
    run(
        "runuser",
        "-u",
        "hk-build",
        "--",
        "env",
        "HOUSEKEEPER_DISPOSABLE_TEST=1",
        f"PYTHONPATH={ROOT / 'src'}",
        "GIO_USE_VFS=local",
        "GIO_USE_NETWORK_MONITOR=base",
        "dbus-run-session",
        f"--config-file={ROOT / 'tests/session.conf'}",
        "--",
        "/usr/bin/python",
        __file__,
    )
finally:
    for daemon in reversed(daemons):
        daemon.terminate()
        daemon.wait(timeout=10)
