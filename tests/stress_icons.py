"""Exercise SVG font rendering in fresh GTK processes using only generated icons."""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def create_icons(directory):
    icons = directory / "hicolor/scalable/apps"
    icons.mkdir(parents=True)
    (directory / "hicolor/index.theme").write_text(
        "[Icon Theme]\nName=Hicolor\nDirectories=scalable/apps\n"
        "[scalable/apps]\nSize=64\nType=Scalable\nMinSize=16\nMaxSize=256\n"
        "Context=Applications\n"
    )
    for index in range(180):
        (icons / f"housekeeper-stress-{index}.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
            '<rect width="64" height="64" fill="#3584e4"/>'
            '<text x="2" y="36" font-family="sans-serif" font-size="20" fill="white">'
            f"App {index}</text></svg>"
        )


def run_child(directory):
    import faulthandler

    import gi

    faulthandler.enable()
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk

    sys.path.insert(0, str(ROOT / "src"))
    from housekeeper.ui.icons import icon_image, set_icon

    errors = []
    application = Adw.Application(
        application_id="io.github.yuelinxin.housekeeper.IconStress",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )

    def failed(kind, value, trace):
        sys.__excepthook__(kind, value, trace)
        errors.append(str(value))
        application.quit()

    sys.excepthook = failed

    def activate(app):
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        theme.add_search_path(str(directory))
        window = Adw.ApplicationWindow(application=app, default_width=1000, default_height=720)
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=12)
        pictures = []
        for index in range(180):
            name = f"housekeeper-stress-{index}"
            icon = (
                str(directory / "hicolor/scalable/apps" / (name + ".svg"))
                if index % 4 == 0
                else name
            )
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            picture = icon_image(icon, 48)
            pictures.append(picture)
            box.append(picture)
            box.append(Gtk.Label(label=f"Application {index}"))
            grid.append(box)
        for missing in ("", "housekeeper-missing-icon", str(directory / "missing.svg")):
            picture = icon_image(missing, 48)
            pictures.append(picture)
            grid.append(picture)
        window.set_content(Gtk.ScrolledWindow(child=grid))
        window.present()

        def refresh_icons():
            assert window.get_mapped()
            for picture in pictures:
                # A named Gtk.Image implicitly enables asynchronous icon preloading.
                assert picture.get_storage_type() == Gtk.ImageType.PAINTABLE
                assert picture.get_paintable() is not None
                picture.set_pixel_size(40)
                picture.set_direction(Gtk.TextDirection.RTL)
                assert picture.get_paintable().get_intrinsic_width() == 40
            set_icon(pictures[0], "housekeeper-stress-1")
            theme.emit("changed")
            assert (
                pictures[0].get_paintable().get_file().get_basename() == "housekeeper-stress-1.svg"
            )
            return GLib.SOURCE_REMOVE

        def finish():
            window.close()
            assert all(picture._housekeeper_icon_theme is None for picture in pictures)
            app.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(500, refresh_icons)
        GLib.timeout_add(1000, finish)

    application.connect("activate", activate)
    GLib.timeout_add_seconds(
        10, lambda: (errors.append("Icon test timed out"), application.quit(), False)[2]
    )
    application.run([sys.argv[0]])
    return bool(errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--child", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return run_child(args.child)
    if args.runs < 1:
        parser.error("--runs must be positive")
    environment = {
        **os.environ,
        "GTK_A11Y": "none",
        "GIO_USE_VFS": "local",
        "GSETTINGS_BACKEND": "memory",
    }
    with tempfile.TemporaryDirectory(prefix="housekeeper-icons-") as temporary:
        directory = Path(temporary)
        create_icons(directory)
        for index in range(args.runs):
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--child", str(directory)],
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode:
                print(result.stderr, file=sys.stderr)
                print(
                    f"SVG icon startup {index + 1} failed with exit {result.returncode}",
                    file=sys.stderr,
                )
                return 1
    print(f"PASS: {args.runs} fresh SVG icon startups, theme refresh, resizing, and cleanup")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
