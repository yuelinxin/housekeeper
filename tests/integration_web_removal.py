"""Create and remove a website launcher with real GIO Trash in a disposable container."""

import os
from pathlib import Path
from unittest.mock import patch

from housekeeper.installations import Installer, InstallRequest
from housekeeper.models import Action, Outcome
from housekeeper.providers.web import WebLauncherProvider
from housekeeper.services import collect

if os.environ.get("HOUSEKEEPER_DISPOSABLE_TEST") != "1" or not (
    Path("/run/.containerenv").exists() or Path("/.dockerenv").exists()
):
    raise SystemExit("This script requires the disposable test container.")

home = Path.home()
browser = home / "google-chrome"
browser.write_text("#!/bin/sh\nexit 1\n")
browser.chmod(0o755)
data = home / ".config/google-chrome/keep-me"
data.parent.mkdir(parents=True, exist_ok=True)
data.write_text("Preserve browser data.")
with (
    patch("housekeeper.installations.shutil.which", return_value=str(browser)),
    patch("housekeeper.installations.website_icon", return_value="web-browser"),
):
    installed = Installer().install(InstallRequest("web", "https://example.org"), lambda *_: None)
launcher = Path(installed.completed[0])
apps, *_ = collect(roots=[launcher.parent], indexes=[])
app = next(app for app in apps if app.provider == "web-launcher")
assert app.action == Action.UNINSTALL
manager = WebLauncherProvider()
plan = manager.prepare(app, apps)
result = manager.execute(app, plan, lambda *_: None)
assert result.outcome == Outcome.SUCCESS, result
assert not launcher.exists() and browser.exists()
assert data.read_text() == "Preserve browser data."
trash = home / ".local/share/Trash"
assert (trash / "files" / launcher.name).exists()
assert (trash / "info" / (launcher.name + ".trashinfo")).exists()
assert not collect(roots=[launcher.parent], indexes=[])[0]
print("PASS: website creation, direct Uninstall, real GIO Trash and browser-data preservation")
