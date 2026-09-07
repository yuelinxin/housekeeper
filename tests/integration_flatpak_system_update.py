"""Reproduce commit pinning failure and verify Polkit-mediated system Flatpak updates."""

import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

from housekeeper.models import Outcome, UpdateChange, UpdatePlan
from housekeeper.providers.flatpak import (
    FlatpakIndex,
    FlatpakProvider,
    configured_installations,
    load_flatpak,
)

if os.environ.get("HOUSEKEEPER_DISPOSABLE_TEST") != "1" or not (
    Path("/run/.containerenv").exists() or Path("/.dockerenv").exists()
):
    raise SystemExit("Only run in the disposable integration container.")

root = Path(__file__).resolve().parents[1]
top = Path("/tmp/housekeeper-system-flatpak")
repository = top / "repository"
app_id = "org.example.HousekeeperSystemUpdate"
runtime_id = "org.example.HousekeeperSystemPlatform"
arch = os.uname().machine


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def selected():
    return next(
        a for a in FlatpakIndex().apps if a.metadata["app_id"] == app_id and a.scope == "System"
    )


def user_check(mode):
    from gi.repository import GLib

    app = selected()
    before = app.metadata["commit"]
    provider = FlatpakProvider()
    marker = Path.home() / f".var/app/{app_id}/keep-me"
    marker.parent.mkdir(parents=True, exist_ok=True)
    if not marker.exists():
        marker.write_text("Keep personal data")
    if mode == "stale":
        value = json.loads((Path.home() / "system-flatpak-plan.json").read_text())
        value["changes"] = tuple(UpdateChange(**c) for c in value["changes"])
        plan = UpdatePlan(**value)
    else:
        plan = provider.prepare_update(app, [app], lambda *_: None).plan
        assert plan is not None
    if mode == "legacy":
        fp = load_flatpak()
        installation = next(
            i
            for i in configured_installations(fp)[0]
            if i.get_path().get_path() == plan.installation
        )
        tx = fp.Transaction.new_for_installation(installation, None)
        tx.set_disable_dependencies(True)
        tx.set_disable_related(True)
        tx.add_update(app.identity, None, plan.target)
        try:
            tx.run(None)
        except GLib.Error as error:
            assert "specific commit without root permissions" in error.message, error
        else:
            raise AssertionError("Expected the original system commit-pinning failure")
        assert selected().metadata["commit"] == before
        print("PASS: reproduced the original specific-commit root-permission error")
        return
    if mode == "preview":
        (Path.home() / "system-flatpak-plan.json").write_text(json.dumps(asdict(plan)))
        assert selected().metadata["commit"] == before
        return
    can_cancel_now = False
    download_seen, finished = Event(), Event()
    cancellation_errors = []

    def progress(_message, _fraction, can_cancel):
        nonlocal can_cancel_now
        can_cancel_now = can_cancel

    def cancel_download():
        while not finished.wait(0.01):
            if (top / "download-started").exists():
                download_seen.set()
                if not can_cancel_now:
                    cancellation_errors.append("Extra-data downloads must allow cancellation")
                provider.request_cancel()
                return

    monitor = Thread(target=cancel_download, daemon=True) if mode == "cancel" else None
    if monitor:
        monitor.start()
    # Match the desktop: transactions run in a worker while GLib delivers progress.
    loop = GLib.MainLoop()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(provider.execute_update, app, plan, progress)
        future.add_done_callback(lambda _future: GLib.idle_add(loop.quit))
        loop.run()
        result = future.result()
    finished.set()
    if monitor:
        monitor.join()
    if mode == "cancel":
        assert download_seen.is_set(), ("The extra-data download did not start", result)
        assert not cancellation_errors, cancellation_errors
        assert result.outcome in {Outcome.CANCELLED, Outcome.PARTIAL}, result
        assert selected().metadata["commit"] == before
        assert marker.read_text() == "Keep personal data"
        print(
            "PASS: cancelled an actual system Flatpak extra-data download without changing the app"
        )
        return
    if mode in {"denied", "stale"}:
        assert result.outcome == Outcome.FAILED, result
        assert selected().metadata["commit"] == before
        if mode == "denied":
            assert any(
                "not allowed" in error.lower()
                or "denied" in error.lower()
                or "authorized" in error.lower()
                for error in result.errors
            ), result
        else:
            assert any("plan changed" in error for error in result.errors), result
        print(f"PASS: system Flatpak {mode} update does not deploy")
    else:
        assert result.outcome == Outcome.SUCCESS, result
        assert selected().metadata["commit"] == plan.target != before
        assert marker.read_text() == "Keep personal data"
        print("PASS: system Flatpak update through Polkit helper, exact commit and retained data")


