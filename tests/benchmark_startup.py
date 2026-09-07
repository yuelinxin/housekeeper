"""Measure a read-only real inventory startup using temporary in-memory settings."""

import os
import resource
import sys
import time
from pathlib import Path

started = time.perf_counter()
root = Path(__file__).resolve().parents[1]
build = Path(os.environ.get("HOUSEKEEPER_BUILD_DIR", root / "build"))
sys.path.insert(0, str(root / "src"))
os.environ["GSETTINGS_BACKEND"] = "memory"
os.environ["GSETTINGS_SCHEMA_DIR"] = str(build / "data")
os.environ["HOUSEKEEPER_RESOURCE_FILE"] = str(build / "data/housekeeper.gresource")
os.environ["HOUSEKEEPER_ICON_DIR"] = str(root / "data/icons")

from gi.repository import Gio, GLib

from housekeeper.application import main

Gio.resources_register(Gio.Resource.load(os.environ["HOUSEKEEPER_RESOURCE_FILE"]))
from housekeeper.ui.window import HousekeeperWindow

initialize = HousekeeperWindow.__init__
complete = HousekeeperWindow._complete


def measured_initialize(self, *args):
    initialize(self, *args)

    def frame(widget, _clock):
        print(f"First frame: {time.perf_counter() - started:.3f}s")
        return GLib.SOURCE_REMOVE

    self.add_tick_callback(frame)


def measured_complete(self, records, warnings, roots, installations):
    complete(self, records, warnings, roots, installations)
    print(f"Enriched inventory ready: {time.perf_counter() - started:.3f}s")
    print(f"Records: {len(records)}; visible: {sum(a.visible for a in records)}")
    print(f"Provider warnings: {len(warnings)}")

    def finish():
        print(
            f"Startup peak RSS: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.1f} MiB"
        )
        application = self.get_application()
        self.close()
        application.quit()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(250, finish)


HousekeeperWindow.__init__ = measured_initialize
HousekeeperWindow._complete = measured_complete
raise SystemExit(main([sys.argv[0]]))
