"""Check the installed DEB in a container, without touching a desktop session."""

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
from housekeeper.discovery import read_entry  # noqa: E402
from housekeeper.providers.deb import DebIndex  # noqa: E402
from housekeeper.self_update import InstallMethod, detect_installation  # noqa: E402

assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 12)
assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 4)
schema = Gio.SettingsSchemaSource.get_default().lookup(APP_ID, True)
assert schema is not None, "The DEB did not compile the installed GSettings schema"
resource = Gio.Resource.load("/usr/share/housekeeper/housekeeper.gresource")
assert resource is not None
assert Path(f"/usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg").is_file()
assert load_icon_image(f"/usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg").get_width() > 0
applications = Path("/usr/share/applications")
entry = read_entry(applications / f"{APP_ID}.desktop", applications, {"KDE"})
assert entry.visible, entry.reason
assert entry.executable == "/usr/bin/housekeeper", entry
owners = DebIndex().candidates(entry)
assert len(owners) == 1, owners
installation = detect_installation()
assert installation.method == InstallMethod.DEB, installation
assert installation.package == "housekeeper" and not installation.updatable, installation
output = subprocess.check_output(["housekeeper", "--version"], text=True)
assert VERSION in output, output
# Bytecode must be generated on installation by dh_python3, never shipped from
# a build machine with a different interpreter.
files = subprocess.check_output(["dpkg-query", "-L", "housekeeper"], text=True).splitlines()
assert not any(path.endswith(".pyc") or "__pycache__" in path for path in files)
print("PASS: installed DEB runtime, resources, schema, launcher and dpkg ownership")
