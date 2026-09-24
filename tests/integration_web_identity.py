"""Check actual Chrome window identities without touching the user's browser profile."""

import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from gi.repository import Gio, GLib

from housekeeper.installations import Installer, InstallRequest

if os.environ.get("HOUSEKEEPER_DISPOSABLE_TEST") != "1" or not Path("/run/.containerenv").exists():
    raise SystemExit("This script requires the disposable test container.")


def wait_for(check):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)
    raise AssertionError("Window identity did not appear before the timeout")


def run(platform, root):
    directory = root / platform
    directory.mkdir()
    runtime = directory / "runtime"
    runtime.mkdir(mode=0o700)
    env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), XDG_DATA_HOME=str(directory / "data"))
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    env["NO_AT_BRIDGE"] = "1"
    if platform == "wayland":
        env.update(WAYLAND_DISPLAY="wayland-test", WAYLAND_DEBUG="client")
        display_command = [
            "weston",
            "--backend=headless",
            "--renderer=pixman",
            "--socket=wayland-test",
            "--idle-time=0",
        ]
    else:
        env["DISPLAY"] = ":97"
        display_command = ["Xvfb", ":97", "-screen", "0", "1024x768x24", "-ac"]
    processes = []
    with (
        (directory / "display.log").open("w") as display_log,
        (directory / "chrome.log").open("w") as log,
    ):

        def start(argv, output=log):
            process = subprocess.Popen(
                argv, env=env, stdout=output, stderr=output, start_new_session=True
            )
            processes.append(process)
            return process

        try:
            display = start(display_command, display_log)
            wait_for(
                lambda: (
                    (runtime / "wayland-test").exists()
                    if platform == "wayland"
                    else Path("/tmp/.X11-unix/X97").exists()
                )
            )
            assert display.poll() is None, (directory / "display.log").read_text()
            binary = "/opt/google/chrome/chrome"
            # These flags belong only to the disposable test process, never desktop files.
            options = [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--password-store=basic",
                "--user-data-dir=" + str(directory / "profile"),
                "--ozone-platform=" + platform,
            ]
            browser = start([binary, *options, "--profile-directory=Default", "about:blank"])

            def classes():
                tree = subprocess.check_output(["xwininfo", "-root", "-tree"], env=env, text=True)
                return tree

            if platform == "wayland":
                wait_for(lambda: "set_app_id(" in (directory / "chrome.log").read_text())
            else:
                wait_for(lambda: "Google-chrome" in classes())
            assert browser.poll() is None
            with (
                patch.dict(os.environ, env),
                patch("housekeeper.installations.shutil.which", return_value=binary),
                patch("housekeeper.installations.website_icon", return_value="web-browser"),
            ):
                for url in (
                    "https://apple.com/",
                    "https://example.org/notes",
                    "https://example.org/a%20b?q=one",
                ):
                    installed = Installer().install(
                        InstallRequest("web", url, "Website Fixture"), lambda *_: None
                    )
                    desktop = Path(installed.completed[0])
                    info = Gio.DesktopAppInfo.new_from_filename(str(desktop))
                    assert info.get_name() == "Website Fixture"
                    keyfile = GLib.KeyFile()
                    keyfile.load_from_file(str(desktop), GLib.KeyFileFlags.NONE)
                    argv = GLib.shell_parse_argv(keyfile.get_string("Desktop Entry", "Exec"))[1]
                    argv = [arg.replace("%%", "%") for arg in argv]
                    launch = start([argv[0], *options, *argv[1:]])
                    assert launch.wait(timeout=15) == 0  # Reuses the already running browser.
                    if platform == "wayland":
                        expected = f'set_app_id("{desktop.stem}")'
                        wait_for(
                            lambda expected=expected: (
                                expected in (directory / "chrome.log").read_text()
                            )
                        )
                    else:
                        expected = re.escape(
                            '("' + info.get_startup_wm_class() + '" "Google-chrome")'
                        )
                        wait_for(lambda expected=expected: re.search(expected, classes()))
                    print("PASS:", platform, desktop.name, flush=True)
        except Exception:
            print((directory / "display.log").read_text()[-2000:])
            print((directory / "chrome.log").read_text()[-5000:])
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()


with tempfile.TemporaryDirectory(prefix="housekeeper-web-identity-") as temporary:
    for backend in ("x11", "wayland"):
        run(backend, Path(temporary))
