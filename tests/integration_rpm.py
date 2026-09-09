"""Run only inside the disposable container prepared by integration_host.py."""

import logging
import os
import subprocess
import sys
from pathlib import Path

from housekeeper.attribution import attribute
from housekeeper.discovery import read_entry
from housekeeper.identity import classify
from housekeeper.models import ManagementError, Outcome
from housekeeper.providers.rpm import RpmIndex, RpmProvider

logging.basicConfig(level=logging.DEBUG)

if (
    os.environ.get("HOUSEKEEPER_DISPOSABLE_TEST") != "1"
    or not (Path("/run/.containerenv").exists() or Path("/.dockerenv").exists())
    or os.geteuid() == 0
):
    raise SystemExit("This script requires an unprivileged user in the disposable test container.")

path = Path("/usr/share/applications/housekeeper-fixture.desktop")
app = classify(read_entry(path, path.parent, {"GNOME"}))
app = attribute(app, (RpmIndex(),))
assert app.metadata["name"] == "housekeeper-fixture"
manager = RpmProvider()
mode = sys.argv[1]
capability_file = Path.home() / "native-removal-result"


def recognized_unavailable(error):
    return "!allow_deps is not supported" in str(error) or "single-package removal preview" in str(
        error
    )


if mode == "blocked":
    try:
        manager.prepare(app, [app])
    except ManagementError as error:
        assert "changed" not in str(error), str(error)
        assert path.exists()
        if recognized_unavailable(error):
            print("PASS: unavailable native preview preserves the installed package")
        else:
            print("PASS: reverse dependency prevents removal")
    else:
        raise AssertionError("A dependent package must prevent removal")
elif mode == "remove":
    try:
        plan = manager.prepare(app, [app])
    except ManagementError as error:
        if not recognized_unavailable(error):
            raise
        assert path.exists()
        capability_file.write_text("unavailable")
        print("LIMITATION: " + str(error))
        raise SystemExit(0) from None
    assert plan.affected == ("Housekeeper Fixture",)
    result = manager.execute(app, plan, lambda *_: None)
    assert result.outcome == Outcome.SUCCESS, result
    assert subprocess.run(["rpm", "-q", "housekeeper-fixture"], capture_output=True).returncode != 0
    assert not path.exists()
    capability_file.write_text("removed")
    print("PASS: PackageKit preview, Polkit authorization, and removal")
elif mode == "denied":
    plan = manager.prepare(app, [app])
    try:
        result = manager.execute(app, plan, lambda *_: None)
        assert result.outcome != Outcome.SUCCESS
    except ManagementError:
        pass
    assert path.exists()
    print("PASS: denied authorization retains the package")
else:
    raise AssertionError("Unknown integration mode")
