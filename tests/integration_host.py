#!/usr/bin/python3
"""Prepare disposable RPM/Polkit fixtures. Refuses to run on the real host."""

import os
import subprocess
import time
from pathlib import Path

if (
    os.environ.get("HOUSEKEEPER_DISPOSABLE_TEST") != "1"
    or not (Path("/run/.containerenv").exists() or Path("/.dockerenv").exists())
    or os.geteuid() != 0
):
    raise SystemExit("This script requires root inside the disposable test container.")
root = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


run("useradd", "--create-home", "hk-test")
Path("/run/dbus").mkdir(exist_ok=True)
run("dbus-uuidgen", "--ensure")
run("dbus-daemon", "--system", "--fork", "--nopidfile")
top = Path("/tmp/housekeeper-fixtures")
for folder in ("SPECS", "SOURCES", "BUILD", "RPMS", "SRPMS"):
    (top / folder).mkdir(parents=True, exist_ok=True)
for name, dependency in (
    ("housekeeper-fixture", ""),
    ("housekeeper-fixture-dependent", "Requires: housekeeper-fixture"),
):
    spec = top / "SPECS" / (name + ".spec")
    entry = (
        "[Desktop Entry]\nType=Application\nName=Housekeeper Fixture\n"
        "Exec=/usr/bin/housekeeper-fixture\nIcon=application-x-executable\n"
    )
    install = (
        f"mkdir -p %{{buildroot}}/usr/share/{name}\ntouch %{{buildroot}}/usr/share/{name}/marker\n"
    )
    files = f"/usr/share/{name}/marker"
    if not dependency:
        (top / "SOURCES" / "fixture.desktop").write_text(entry)
        (top / "SOURCES" / "fixture").write_text("#!/bin/sh\nexit 0\n")
        install += (
            "install -Dm755 %{_sourcedir}/fixture %{buildroot}/usr/bin/housekeeper-fixture\n"
            "install -Dm644 %{_sourcedir}/fixture.desktop "
            "%{buildroot}/usr/share/applications/housekeeper-fixture.desktop\n"
        )
        files += (
            "\n/usr/bin/housekeeper-fixture\n/usr/share/applications/housekeeper-fixture.desktop"
        )
    spec.write_text(
        f"Name: {name}\nVersion: 1\nRelease: 1\nSummary: Disposable test fixture\n"
        f"License: MIT\nBuildArch: noarch\n{dependency}\n"
        f"%description\nA disposable integration fixture.\n%install\n{install}"
        f"%files\n{files}\n"
    )
    run("rpmbuild", "-bb", "--define", f"_topdir {top}", str(spec))
packages = [str(p) for p in (top / "RPMS/noarch").glob("*.rpm")]
run("rpm", "-i", *packages)
daemons = []
for command, bus_name in (
    (["/usr/lib/polkit-1/polkitd", "--no-debug"], "org.freedesktop.PolicyKit1"),
    (["/usr/libexec/packagekitd"], "org.freedesktop.PackageKit"),
    (["/usr/libexec/accounts-daemon"], "org.freedesktop.Accounts"),
):
    daemons.append(subprocess.Popen(command))
    run("gdbus", "wait", "--system", "--timeout=15", bus_name)
run("pkcon", "--filter=installed", "resolve", "housekeeper-fixture")
rules = Path("/etc/polkit-1/rules.d/00-housekeeper-fixture.rules")
rules.write_text(
    "polkit.addRule(function(action, subject) {\n"
    '  if (subject.user == "hk-test" && action.id == '
    '"org.freedesktop.packagekit.package-remove") return polkit.Result.YES;\n'
    "});\n"
)


def as_user(script, *args):
    run(
        "runuser",
        "-u",
        "hk-test",
        "--",
        "env",
        "HOUSEKEEPER_DISPOSABLE_TEST=1",
        f"PYTHONPATH={root / 'src'}",
        "GIO_USE_VFS=local",
        "dbus-run-session",
        f"--config-file={root / 'tests/session.conf'}",
        "--",
        "/usr/bin/python3",
        str(root / "tests" / script),
        *args,
    )


as_user("integration_rpm.py", "blocked")
run("rpm", "-e", "housekeeper-fixture-dependent")
as_user("integration_rpm.py", "remove")
if Path("/home/hk-test/native-removal-result").read_text() == "removed":
    # Exercise denial independently of a previously accepted request.
    run("rpm", "-i", str(next((top / "RPMS/noarch").glob("housekeeper-fixture-1-*.rpm"))))
    rules.write_text(
        "polkit.addRule(function(action, subject) {\n"
        '  if (subject.user == "hk-test" && action.id == '
        '"org.freedesktop.packagekit.package-remove") return polkit.Result.NO;\n'
        "});\n"
    )
    time.sleep(1)
    as_user("integration_rpm.py", "denied")
else:
    print("NOT VERIFIED: native removal authorization; this backend cannot provide a safe preview.")
as_user("integration_flatpak.py")
as_user("integration_appimage.py")
print("PASS: isolated integration suite")
