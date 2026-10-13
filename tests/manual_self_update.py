#!/usr/bin/python3
"""Run Housekeeper from this checkout with a pretend update to Housekeeper itself.

Only Housekeeper's own update is simulated; the sidebar card, confirmation, progress,
result and restart are the real ones. The Updates page still checks your other apps
for real, and updating them from there is real. Settings stay in memory and update
results go to a temporary cache, so nothing reaches your installed Housekeeper.

Close any running Housekeeper first, then open Updates to run the check:

    /usr/bin/python3 tests/manual_self_update.py            # update succeeds, Housekeeper restarts
    /usr/bin/python3 tests/manual_self_update.py --partial  # update partly fails, then restarts
    /usr/bin/python3 tests/manual_self_update.py --fail     # update fails, no restart
    /usr/bin/python3 tests/manual_self_update.py --flatpak  # installation it cannot update itself

The RPM modes need Housekeeper's own RPM installed, which provides the app record
the update acts on. After a restart the check finds Housekeeper up to date.
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = Path(os.environ.get("HOUSEKEEPER_BUILD_DIR", ROOT / "build"))
MODES = ("--partial", "--fail", "--flatpak")


def install_mock(mode, restarted):
    """Replace detection and Housekeeper's own check and update with pretend ones."""
    from housekeeper import VERSION, self_update, update_cache
    from housekeeper.batch_updates import UpdateItem
    from housekeeper.models import (
        OperationCancelled,
        OperationResult,
        Outcome,
        UpdateChange,
        UpdatePlan,
    )

    cache = Path(tempfile.mkdtemp(prefix="housekeeper-mock-")) / "updates.json"
    update_cache.cache_path = lambda: cache
    if mode == "--flatpak":
        installation = self_update._flatpak_installation(Path("/nonexistent"))
    else:
        installation = self_update.SelfInstallation(
            self_update.InstallMethod.RPM, "housekeeper", "noarch", updatable=True
        )
    self_update.current_installation = lambda: installation

    def wait(updater, progress, message, seconds):
        steps = int(seconds / 0.2)
        for step in range(steps):
            if updater.cancelled:
                raise OperationCancelled("The update operation was cancelled.")
            progress(message, step / steps, True)
            time.sleep(0.2)

    def check(updater, progress, provider=None, **_kwargs):
        app = updater.record()
        wait(updater, progress, "Pretending to check Housekeeper", 1.5)
        if restarted:
            return None
        major, minor, patch = (VERSION.split(".") + ["0", "0"])[:3]
        target_version = f"{major}.{minor}.{int(patch) + 1}-1.mock"
        target = f"housekeeper;{target_version};noarch;mock"
        change = UpdateChange(
            "housekeeper.noarch", target, "update", "mock", app.version, target_version
        )
        plan = UpdatePlan(
            app.key,
            "rpm",
            target,
            "System",
            app.version,
            (change,),
            "mock",
            "Update this package. This update is simulated; nothing will be installed.",
        )
        return UpdateItem(app, plan, (app.name,))

    def execute(updater, item, progress):
        try:
            wait(updater, progress, "Pretending to install Housekeeper", 4)
        except OperationCancelled as error:
            return OperationResult(Outcome.CANCELLED, str(error))
        if mode == "--fail":
            return OperationResult(
                Outcome.FAILED, "The pretend update failed.", errors=("Nothing was changed.",)
            )
        # The restarted process reads this and then finds Housekeeper up to date.
        os.environ["HOUSEKEEPER_MOCK_RESTARTED"] = "1"
        completed = (item.plan.changes[0].identity,)
        if mode == "--partial":
            return OperationResult(
                Outcome.PARTIAL,
                "Some packages changed. Refresh and review the remaining updates.",
                completed,
                ("A pretend dependency could not be updated.",),
            )
        return OperationResult(Outcome.SUCCESS, "The system packages were updated.", completed)

    self_update.SelfUpdater.check = check
    self_update.SelfUpdater.execute = execute


def main():
    mode = next((arg for arg in sys.argv[1:] if arg in MODES), "")
    restarted = os.environ.pop("HOUSEKEEPER_MOCK_RESTARTED", "") == "1"
    subprocess.run(["meson", "compile", "-C", str(BUILD)], check=True)
    sys.path.insert(0, str(ROOT / "src"))
    os.environ["HOUSEKEEPER_RESOURCE_FILE"] = str(BUILD / "data/housekeeper.gresource")
    os.environ["GSETTINGS_SCHEMA_DIR"] = str(BUILD / "data")
    os.environ["GSETTINGS_BACKEND"] = "memory"
    os.environ["HOUSEKEEPER_ICON_DIR"] = str(ROOT / "data/icons")
    install_mock(mode, restarted)
    from housekeeper.application import main as run

    if restarted:
        print(f"Restarted after the pretend update (process {os.getpid()}).", file=sys.stderr)
    return run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