if os.geteuid() != 0:
    user_check(sys.argv[1])
    raise SystemExit(0)

keys = Path("/tmp/housekeeper-update-rpms/keys")
key_listing = run(
    "gpg", "--homedir", str(keys), "--with-colons", "--list-keys", capture_output=True, text=True
)
fingerprint = next(
    line.split(":")[9] for line in key_listing.stdout.splitlines() if line.startswith("fpr:")
)


def publish(version, extra_data=None):
    for identity, runtime in ((runtime_id, True), (app_id, False)):
        build = top / f"build-{identity}-{version}"
        content = build / ("usr" if runtime else "files")
        (content / "bin").mkdir(parents=True)
        (build / "files").mkdir(exist_ok=True)
        (content / "bin/example").write_text(f"#!/bin/sh\n# version {version}\nexit 0\n")
        (content / "bin/example").chmod(0o755)
        (build / "metadata").write_text(
            f"[{'Runtime' if runtime else 'Application'}]\nname={identity}\n"
            f"runtime={runtime_id}/{arch}/stable\nsdk={runtime_id}/{arch}/stable\n"
            + ("" if runtime else "command=example\n")
        )
        extra_args = []
        if extra_data and not runtime:
            extra_args = ["--extra-data=" + extra_data]
            script = content / "bin/apply_extra"
            script.write_text("#!/bin/sh\nexit 0\n")
            script.chmod(0o755)
        run("flatpak", "build-finish", *extra_args, str(build))
        run(
            "flatpak",
            "build-export",
            *(["--runtime"] if runtime else []),
            f"--gpg-sign={fingerprint}",
            f"--gpg-homedir={keys}",
            str(repository),
            str(build),
            "stable",
        )


def authorize(allowed):
    Path("/etc/polkit-1/rules.d/00-housekeeper-flatpak.rules").write_text(
        'polkit.addRule(function(action, subject) { if (subject.user == "hk-test" && '
        'action.id.indexOf("org.freedesktop.Flatpak.") == 0) { '
        # Checking repository metadata must remain possible for both policy outcomes.
        'if (action.id == "org.freedesktop.Flatpak.metadata-update" || '
        'action.id == "org.freedesktop.Flatpak.update-remote") return polkit.Result.YES; '
        f"return polkit.Result.{'YES' if allowed else 'NO'}; }} }});\n"
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
        # The container has no /dev/fuse; use Flatpak's signed child-repository fallback.
        "FLATPAK_REVOKEFS_FUSE=/usr/bin/false",
        "dbus-run-session",
        f"--config-file={root / 'tests/session.conf'}",
        "--",
        "/usr/bin/python3",
        __file__,
        mode,
    )


publish(1)
run(
    "flatpak",
    "--system",
    "remote-add",
    "--gpg-import=/tmp/housekeeper-update-rpms/fixture.asc",
    "system-update-fixture",
    str(repository),
)
run("flatpak", "--system", "install", "-y", "system-update-fixture", app_id)
publish(2)
authorize(True)
as_user("legacy")
authorize(False)
as_user("denied")
authorize(True)
as_user("preview")
publish(3)
as_user("stale")
as_user("update")


# Slow localhost payload reproduces the WeChat extra-data download stage, offline.
payload = b"housekeeper extra data\n" * 200000


class PayloadHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            for offset in range(0, len(payload), 32768):
                chunk = payload[offset : offset + 32768]
                self.wfile.write(chunk)
                self.wfile.flush()
                self.server.bytes_sent += len(chunk)
                (top / "download-started").touch()
                time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError):
            pass


server = ThreadingHTTPServer(("127.0.0.1", 0), PayloadHandler)
server.bytes_sent = 0
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    url = f"http://127.0.0.1:{server.server_port}/fixture.bin"
    publish(4, f"fixture.bin:{hashlib.sha256(payload).hexdigest()}:{len(payload)}:0:{url}")
    as_user("cancel")
    assert 0 < server.bytes_sent < len(payload), server.bytes_sent
finally:
    server.shutdown()
    server.server_close()
    thread.join()
