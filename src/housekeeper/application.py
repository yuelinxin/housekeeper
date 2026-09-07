"""Application startup and installed/build-tree resource resolution."""

import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk

from housekeeper import APP_ID, VERSION
from housekeeper.diagnostics import configure_logging


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if "--version" in argv:
        print(f"Housekeeper {VERSION}")
        return 0
    if os.geteuid() == 0:
        print("Run Housekeeper as a regular desktop user, not as root.", file=sys.stderr)
        return 1
    configure_logging()
    if Gtk.get_major_version() != 4 or Gtk.get_minor_version() < 12:
        print("Housekeeper requires GTK 4.12 or newer.", file=sys.stderr)
        return 1
    if Adw.get_major_version() < 1 or (
        Adw.get_major_version() == 1 and Adw.get_minor_version() < 4
    ):
        print("Housekeeper requires libadwaita 1.4 or newer.", file=sys.stderr)
        return 1
    resource_path = os.environ.get("HOUSEKEEPER_RESOURCE_FILE")
    if not resource_path:
        print(
            "Run Housekeeper using its installed command or the build-tree launcher.",
            file=sys.stderr,
        )
        return 1
    resource = Gio.Resource.load(resource_path)
    Gio.resources_register(resource)
    from housekeeper.ui.window import HousekeeperWindow

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        def do_activate(self):
            window = self.get_active_window()
            if window is None:
                settings = Gio.Settings.new(APP_ID)
                provider = Gtk.CssProvider()
                provider.load_from_resource("/io/github/yuelinxin/housekeeper/style.css")
                display = Gdk.Display.get_default()
                Gtk.StyleContext.add_provider_for_display(
                    display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
                icon_dir = os.environ.get("HOUSEKEEPER_ICON_DIR")
                if icon_dir and Path(icon_dir).is_dir():
                    Gtk.IconTheme.get_for_display(display).add_search_path(icon_dir)
                window = HousekeeperWindow(self, settings)
            window.present()

    return Application().run(argv)
