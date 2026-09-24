"""Install signed RPM fixtures via PackageKit, confined to the disposable container."""

import os
import subprocess
from pathlib import Path

from housekeeper.installations import Installer, InstallRequest
from housekeeper.models import Outcome

if os.environ.get("HOUSEKEEPER_DISPOSABLE_TEST") != "1" or not (
    Path("/run/.containerenv").exists() or Path("/.dockerenv").exists()
):
    raise SystemExit("This script requires the disposable test container.")

root = Path(__file__).resolve().parents[1]
package = "housekeeper-second-update-fixture"
dependency = "housekeeper-update-dependency"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


if os.geteuid() != 0:
    installer = Installer()
    matches = installer.search("rpm", "housekeeper-second-update-fixtur")
    candidate = next(item for item in matches if item.name == package)
    assert candidate.target.split(";")[1] == "2-1", candidate
    result = installer.install(InstallRequest("rpm", candidate=candidate), lambda *_: None)
    assert result.outcome == Outcome.SUCCESS, result
    for name in (package, dependency):
        version = run("rpm", "-q", "--qf", "%{VERSION}", name, capture_output=True, text=True)
        assert version.stdout == "2", version.stdout
    # PackageKit's search snapshot may lag its own transaction. The independent
    # RPM queries above establish completion, not an immediately repeated search.
    print("PASS: real native package search, signed installation and dependency installation")
    raise SystemExit(0)

os.environ["GIO_USE_NETWORK_MONITOR"] = "base"
run("useradd", "--create-home", "hk-test")
run("/usr/bin/python3", str(root / "tests/integration_rpm_update.py"), "setup")
run("rpm", "-e", "housekeeper-update-fixture", dependency)
# Start the bus only after fixture setup; RPM scriptlets can otherwise activate
# PackageKit before the local repository and final installed state exist.
Path("/run/dbus").mkdir(exist_ok=True)
run("dbus-uuidgen", "--ensure")
run("dbus-daemon", "--system", "--fork", "--nopidfile")
Path("/etc/polkit-1/rules.d/00-housekeeper-install-fixture.rules").write_text(
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
    run("pkcon", "refresh", "force")
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
