"""Exercise real PackageKit source toggles only in a disposable Fedora container."""

import os
import subprocess
from pathlib import Path

from housekeeper.models import ManagementError
from housekeeper.repositories import RepositoryManager

if os.environ.get("HOUSEKEEPER_DISPOSABLE_TEST") != "1" or not (
    Path("/run/.containerenv").exists() or Path("/.dockerenv").exists()
):
    raise SystemExit("This script requires the disposable test container.")

root = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run(args, check=True)


if os.geteuid() != 0:
    manager = RepositoryManager()

    def sources():
        native = next(group for group in manager.list_sources() if group.provider == "native")
        assert not native.error, native.error
        return {source.identifier: source for source in native.sources}

    original = sources()
    assert original["hk-first"].enabled and original["hk-second"].enabled
    manager.set_enabled(original["hk-first"], False)
    disabled = sources()
    assert not disabled["hk-first"].enabled and disabled["hk-second"].enabled
    try:
        manager.set_enabled(original["hk-first"], True)
    except ManagementError:
        pass
    else:
        raise AssertionError("Stale repository configuration was accepted")
    manager.set_enabled(disabled["hk-first"], True)
    assert sources() == original
    print("PASS: real native repository listing, exact-ID toggles and stale-state rejection")
    raise SystemExit(0)

# This setup never runs on the host. No packages are downloaded or installed.
run("useradd", "--create-home", "hk-test")
for path in Path("/etc/yum.repos.d").glob("*.repo"):
    path.rename(path.with_suffix(".disabled"))
Path("/tmp/hk-empty-repository").mkdir()
Path("/etc/yum.repos.d/hk-fixture.repo").write_text(
    "".join(
        f"[{name}]\nname=Housekeeper fixture\nbaseurl=file:///tmp/hk-empty-repository\n"
        "enabled=1\ngpgcheck=1\n"
        for name in ("hk-first", "hk-second")
    )
)
Path("/run/dbus").mkdir(exist_ok=True)
run("dbus-uuidgen", "--ensure")
run("dbus-daemon", "--system", "--fork", "--nopidfile")
Path("/etc/polkit-1/rules.d/00-housekeeper-sources-fixture.rules").write_text(
    "polkit.addRule(function(action, subject) {\n"
    ' if (subject.user == "hk-test" && action.id.indexOf("org.freedesktop.packagekit.") == 0)\n'
    "  return polkit.Result.YES;\n});\n"
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
        f"PYTHONPATH={root / 'src'}",
        "GIO_USE_VFS=local",
        "dbus-run-session",
        f"--config-file={root / 'tests/session.conf'}",
        "--",
        "/usr/bin/python3",
        __file__,
    )
finally:
    for daemon in daemons:
        daemon.terminate()
        daemon.wait(timeout=10)
