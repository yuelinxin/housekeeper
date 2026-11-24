"""Verify the installed Arch package against the real pacman local database."""

import os
import subprocess
import sys
from pathlib import Path

if not (Path("/run/.containerenv").exists() or Path("/.dockerenv").exists()):
    raise SystemExit("This script requires a disposable container.")

os.environ["GSETTINGS_BACKEND"] = "memory"
sys.path.insert(0, "/usr/share/housekeeper")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from housekeeper import APP_ID, VERSION  # noqa: E402
from housekeeper.appearance import load_icon_image  # noqa: E402
from housekeeper.attribution import attribute  # noqa: E402
from housekeeper.discovery import read_entry  # noqa: E402
from housekeeper.identity import classify  # noqa: E402
from housekeeper.models import Action, FileOwnershipState, Source, UpdateAction  # noqa: E402
from housekeeper.ownership import FileOwnershipIndex  # noqa: E402
from housekeeper.platforms import native_package_source  # noqa: E402
from housekeeper.providers.packages import PackageIndex, pacman_packages  # noqa: E402
from housekeeper.self_update import InstallMethod, detect_installation  # noqa: E402
from housekeeper.storage import measure_storage  # noqa: E402
from housekeeper.updates import assign_update_action  # noqa: E402

assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 12)
assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 4)
assert native_package_source() == ("pacman", "Pacman")
assert Gio.SettingsSchemaSource.get_default().lookup(APP_ID, True) is not None
assert Gio.Resource.load("/usr/share/housekeeper/housekeeper.gresource") is not None
assert load_icon_image(f"/usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg").get_width() > 0
applications = Path("/usr/share/applications")
entry = read_entry(applications / f"{APP_ID}.desktop", applications, {"KDE"})
assert entry.visible and entry.resolved_executable == "/usr/bin/housekeeper", entry
app = attribute(classify(entry), (PackageIndex(),))
package = next(p for p in pacman_packages(strict=True) if p.name == "housekeeper")
assert app.source == Source.PACMAN and app.version == f"{VERSION}-1", app
assert app.action == Action.NONE
assert app.software_size == package.size and package.size > 0
assert measure_storage(app).software == package.size
assert app.updated_at == package.updated_at and package.updated_at is not None
assign_update_action(app, {})
assert app.update_action == UpdateAction.INSTRUCTIONS
owned = FileOwnershipIndex().query(Path("/usr/share/housekeeper/housekeeper/__init__.py"))
assert owned.state == FileOwnershipState.OWNED and len(owned.owners) == 1, owned
assert owned.owners[0].endswith(":housekeeper"), owned
assert (
    subprocess.check_output(["pacman", "-Qqo", str(entry.path)], text=True).strip() == "housekeeper"
)
installation = detect_installation()
assert installation.method == InstallMethod.PACMAN and installation.package == "housekeeper"
assert not installation.updatable and installation.command == "sudo pacman -Syu", installation
assert VERSION in subprocess.check_output(["housekeeper", "--version"], text=True)
files = subprocess.check_output(["pacman", "-Qlq", "housekeeper"], text=True).splitlines()
# Bytecode is packaged so pacman owns and removes it, and only for this module tree.
bytecode = [path for path in files if path.endswith(".pyc")]
assert bytecode and all(
    path.startswith("/usr/share/housekeeper/housekeeper/")
    and f".{sys.implementation.cache_tag}." in path
    for path in bytecode
), bytecode
assert Path("/usr/share/housekeeper/icon-licenses/COPYING_LGPL").is_file()
print(
    "PASS: Arch runtime, SVG, schema, desktop entry, pacman attribution/size/date and self-installation"
)
