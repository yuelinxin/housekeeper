"""Update local Flatpak commits and runtimes in an isolated container installation."""

import os
import subprocess
from dataclasses import replace
from pathlib import Path

from housekeeper.batch_updates import UpdateBatch
from housekeeper.models import Outcome, UpdateAction, UpdateState
from housekeeper.providers.flatpak import FlatpakIndex, FlatpakProvider

if (
    os.environ.get("HOUSEKEEPER_DISPOSABLE_TEST") != "1"
    or not (Path("/run/.containerenv").exists() or Path("/.dockerenv").exists())
    or os.geteuid() == 0
):
    raise SystemExit("Run as an unprivileged user in the disposable test container.")

home = Path.home()
repository = home / "update-repository"
app_id = "org.example.HousekeeperUpdate"
runtime_id = "org.example.HousekeeperPlatform"
second_id = "org.example.HousekeeperSecondUpdate"
arch = os.uname().machine


def run(*args):
    subprocess.run(args, check=True)


def publish(version):
    for identity, runtime in ((runtime_id, True), (app_id, False), (second_id, False)):
        build = home / f"update-build-{version}-{identity}"
        content = build / ("usr" if runtime else "files")
        (content / "bin").mkdir(parents=True)
        (build / "files").mkdir(exist_ok=True)
        (content / "bin/example").write_text(f"#!/bin/sh\n# version {version}\nexit 0\n")
        (content / "bin/example").chmod(0o755)
        (build / "metadata").write_text(
            f"[{'Runtime' if runtime else 'Application'}]\n"
            f"name={identity}\n"
            f"runtime={runtime_id}/{arch}/stable\nsdk={runtime_id}/{arch}/stable\n"
            + ("" if runtime else "command=example\n")
        )
        run("flatpak", "build-finish", str(build))
        args = ["flatpak", "build-export"]
        if runtime:
            args.append("--runtime")
        run(*args, str(repository), str(build), "stable")


def selected():
    return next(a for a in FlatpakIndex().apps if a.metadata["app_id"] == app_id)


publish(1)
run("flatpak", "--user", "remote-add", "--no-gpg-verify", "update-fixture", str(repository))
run("flatpak", "--user", "install", "-y", "update-fixture", app_id, second_id)
data = home / f".var/app/{app_id}/keep-me"
data.parent.mkdir(parents=True)
data.write_text("Personal data survives updates.")
app = selected()
old_commit = app.metadata["commit"]
provider = FlatpakProvider()
assert not provider._update_transaction(app)[2].get_no_interaction()
assert provider.prepare_update(app, [app], lambda *_: None).state == UpdateState.CURRENT
publish(2)
plan = FlatpakProvider().prepare_update(app, [app], lambda *_: None).plan
assert plan is not None and len(plan.changes) == 2
assert selected().metadata["commit"] == old_commit, "Preview deployed an update"
assert plan.target != old_commit
result = FlatpakProvider().execute_update(app, plan, lambda *_: None)
assert result.outcome == Outcome.SUCCESS, result
assert selected().key == app.key
assert selected().metadata["commit"] == plan.target
assert data.read_text() == "Personal data survives updates."
current = selected()
assert (
    FlatpakProvider().prepare_update(current, [current], lambda *_: None).state
    == UpdateState.CURRENT
)
publish(3)
stale = FlatpakProvider().prepare_update(current, [current], lambda *_: None).plan
publish(4)
result = FlatpakProvider().execute_update(current, stale, lambda *_: None)
assert result.outcome == Outcome.FAILED, result
assert selected().metadata["commit"] == current.metadata["commit"]
print(
    "PASS: Flatpak application/runtime update, no-update check, changed dependency rejection, and data preservation"
)

# Both previews include the shared runtime; only the first execution should update it.
apps = [
    replace(a, update_action=UpdateAction.CHECK)
    for a in FlatpakIndex().apps
    if a.metadata["app_id"] in {app_id, second_id}
]
worker = UpdateBatch(lambda _a: FlatpakProvider(), apps)
report = worker.check(lambda *_: None)
assert not report.errors and len(report.items) == 2, report
assert all(len(item.plan.changes) == 2 for item in report.items)
result = UpdateBatch(lambda _a: FlatpakProvider(), apps).execute(report.items, lambda *_: None)
assert result.outcome == Outcome.SUCCESS, result
installed = {a.key: a for a in FlatpakIndex().apps}
for item in report.items:
    assert installed[item.app.key].metadata["commit"] == item.plan.target
assert data.read_text() == "Personal data survives updates."
print("PASS: real two-app Flatpak batch with a shared runtime and exact committed targets")
