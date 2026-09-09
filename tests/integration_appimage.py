"""Use the real GIO Trash implementation on an isolated user's temporary files."""

import os
from pathlib import Path

from appimage_fixture import image_bytes

from housekeeper.discovery import read_entry
from housekeeper.identity import classify
from housekeeper.models import Outcome
from housekeeper.providers.appimage import AppImageProvider

if os.environ.get("HOUSEKEEPER_DISPOSABLE_TEST") != "1" or not (
    Path("/run/.containerenv").exists() or Path("/.dockerenv").exists()
):
    raise SystemExit("This script requires the disposable test container.")
home = Path.home()
binary = home / "HousekeeperFixture.AppImage"
binary.write_bytes(image_bytes())
binary.chmod(0o755)
launcher = home / ".local/share/applications/housekeeper-fixture.desktop"
launcher.parent.mkdir(parents=True, exist_ok=True)
launcher.write_text("[Desktop Entry]\nType=Application\nName=Fixture\nExec=" + str(binary) + "\n")
data = home / ".config/housekeeper-fixture/keep-me"
data.parent.mkdir(parents=True, exist_ok=True)
data.write_text("Preserve this unrelated application data.")
app = classify(read_entry(launcher, launcher.parent, {"GNOME"}))
manager = AppImageProvider()
plan = manager.prepare(app, [app])
result = manager.execute(app, plan, lambda *_: None)
assert result.outcome == Outcome.SUCCESS, result
assert not binary.exists() and not launcher.exists()
assert data.exists()
trash = home / ".local/share/Trash/files"
assert any(p.name.startswith(binary.name) for p in trash.iterdir())
assert any(p.name.startswith(launcher.name) for p in trash.iterdir())
print("PASS: real GIO Trash, exact files, and application-data preservation")
