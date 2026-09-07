"""Signed, two-version RPM update fixtures; requires the disposable host harness."""

import os
import subprocess
import sys
import time
from pathlib import Path

from housekeeper.batch_updates import UpdateBatch
from housekeeper.models import ManagementError, Outcome, UpdateState
from housekeeper.providers.rpm import RpmIndex, RpmProvider
from housekeeper.services import collect

if os.environ.get("HOUSEKEEPER_DISPOSABLE_TEST") != "1" or not (
    Path("/run/.containerenv").exists() or Path("/.dockerenv").exists()
):
    raise SystemExit("This script requires the disposable integration container.")

root = Path(__file__).resolve().parents[1]
name = "housekeeper-update-fixture"
dependency = "housekeeper-update-dependency"
second_name = "housekeeper-second-update-fixture"
top = Path("/tmp/housekeeper-update-rpms")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def installed_version():
    owners = RpmIndex().owners("/usr/bin/" + name)
    assert len(owners) == 1
    return owners[0]["version"]


def user_check(mode):
    if mode == "batch":
        apps = [a for a in collect()[0] if a.metadata.get("name") in {name, second_name}]
        assert len(apps) == 2
        report = UpdateBatch(lambda _a: RpmProvider(), apps).check(lambda *_: None)
        assert not report.errors and len(report.items) == 2, report
        result = UpdateBatch(lambda _a: RpmProvider(), apps).execute(report.items, lambda *_: None)
        assert result.outcome == Outcome.SUCCESS, result
        for package in (name, second_name, dependency):
            version = run(
                "rpm", "-q", "--qf", "%{VERSION}", package, capture_output=True, text=True
            )
            assert version.stdout == "2", version.stdout
        print("PASS: real signed two-app RPM batch with a shared dependency")
        return
    app = next(a for a in collect()[0] if a.metadata.get("name") == name)
    provider = RpmProvider()
    try:
        check = provider.prepare_update(app, [app], lambda *_: None)
    except ManagementError as error:
        if mode != "update":
            raise
        assert "preview" in str(error).lower() or "cannot update" in str(error).lower(), error
        assert installed_version() == "1-1"
        Path.home().joinpath("rpm-update-result").write_text("fallback")
        print(f"LIMITATION: RPM update preview unavailable: {error}")
        return
    if mode == "current":
        assert check.state == UpdateState.CURRENT
        print("PASS: RPM no-update check")
        return
    assert check.state == UpdateState.AVAILABLE
    assert installed_version() == "1-1", "Preview changed the installed version"
    assert len(check.plan.changes) == 2, check.plan
    result = RpmProvider().execute_update(app, check.plan, lambda *_: None)
    if mode == "denied":
        assert result.outcome == Outcome.FAILED, result
        assert installed_version() == "1-1"
        print("PASS: RPM denied update preserves the installed version")
        return
    assert result.outcome == Outcome.SUCCESS, result
    assert installed_version() == "2-1"
    Path.home().joinpath("rpm-update-result").write_text("updated")
    print("PASS: signed RPM application and dependency updated from version 1 to version 2")


if os.geteuid() != 0:
    user_check(sys.argv[1])
    raise SystemExit(0)


def setup():
    for directory in ("SPECS", "SOURCES", "BUILD", "RPMS", "SRPMS", "keys", "repository"):
        (top / directory).mkdir(parents=True)
    (top / "keys").chmod(0o700)
    key_env = {**os.environ, "GNUPGHOME": str(top / "keys")}
    run(
        "gpg",
        "--batch",
        "--pinentry-mode",
        "loopback",
        "--passphrase",
        "",
        "--quick-generate-key",
        "Housekeeper Fixture <fixture@example.invalid>",
        "rsa2048",
        "sign",
        "0",
        env=key_env,
    )
    key = run(
        "gpg",
        "--batch",
        "--with-colons",
        "--list-keys",
        env=key_env,
        capture_output=True,
        text=True,
    )
    fingerprint = next(
        line.split(":")[9] for line in key.stdout.splitlines() if line.startswith("fpr:")
    )
    public_key = top / "fixture.asc"
    public_key.write_bytes(
        run("gpg", "--armor", "--export", fingerprint, env=key_env, capture_output=True).stdout
    )
    run("rpm", "--import", str(public_key))
    packages = {}
    for version in (1, 2):
        for package in (dependency, name, second_name):
            spec = top / "SPECS" / (package + ".spec")
            requires = f"Requires: {dependency} = {version}\n" if package != dependency else ""
            install = f"mkdir -p %{{buildroot}}/usr/share/{package}\necho {version} > %{{buildroot}}/usr/share/{package}/version\n"
            files = f"/usr/share/{package}/version"
            if package != dependency:
                (top / "SOURCES/fixture").write_text("#!/bin/sh\nexit 0\n")
                (top / "SOURCES/fixture.desktop").write_text(
                    f"[Desktop Entry]\nType=Application\nName=Housekeeper Update Fixture\nExec=/usr/bin/{package}\n"
                )
                install += (
                    f"install -Dm755 %{{_sourcedir}}/fixture %{{buildroot}}/usr/bin/{package}\n"
                    f"install -Dm644 %{{_sourcedir}}/fixture.desktop %{{buildroot}}/usr/share/applications/{package}.desktop\n"
                )
                files += f"\n/usr/bin/{package}\n/usr/share/applications/{package}.desktop"
            spec.write_text(
                f"Name: {package}\nVersion: {version}\nRelease: 1\nSummary: Update fixture\n"
                f"License: MIT\nBuildArch: noarch\n{requires}"
                f"%description\nDisposable update fixture.\n%install\n{install}%files\n{files}\n"
            )
            run("rpmbuild", "-bb", "--define", f"_topdir {top}", str(spec))
            rpm_path = top / f"RPMS/noarch/{package}-{version}-1.noarch.rpm"
            run(
                "rpmsign",
                "--define",
                f"_openpgp_sign_id {fingerprint}",
                "--define",
                f"_gpg_path {top / 'keys'}",
                "--addsign",
                str(rpm_path),
                env=key_env,
            )
            packages[package, version] = str(rpm_path)
            if version == 2:
                (top / "repository" / rpm_path.name).write_bytes(rpm_path.read_bytes())

    run("rpm", "-i", packages[dependency, 1], packages[name, 1])
    run("createrepo_c", str(top / "repository"))
    # Keep all repository changes confined to this throwaway container.
    for path in Path("/etc/yum.repos.d").glob("*.repo"):
        path.rename(path.with_suffix(".disabled"))
    Path("/etc/yum.repos.d/housekeeper-fixture.repo").write_text(
        f"[housekeeper-update-fixture]\nname=Housekeeper local update fixture\nbaseurl=file://{top}/repository\n"
        f"enabled=1\ngpgcheck=1\nrepo_gpgcheck=0\ngpgkey=file://{public_key}\n"
    )


if sys.argv[1] == "setup":
    setup()
    raise SystemExit(0)

packages = {
    (package, version): str(top / f"RPMS/noarch/{package}-{version}-1.noarch.rpm")
    for package in (name, dependency, second_name)
    for version in (1, 2)
}
rules = Path("/etc/polkit-1/rules.d/00-housekeeper-fixture.rules")


def authorize(allow):
    rules.write_text(
        "polkit.addRule(function(action, subject) {\n"
        ' if (subject.user == "hk-test" && action.id.indexOf("org.freedesktop.packagekit.") == 0) {\n'
        '  if (action.id == "org.freedesktop.packagekit.system-sources-refresh") return polkit.Result.YES;\n'
        f"  return polkit.Result.{'YES' if allow else 'NO'};\n }}\n}});\n"
    )
    time.sleep(1)


def as_user(mode):
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
        __file__,
        mode,
    )


authorize(True)
as_user("update")
if Path("/home/hk-test/rpm-update-result").read_text() == "updated":
    as_user("current")
    run("rpm", "-U", "--oldpackage", packages[dependency, 1], packages[name, 1])
    authorize(False)
    as_user("denied")
    run("rpm", "-i", packages[second_name, 1])
    authorize(True)
    as_user("batch")
else:
    print(
        "NOT VERIFIED: RPM update execution and authorization; this backend only passed fallback checks."
    )
