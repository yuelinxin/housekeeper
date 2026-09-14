"""Exercise the real GTK widgets using synthetic application data, never removal."""

import os
import resource
import sys
import tempfile
import time
import traceback
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BUILD = Path(os.environ.get("HOUSEKEEPER_BUILD_DIR", ROOT / "build"))
os.environ["GSETTINGS_SCHEMA_DIR"] = str(BUILD / "data")
os.environ["GSETTINGS_BACKEND"] = "memory"
cache_directory = tempfile.TemporaryDirectory(prefix="housekeeper-smoke-cache-")
os.environ["XDG_CACHE_HOME"] = cache_directory.name
sys.path.insert(0, str(ROOT / "src"))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gsk, Gtk

from housekeeper import APP_ID
from housekeeper.batch_updates import UpdateItem, UpdateReport
from housekeeper.discovery import scan_entries
from housekeeper.identity import classify
from housekeeper.models import (
    Action,
    AppRecord,
    DesktopEntry,
    InstallationInstance,
    ManagementTarget,
    OperationCancelled,
    OperationResult,
    Outcome,
    ProviderCapabilities,
    RemovalPlan,
    Source,
    UpdateAction,
    UpdateChange,
    UpdateCheckResult,
    UpdatePlan,
    UpdateState,
)
from housekeeper.platforms import native_package_source
from housekeeper.services import InventoryService
from housekeeper.storage import StorageUsage
from housekeeper.updates import assign_update_action

Gio.resources_register(Gio.Resource.load(str(BUILD / "data/housekeeper.gresource")))
from housekeeper.ui.update_confirmation import UpdateDetails
from housekeeper.ui.updates import UpdatesPage
from housekeeper.ui.window import HousekeeperWindow


def examples():
    apps = []
    for i, (name, source, icon) in enumerate(
        [
            ("Boxes", Source.RPM, "org.gnome.Boxes"),
            ("Calendar", Source.RPM, "org.gnome.Calendar"),
            ("Firefox", Source.RPM, "firefox"),
            ("Foliate", Source.FLATPAK, "com.github.johnfactotum.Foliate"),
            ("Maps", Source.FLATPAK, "org.gnome.Maps"),
            ("Music", Source.WEB, "org.gnome.Music"),
            ("Notes", Source.APPIMAGE, "org.gnome.TextEditor"),
            ("Photos", Source.FLATPAK, "org.gnome.Photos"),
            ("Steam", Source.RPM, "steam"),
            ("Weather", Source.RPM, "org.gnome.Weather"),
        ]
    ):
        app = AppRecord(
            str(i),
            name,
            source=source,
            provider=source.value,
            icon=icon,
            version="1.0",
            scope="System" if source == Source.RPM else "User",
            software_size=(i + 1) * 1024 * 1024 if source != Source.WEB else None,
            updated_at=1735689600 + i * 86400 if source == Source.RPM else None,
            identity="org.example." + name,
            action=Action.UNINSTALL if source in {Source.RPM, Source.FLATPAK} else Action.NONE,
            update_action=UpdateAction.CHECK
            if source in {Source.RPM, Source.FLATPAK}
            else UpdateAction.INSTRUCTIONS,
            metadata={"name": name.lower(), "arch": "x86_64"},
            entries=[
                DesktopEntry(
                    "org.example." + name + ".desktop",
                    Path("/usr/share/applications") / (name.lower() + ".desktop"),
                    name,
                    icon=icon,
                    executable="/usr/bin/" + name.lower(),
                )
            ],
        )
        apps.append(app)
    return apps


def fake_scan(self, partial, completed, failed):
    self.inventory = examples()
    GLib.idle_add(lambda: (completed(self.inventory, [], [], {}), False)[1])
    return True


InventoryService.scan = fake_scan


def fake_storage(self, app, completed):
    GLib.idle_add(lambda: (completed(StorageUsage(123456789)), False)[1])


InventoryService.measure_storage = fake_storage
failed = []


def exception(kind, value, trace):
    traceback.print_exception(kind, value, trace)
    failed.append(str(value))


sys.excepthook = exception
application = Adw.Application(
    application_id=APP_ID + ".Smoke", flags=Gio.ApplicationFlags.NON_UNIQUE
)
started = time.monotonic()
output = Path(os.environ.get("HOUSEKEEPER_SCREENSHOT_DIR", ROOT / "work/screenshots"))
output.mkdir(parents=True, exist_ok=True)


def capture(window, name):
    paintable = Gtk.WidgetPaintable.new(window)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, window.get_width(), window.get_height())
    node = snapshot.to_node()
    if node is None:
        raise AssertionError("The window did not render")
    renderer = Gsk.CairoRenderer.new()
    renderer.realize(window.get_surface())
    texture = renderer.render_texture(node, None)
    texture.save_to_png(str(output / name))
    renderer.unrealize()


def activate(app):
    css = Gtk.CssProvider()
    css.load_from_resource("/io/github/yuelinxin/housekeeper/style.css")
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    window = HousekeeperWindow(app, Gio.Settings.new(APP_ID))
    window.toast = lambda _text: None
    window.present()
    steps = []
    icon_fixture = {}
    badge_fixture = {}

    def inventory_names():
        return [
            window.filtered.get_item(i).record.name for i in range(window.filtered.get_n_items())
        ]

    def sort_metric(mode):
        container = window.scrolls[mode].get_child().get_first_child().get_first_child()
        return container.get_first_child().get_next_sibling().get_last_child()

    def start_sorting():
        assert window.settings.get_string("sort-mode") == "name"
        assert (
            window.sort_button.get_menu_model()
            .get_item_attribute_value(2, "label", None)
            .get_string()
            == "Last Updated (Newest First)"
        )
        assert inventory_names() == [app.name for app in examples()]
        window.selection.set_selected(0)
        window.lookup_action("sort-mode").activate(GLib.Variant("s", "size"))
        assert window.selection.get_selected_item().record.name == "Boxes"
        assert Gio.Settings.new(APP_ID).get_string("sort-mode") == "size"
        assert inventory_names() == [
            "Weather",
            "Steam",
            "Photos",
            "Notes",
            "Maps",
            "Foliate",
            "Firefox",
            "Calendar",
            "Boxes",
            "Music",
        ]

    def check_size_sorting():
        assert window.selection.get_selected_item().record.name == "Boxes"
        assert sort_metric("list").get_visible()
        assert sort_metric("list").get_label() == GLib.format_size(10 * 1024 * 1024)
        capture(window, "sort-size-list.png")
        window.view_buttons["grid"].set_active(True)

    def check_grid_sorting():
        assert sort_metric("grid").get_label() == GLib.format_size(10 * 1024 * 1024)
        capture(window, "sort-size-grid.png")
        window.search.set_text("flatpak")
        window._search_changed(window.search)
        assert inventory_names() == ["Photos", "Maps", "Foliate"]
        window.lookup_action("sort-mode").activate(GLib.Variant("s", "installed"))
        assert inventory_names() == ["Foliate", "Maps", "Photos"]
        window.search.set_text("")
        window._search_changed(window.search)
        assert inventory_names() == [
            "Weather",
            "Steam",
            "Firefox",
            "Calendar",
            "Boxes",
            "Foliate",
            "Maps",
            "Music",
            "Notes",
            "Photos",
        ]
        changed = examples()
        changed[0] = replace(changed[0], updated_at=1835689600)
        window._complete(changed, [], [], {})
        assert inventory_names()[0] == "Boxes"
        window._complete(examples(), [], [], {})
        assert inventory_names()[0] == "Weather"
        window.view_buttons["list"].set_active(True)
        window.set_default_size(360, 640)

    def check_installation_sorting():
        assert sort_metric("list").get_label() == GLib.DateTime.new_from_unix_local(
            1735689600 + 9 * 86400
        ).format("%Y-%m-%d")
        assert window.split.get_collapsed()
        assert window.get_width() <= 400
        assert window.header.measure(Gtk.Orientation.HORIZONTAL, -1)[0] <= window.get_width()
        for button in window.view_buttons.values():
            assert button.get_mapped()
            bounds = button.compute_bounds(window)[1]
            assert bounds.get_width() > 0
            assert bounds.get_x() >= 0
            assert bounds.get_x() + bounds.get_width() <= window.get_width()
        bounds = window.sort_button.compute_bounds(window)[1]
        assert bounds.get_x() >= 0 and bounds.get_x() + bounds.get_width() <= window.get_width()
        capture(window, "sort-installed-narrow.png")
        window.sort_button.popup()

    def finish_sorting():
        popover = window.sort_button.get_popover()
        assert popover.get_visible() and popover.get_width() <= window.get_width()
        capture(popover, "sort-menu-narrow.png")
        window.sort_button.popdown()
        window.lookup_action("sort-mode").activate(GLib.Variant("s", "name"))
        assert inventory_names() == [app.name for app in examples()]
        window.selection.unselect_all()
        window.set_default_size(1040, 720)

    def start_icon_change():
        directory = tempfile.TemporaryDirectory(prefix="housekeeper-smoke-icons-")
        root = Path(directory.name)
        launcher_root = root / "system/applications"
        launcher_root.mkdir(parents=True)
        source = launcher_root / "example.desktop"
        source.write_text(
            "[Desktop Entry]\nType=Application\nName=Icon Fixture\nExec=/usr/bin/true\nIcon=folder\n"
        )
        image = root / "image.svg"
        image.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" fill="red"/></svg>'
        )
        environment = patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "data")})
        environment.start()
        icon_fixture.update(
            directory=directory, environment=environment, refresh=window.refresh, root=root
        )

        def refresh_fixture():
            entries, warnings = scan_entries([root / "data/applications", launcher_root])
            window._complete([classify(entry) for entry in entries], warnings, [], {})

        window.refresh = refresh_fixture
        window.section = "apps"
        window.detail_app = None
        refresh_fixture()
        app = window.records[0]
        window.show_details(app, replace=True)

        class Chooser:
            def __init__(self, **_kwargs):
                pass

            def open(self, _parent, _cancel, callback):
                callback(self, None)

            def open_finish(self, _result):
                return Gio.File.new_for_path(str(image))

        with patch("housekeeper.ui.window.Gtk.FileDialog", Chooser):
            window.appearance_group.buttons[0][0].emit("clicked")
        assert window.operation_active
        assert not window.appearance_group.buttons[0][0].get_sensitive()
        assert not window.manage_button.get_sensitive()

    def verify_icon_change():
        assert not window.operation_active and not window.service.busy
        group = window.appearance_group
        assert group.buttons[1][0].get_sensitive()
        icon = Path(window.detail_app.icon)
        assert icon.is_file() and icon.suffix == ".png"
        assert group.icon_file.get_subtitle() == str(icon)
        assert group.icon_theme.get_subtitle() == "Custom Icon"
        assert group.icon_file.get_ancestor(Adw.ExpanderRow).get_title() == "Technical Details"
        # The selection callback may arrive after the selected launcher disappears.
        stale = window.detail_app
        window.records = []
        window.change_icon(stale, stale.entries[0], str(icon))
        assert not window.operation_active
        window.records = [stale]
        group.buttons[1][0].emit("clicked")
        assert window.operation_active

    def verify_icon_reset():
        assert not window.operation_active and not window.service.busy
        assert window.detail_app.icon == "folder"
        assert window.appearance_group.icon_theme.get_title() == "Icon Theme"
        assert not window.appearance_group.buttons[1][0].get_sensitive()
        assert not (icon_fixture["root"] / "data/applications/example.desktop").exists()
        window.refresh = icon_fixture["refresh"]
        icon_fixture["environment"].stop()
        icon_fixture["directory"].cleanup()
        window.detail_app = None
        window.navigation.pop_to_tag("overview")
        window._complete(examples(), [], [], {})

    def check_list():
        assert window.filtered.get_n_items() == 10, (
            f"source={window.source}, query={window.query}, count={window.filtered.get_n_items()}"
        )
        assert window.views.get_visible_child_name() == "list"
        capture(window, "list-light.png")
        all_row = window.sidebar.get_row_at_index(0)
        native_key, native_title = native_package_source()
        native_row = window.sidebar.get_row_at_index(1)
        assert native_row.source == native_key
        assert (
            native_row.get_child().get_first_child().get_next_sibling().get_text() == native_title
        )
        window.sidebar.emit("row-activated", native_row)
        assert window.overview_page.get_title() == native_title
        if native_key != "rpm":
            assert window.filtered.get_n_items() == 0
            assert window.empty.get_title() == (
                "No Applications"
                if native_key in {"deb", "pacman", "apk"}
                else "Package Source Not Yet Supported"
            )
            window._replace(examples())
            assert window.source == native_key, "Refresh left the native package tab"
        for source in (Source.DEB, Source.PACMAN, Source.APK, Source.SNAP):
            package_app = replace(
                examples()[0],
                key="package-example",
                source=source,
                provider=source.value,
                identity="example",
                action=Action.NONE,
                update_action=UpdateAction.INSTRUCTIONS,
            )
            window._replace([*examples(), package_app])
            package_row = window.sidebar.get_first_child()
            while package_row.source != source.value:
                package_row = package_row.get_next_sibling()
            window.sidebar.emit("row-activated", package_row)
            assert window.filtered.get_n_items() == 1
            window.show_details(package_app)
            assert window.manage_button.get_label() == "Show Management Instructions"
            assert window.open_button.get_label() == "Open"
            window.navigation.pop_to_tag("overview")
        window._replace(examples())
        rows = []
        child = window.sidebar.get_first_child()
        while child:
            rows.append(child)
            child = child.get_next_sibling()
        # RPM fixtures must keep their actual source even when testing on Ubuntu.
        rpm_row = next(row for row in rows if row.source == "rpm")
        assert rpm_row.get_child().get_first_child().get_next_sibling().get_text() == "RPM"
        window.sidebar.emit("row-activated", rpm_row)
        assert window.filtered.get_n_items() == 5
        flatpak_row = next(row for row in rows if row.source == "flatpak")
        window.sidebar.select_row(flatpak_row)
        window.sidebar.emit("row-activated", flatpak_row)
        assert window.source == "flatpak"
        assert window.filtered.get_n_items() == 3
        window._replace(examples())
        assert window.source == "flatpak"
        window.sidebar.select_row(window.sidebar.get_row_at_index(0))
        window.sidebar.emit("row-activated", window.sidebar.get_row_at_index(0))
        assert window.source == all_row.source == "all"
        assert window.filtered.get_n_items() == 10
        window.search.set_text("flatpak")
        window._search_changed(window.search)
        assert window.filtered.get_n_items() == 3
        window.search.set_text("no-such-application")
        window._search_changed(window.search)
        assert window.views.get_visible_child_name() == "empty"
        window.search.set_text("")
        window._search_changed(window.search)
        window.view_buttons["grid"].set_active(True)

    def hover_list():
        row = window.scrolls["list"].get_child().get_first_child()
        assert row.get_first_child().has_css_class("app-row")
        row.set_state_flags(Gtk.StateFlags.PRELIGHT, False)

    def hover_list_dark():
        capture(window, "list-hover-light.png")
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def finish_list_hover():
        capture(window, "list-hover-dark.png")
        window.scrolls["list"].get_child().get_first_child().unset_state_flags(
            Gtk.StateFlags.PRELIGHT
        )
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

    def check_grid():
        assert window.views.get_visible_child_name() == "grid"
        capture(window, "grid-light.png")
        window.show_details(window.records[0])

    def hover_grid():
        grid = window.scrolls["grid"].get_child()
        child = grid.get_first_child()
        tile = child.get_first_child()
        assert tile.has_css_class("app-tile")
        child.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
        tile.set_state_flags(Gtk.StateFlags.PRELIGHT, False)

    def hover_grid_dark():
        capture(window, "grid-hover-light.png")
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def finish_grid_hover():
        capture(window, "grid-hover-dark.png")
        window.selection.set_selected(0)

    def selected_grid_hover():
        child = window.scrolls["grid"].get_child().get_first_child()
        assert child.get_state_flags() & Gtk.StateFlags.SELECTED
        capture(window, "grid-selected-hover-dark.png")
        child.set_state_flags(Gtk.StateFlags.ACTIVE, False)

    def pressed_grid():
        capture(window, "grid-selected-pressed-dark.png")
        child = window.scrolls["grid"].get_child().get_first_child()
        child.unset_state_flags(Gtk.StateFlags.ACTIVE)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

    def finish_selected_grid():
        capture(window, "grid-selected-hover-light.png")
        child = window.scrolls["grid"].get_child().get_first_child()
        child.unset_state_flags(Gtk.StateFlags.PRELIGHT)
        child.get_first_child().unset_state_flags(Gtk.StateFlags.PRELIGHT)
        window.selection.unselect_all()

    def check_details():
        assert window.detail_app.name == "Boxes"
        assert window.software_size_row.get_subtitle() == GLib.format_size(123456789)
        assert window.open_button.get_label() == "Open"
        assert window.manage_button.get_label() == "Uninstall"
        assert window.manage_button.has_css_class("destructive-action")
        assert not window.open_button.has_css_class("destructive-action")
        assert window.open_button.get_allocation().height > 0
        assert window.appearance_group.buttons[0][0].get_sensitive()
        assert not window.appearance_group.buttons[1][0].get_sensitive()
        capture(window, "details-light.png")
        group = window.appearance_group
        assert group.get_parent().get_last_child() is group
        theme = Gtk.IconTheme.get_for_display(window.get_display())
        theme.emit("changed")
        assert group.icon_theme.get_subtitle() == theme.get_theme_name()
        original = window.detail_app
        unverified = replace(
            original,
            action=Action.NONE,
            metadata={
                **original.metadata,
                "rpm_verified": "false",
                "management_reason": "The launch target could not be verified.",
            },
        )
        assign_update_action(
            unverified, {"rpm": ProviderCapabilities(update_preview=True, update_execute=True)}
        )
        window.show_details(unverified, replace=True)
        assert window.manage_button.get_label() == "Show Management Instructions"
        assert not window.manage_button.has_css_class("destructive-action")
        assert window.open_button.get_label() == "Open"
        with (
            patch.object(window.service, "prepare") as prepare,
            patch.object(window.service, "prepare_update") as prepare_update,
            patch.object(window, "message") as message,
        ):
            window.manage_button.emit("clicked")
            window.check_update(window.detail_app)
            prepare.assert_not_called()
            prepare_update.assert_not_called()
            assert message.call_count == 2
        from housekeeper.attribution import candidate, project, resolve

        claims = tuple(
            candidate(
                source, "/test", source.value, "1", source.value, "/example.desktop", verified=True
            )
            for source in (Source.RPM, Source.DEB)
        )
        conflict = project(original, resolve(claims))
        assign_update_action(
            conflict, {"rpm": ProviderCapabilities(update_preview=True, update_execute=True)}
        )
        window.show_details(conflict, replace=True)
        assert window.manage_button.get_label() == "Show Management Instructions"
        assert window.open_button.get_label() == "Open"
        with (
            patch.object(window.service, "prepare") as prepare,
            patch.object(window.service, "prepare_update") as prepare_update,
            patch.object(window, "message"),
        ):
            window.manage_button.emit("clicked")
            window.check_update(window.detail_app)
            prepare.assert_not_called()
            prepare_update.assert_not_called()
        window.show_details(original, replace=True)
        storage_callbacks = []
        with patch.object(
            window.service,
            "measure_storage",
            side_effect=lambda app, callback: storage_callbacks.append(callback),
        ):
            window.show_details(window.detail_app, replace=True)
            window.show_details(window.detail_app, replace=True)
        storage_callbacks[0](StorageUsage(999))
        assert window.software_size_row.get_subtitle() == "Calculating…"
        storage_callbacks[1](StorageUsage(0))
        assert window.software_size_row.get_subtitle() == GLib.format_size(0)
        changed = list(window.records)
        changed[0] = replace(changed[0], version="2.0")
        window._complete(changed, [], [], {})
        assert window.detail_app.version == "2.0"
        window._complete(changed[1:], [], [], {})
        assert window.detail_notice.get_revealed()
        assert not window.manage_button.get_sensitive()
        assert not window.open_button.get_sensitive()
        assert not window.appearance_group.buttons[0][0].get_sensitive()
        window._complete(examples(), [], [], {})
        window.navigation.pop_to_tag("overview")
        window.view_buttons["list"].set_active(True)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def hover_details_light():
        for button in (window.open_button, window.manage_button):
            button.get_parent().set_state_flags(Gtk.StateFlags.PRELIGHT, False)
            button.set_state_flags(Gtk.StateFlags.PRELIGHT, False)

    def scroll_storage():
        group = window.storage_group
        scroll = group.get_ancestor(Gtk.ScrolledWindow)
        bounds = group.compute_bounds(scroll)[1]
        adjustment = scroll.get_vadjustment()
        adjustment.set_value(adjustment.get_value() + bounds.get_y() - 16)

    def capture_storage():
        capture(window, "storage-light.png")

    def check_flatpak_removal_options():
        record = next(record for record in window.records if record.provider == "flatpak")
        plan = RemovalPlan(
            record.key,
            "flatpak",
            record.identity,
            (record.name,),
            "Shared runtimes are kept.",
            "fixture",
        )
        for keep in (True, False):
            assert window._begin_operation(record, "remove")
            window._confirm(record, plan)
            dialog = window.confirm_dialog
            assert dialog.get_default_response() == "cancel"
            switches = []

            def find_switch(widget, switches=switches):
                if isinstance(widget, Adw.SwitchRow):
                    switches.append(widget)
                child = widget.get_first_child()
                while child:
                    find_switch(child, switches)
                    child = child.get_next_sibling()

            find_switch(dialog.get_extra_child())
            assert len(switches) == 1 and switches[0].get_active()
            switches[0].set_active(keep)
            with patch.object(window, "_execute") as execute:
                dialog.response("remove")
            selected = execute.call_args.args[1]
            assert selected.delete_user_data == (not keep)
            assert not plan.delete_user_data
            window._end_operation()
        assert window._begin_operation(record, "remove")
        window._confirm(record, plan)

    def capture_flatpak_removal():
        capture(window.confirm_dialog, "flatpak-removal.png")
        with patch.object(window, "_execute") as execute:
            window.confirm_dialog.response("cancel")
        execute.assert_not_called()
        record = next(record for record in window.records if record.provider == "rpm")
        plan = RemovalPlan(
            record.key, "rpm", record.identity, (record.name,), "Remove app.", "fixture"
        )
        assert window._begin_operation(record, "remove")
        window._confirm(record, plan)
        assert isinstance(window.confirm_dialog.get_extra_child(), Gtk.ScrolledWindow)
        window.confirm_dialog.response("cancel")

    def scroll_appearance():
        group = window.appearance_group
        scroll = group.get_ancestor(Gtk.ScrolledWindow)
        bounds = group.compute_bounds(scroll)[1]
        adjustment = scroll.get_vadjustment()
        adjustment.set_value(adjustment.get_value() + bounds.get_y() - 16)

    def capture_appearance():
        group = window.appearance_group
        capture(window, "appearance-light.png")
        group.get_ancestor(Gtk.ScrolledWindow).get_vadjustment().set_value(0)

    def hover_details_dark():
        capture(window, "details-hover-light.png")
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def finish_details_hover():
        capture(window, "details-hover-dark.png")
        for button in (window.open_button, window.manage_button):
            button.unset_state_flags(Gtk.StateFlags.PRELIGHT)
            button.get_parent().unset_state_flags(Gtk.StateFlags.PRELIGHT)
            button.grab_focus()
            assert window.get_focus() is button
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

    def check_open_actions():
        records = window.records
        original = records[0]
        for source in Source:
            current = replace(original, source=source)
            window.records = [current]
            window.show_details(current, replace=True)
            assert window.open_button.get_label() == "Open"
            assert window.open_button.get_parent().get_visible()
            with (
                patch("housekeeper.ui.window.Gio.DesktopAppInfo.new_from_filename") as load,
                patch.object(window.service, "prepare_update") as prepare_update,
            ):
                window.open_button.emit("clicked")
                load.assert_called_once_with(str(current.entries[0].path))
                args = load.return_value.launch.call_args.args
                assert args[0] == [] and isinstance(args[1], Gdk.AppLaunchContext)
                prepare_update.assert_not_called()
        with (
            patch("housekeeper.ui.window.Gio.DesktopAppInfo.new_from_filename", return_value=None),
            patch.object(window, "message") as message,
        ):
            window.open_button.emit("clicked")
            assert message.call_args.args[0] == "Could Not Open App"
        with (
            patch("housekeeper.ui.window.Gio.DesktopAppInfo.new_from_filename") as load,
            patch.object(window, "message") as message,
        ):
            load.return_value.launch.side_effect = GLib.Error("Synthetic launch failure")
            window.open_button.emit("clicked")
            assert "Synthetic launch failure" in message.call_args.args[1]

        other_entry = replace(
            original.entries[0], name="Second Launcher", path=Path("/second.desktop")
        )
        multiple = replace(original, entries=[original.entries[0], other_entry])
        window.records = [multiple]
        window.show_details(multiple, replace=True)
        with patch("housekeeper.ui.window.Gio.DesktopAppInfo.new_from_filename") as load:
            window.open_button.emit("clicked")
            dialog = next(
                w
                for w in Gtk.Window.get_toplevels()
                if isinstance(w, Adw.MessageDialog) and w.get_heading() == "Choose a Launcher"
            )
            load.assert_not_called()
            dialog.get_extra_child().set_selected(1)
            dialog.response("open")
            load.assert_called_once_with(str(other_entry.path))
            load.reset_mock()
            window.open_button.emit("clicked")
            dialog = next(
                w
                for w in Gtk.Window.get_toplevels()
                if isinstance(w, Adw.MessageDialog) and w.get_heading() == "Choose a Launcher"
            )
            dialog.response("cancel")
            load.assert_not_called()

        empty = replace(original, entries=[])
        window.records = [empty]
        window.show_details(empty, replace=True)
        assert not window.open_button.get_sensitive()
        window._end_operation()
        assert not window.open_button.get_sensitive()
        window.records = records
        window.show_details(original, replace=True)
        close_messages()

        page = window.updates_page
        saved_items = page.items
        assert not window.update_badge.get_visible()
        plan = UpdatePlan(
            original.key, original.provider, "target", "System", "1", (), "fixture", ""
        )
        alias = replace(original, key="another-launcher")
        page.render((UpdateItem(alias, plan, (alias.name,)),))
        assert window.update_badge.get_visible()
        assert window.open_button.get_label() == "Open"
        assert window.update_badge.get_halign() == Gtk.Align.CENTER
        assert window.update_badge.get_valign() == Gtk.Align.CENTER
        unrelated = replace(original, identity="unrelated", metadata={"name": "unrelated"})
        page.render((UpdateItem(unrelated, plan, (unrelated.name,)),))
        assert not window.update_badge.get_visible()
        page.render((UpdateItem(original, plan, (original.name,)),))
        window.show_details(original, replace=True)
        assert window.update_badge.get_visible()
        page.render(())
        assert not window.update_badge.get_visible()
        badge_fixture["items"] = saved_items
        page.render((UpdateItem(original, plan, (original.name,)),))
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

    def capture_update_badge():
        badge = window.update_badge
        row = badge.get_parent()
        title = row.get_first_child()
        assert title.get_next_sibling() is badge
        bounds = badge.compute_bounds(row)[1]
        title_bounds = title.compute_bounds(row)[1]
        assert bounds.get_x() >= title_bounds.get_x() + title_bounds.get_width()
        assert abs(bounds.get_y() + bounds.get_height() / 2 - row.get_height() / 2) <= 1
        capture(window, "details-update-badge-light.png")
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def finish_update_badge():
        capture(window, "details-update-badge-dark.png")
        window.updates_page.render(badge_fixture["items"])
        assert not window.update_badge.get_visible()
        window.navigation.pop_to_tag("overview")

    def check_dark():
        capture(window, "list-dark.png")
        window.set_default_size(360, 640)

    def check_narrow():
        assert window.split.get_collapsed()
        capture(window, "list-narrow.png")
        window.show_details(window.records[0])

    def check_narrow_details():
        update = window.open_button.compute_bounds(window)[1]
        remove = window.manage_button.compute_bounds(window)[1]
        assert update.get_x() >= 0 and update.get_x() + update.get_width() <= window.get_width()
        assert remove.get_x() >= 0 and remove.get_x() + remove.get_width() <= window.get_width()
        assert (
            remove.get_y() >= update.get_y() + update.get_height()
            or remove.get_x() >= update.get_x() + update.get_width()
        ), "Action buttons must not overlap"
        window.open_button.grab_focus()
        assert window.get_focus() is window.open_button
        capture(window, "details-narrow.png")
        window.navigation.pop_to_tag("overview")
        # Exercise preferences and About against the actual installed API.
        window.preferences()
        window.about()
        window.message("Test Message", "No applications were modified.")

    def close_messages():
        for w in list(Gtk.Window.get_toplevels()):
            if w is not window:
                w.destroy()

    def check_update_preview():
        close_messages()
        window.set_default_size(900, 700)
        window.show_details(window.records[0])
        app = window.detail_app
        saved = []

        def check(_app, progress, completed, _failed):
            saved.append(progress)
            progress("Checking synthetic updates", 0.5, True)
            completed(UpdateCheckResult(UpdateState.CURRENT))

        window.service.prepare_update = check
        window.check_update(window.detail_app)
        assert not window.operation_active and window.manage_button.get_sensitive()
        close_messages()
        window.service.prepare_update = lambda _a, _p, _c, failed: failed(
            OperationCancelled("Cancelled")
        )
        window.check_update(window.detail_app)
        assert not window.operation_active
        close_messages()
        plan = UpdatePlan(
            app.key,
            app.provider,
            "fixture;2.0;x86_64;updates",
            "System",
            "1.0",
            (
                UpdateChange(
                    "fixture.x86_64",
                    "fixture;2.0;x86_64;updates",
                    "update",
                    "updates",
                    "1.0",
                    "2.0",
                ),
            ),
            "synthetic",
            "Update this application and the listed dependencies.",
        )
        window.service.prepare_update = lambda _a, _p, completed, _f: completed(
            UpdateCheckResult(UpdateState.AVAILABLE, plan)
        )
        window.check_update(window.detail_app)
        assert window.operation_active and not window.manage_button.get_sensitive()
        assert window.confirm_dialog.get_default_response() == "cancel"
        assert window.confirm_dialog.get_body() == "1.0 → 2.0\nPersonal data is kept."
        details = window.confirm_dialog.get_extra_child().get_last_child()
        assert isinstance(details, UpdateDetails) and not details.toggle.get_active()
        assert "fixture.x86_64" in details.preview.get_label()
        assert (
            window.confirm_dialog.get_response_appearance("update")
            == Adw.ResponseAppearance.SUGGESTED
        )
        window.refresh()
        assert window.refresh_pending
        window.confirm_dialog.response("cancel")
        assert not window.operation_active
        window.check_update(window.detail_app)
        saved[0]("Stale progress must be ignored", 1, False)
        assert window.confirm_dialog is not None

    def check_update_expanded():
        capture(window.confirm_dialog, "update-preview.png")
        details = window.confirm_dialog.get_extra_child().get_last_child()
        assert_centered_update_details(details)
        details.toggle.emit("clicked")
        assert details.toggle.get_active() and details.revealer.get_reveal_child()
        assert "system authentication dialog" in details.preview.get_label()

    def check_update_execute():
        capture(window.confirm_dialog, "update-preview-details.png")
        details = window.confirm_dialog.get_extra_child().get_last_child()
        assert_centered_update_details(details)
        assert details.revealer.get_width() == details.get_width()
        window.service.execute_update = lambda _a, _plan, progress, _completed: progress(
            "Updating synthetic application", 0.5, False
        )
        window.confirm_dialog.response("update")
        assert window.operation_active and not window.cancel_button.get_sensitive()
        assert window.task_dialog.get_title() == "Updating Boxes"
        assert window._close(window)
        window._operation_finished(OperationResult(Outcome.SUCCESS, "Synthetic update completed."))
        assert not window.operation_active
        close_messages()
        window.navigation.pop_to_tag("overview")

    def check_operation():
        for w in list(Gtk.Window.get_toplevels()):
            if w is not window:
                w.destroy()
        # A fake executor exercises operation-window lifetime without calling providers.
        window.service.execute = lambda _app, _plan, progress, _completed: progress(
            "Testing an operation window", 0.5, True
        )
        cancelled = []
        window.service.cancel = lambda: cancelled.append(True)
        window._execute(window.records[0], None)
        assert window.cancel_button.get_sensitive()
        window.cancel_button.emit("clicked")
        assert cancelled == [True]
        assert window.cancel_button.get_label() == "Cancel"
        assert window.task_label.get_label() == "Testing an operation window"
        window._progress("A late download callback", 0.6, True)
        assert not window.cancel_button.get_sensitive()
        window.cancel_button.emit("clicked")
        assert cancelled == [True]
        assert window.task_dialog.get_visible()
        assert window.operation_active
        assert window._close(window)
        window._operation_finished(
            OperationResult(Outcome.CANCELLED, "Synthetic operation cancelled.")
        )
        assert not window.operation_active

    def check_update_preferences():
        close_messages()
        page = window.updates_page
        settings = window.settings
        calls = []

        def check(_progress, done, _failed, *, providers):
            calls.append(providers)
            done(UpdateReport(()))

        window.service.check_updates = check
        settings.set_string("update-check-mode", "manual")
        window.service.scanning = True
        page.enter()
        assert not page.check_when_ready and not calls
        window.service.scanning = False
        page.inventory_ready()
        page.enter()
        assert not calls
        page.refresh_button.emit("clicked")
        assert calls == [("rpm", "flatpak")]
        checked_at = page.checked_at
        settings.set_string("update-check-interval", "weekly")
        settings.set_string("update-check-mode", "on-entry")
        with patch("housekeeper.ui.updates.time.time", return_value=checked_at + 604799):
            page.enter()
            assert len(calls) == 1
        with patch("housekeeper.ui.updates.time.time", return_value=checked_at + 604800):
            page.enter()
            assert len(calls) == 2
        # Switching to manual also cancels an entry deferred during inventory scanning.
        page.checked_at = checked_at - 604800
        window.service.scanning = True
        page.enter()
        assert page.check_when_ready
        settings.set_string("update-check-mode", "manual")
        window.service.scanning = False
        page.inventory_ready()
        assert not page.check_when_ready and len(calls) == 2
        settings.set_boolean("update-source-rpm", False)
        assert page.checked_at is None and not page.items and not page.cache.path.exists()
        page.enter()
        assert len(calls) == 2
        page.refresh_button.emit("clicked")
        assert calls[-1] == ("flatpak",)
        restored = UpdatesPage(window)
        restored.inventory_ready()
        restored.enter()
        assert restored.checked_at is not None and len(calls) == 3
        settings.set_boolean("update-source-flatpak", False)
        assert not page.refresh_button.get_sensitive()
        assert page.empty.get_title() == "No Update Sources Enabled"
        page.check()
        page.enter()
        assert len(calls) == 3
        settings.set_boolean("update-source-flatpak", True)
        assert page.refresh_button.get_sensitive() and page.checked_at is None
        # Results started under earlier source settings must not overwrite the new state,
        # even if the sources are toggled off and then back on before completion.
        pending = []
        window.service.check_updates = lambda _p, done, _f, **_kwargs: pending.append(done)
        page.check()
        settings.set_boolean("update-source-flatpak", False)
        settings.set_boolean("update-source-flatpak", True)
        pending[0](UpdateReport(()))
        assert not window.operation_active and page.checked_at is None
        assert not page.cache.path.exists()
        settings.set_boolean("update-source-rpm", True)
        settings.set_string("update-check-interval", "daily")
        settings.set_string("update-check-mode", "on-entry")

        window.preferences()
        preferences = next(
            w for w in Gtk.Window.get_toplevels() if isinstance(w, Adw.PreferencesWindow)
        )

        def descendants(widget):
            yield widget
            child = widget.get_first_child()
            while child:
                yield from descendants(child)
                child = child.get_next_sibling()

        rows = {
            widget.get_title(): widget
            for widget in descendants(preferences)
            if isinstance(widget, (Adw.ComboRow, Adw.SwitchRow))
        }
        rows["Check for Updates"].set_selected(1)
        assert settings.get_string("update-check-mode") == "manual"
        assert not rows["Check Interval"].get_sensitive()
        rows["Check for Updates"].set_selected(0)
        assert rows["Check Interval"].get_sensitive()
        rows["Check Interval"].set_selected(1)
        assert Gio.Settings.new(APP_ID).get_string("update-check-interval") == "weekly"
        rows["Check Interval"].set_selected(0)
        rows["RPM Packages"].set_active(False)
        assert page.enabled_providers() == ("flatpak",)
        rows["RPM Packages"].set_active(True)

    def capture_update_preferences():
        preferences = next(
            w for w in Gtk.Window.get_toplevels() if isinstance(w, Adw.PreferencesWindow)
        )
        capture(preferences, "preferences-updates.png")
        close_messages()

    def check_updates_page():
        close_messages()
        window.set_default_size(1040, 720)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        items = []
        for record in (window.records[0], window.records[2], window.records[3]):
            target = (
                record.identity
                if record.provider == "flatpak"
                else f"{record.metadata['name']}.{record.metadata['arch']}"
            )
            change = UpdateChange(target, "2.0", "update", "updates", "1.0", "2.0")
            plan = UpdatePlan(
                record.key,
                record.provider,
                "2.0",
                record.scope,
                "1.0",
                (change,),
                "synthetic",
                "Update this app and its dependencies.",
            )
            items.append(UpdateItem(record, plan, (record.name,)))
        check_calls = []

        def check(_progress, done, _failed, *, providers):
            assert providers == ("rpm", "flatpak")
            check_calls.append(True)
            done(UpdateReport(tuple(items), unsupported=19))

        window.service.check_updates = check
        window.updates_sidebar.emit("row-activated", window.updates_row)
        page = window.updates_page
        assert window.navigation.get_visible_page() is page
        assert window.sidebar.get_selected_row() is None
        assert len(page.items) == 3 and page.all_button.get_sensitive()
        assert page.status.get_label() == "3 updates available"
        assert page.errors_button.get_visible() and "19 apps" in page.details
        assert not page.selected_button.get_sensitive()
        page.checks[0][1].set_active(True)
        page.checks[0][1].grab_focus()
        assert page.selected_button.get_sensitive() and len(page.selected()) == 1
        for _ in range(3):
            window._source_activated(window.sidebar, window.sidebar.get_row_at_index(0))
            window.updates_sidebar.emit("row-activated", window.updates_row)
        page.inventory_ready()
        assert len(check_calls) == 1 and len(page.selected()) == 1
        assert page.last_checked.get_visible()
        # Recreate the page to exercise a new window's disk-cache restoration.
        restored = UpdatesPage(window)
        restored.check_when_ready = True
        restored.inventory_ready()
        restored.enter()
        assert restored.items == page.items and len(check_calls) == 1
        assert restored.last_checked.get_visible()
        # An empty successful cache also suppresses automatic checks after restart.
        empty = UpdatesPage(window)
        empty.cache.path = Path(cache_directory.name) / "empty.json"
        empty.cache.save(UpdateReport(()), window.records, time.time())
        empty.inventory_ready()
        empty.enter()
        assert not empty.items and empty.empty.get_title() == "You're Up to Date"
        assert len(check_calls) == 1
        page.render(page.items)
        page.refresh_button.emit("clicked")
        assert len(check_calls) == 2 and len(page.selected()) == 1
        # Cancelling a refresh must leave the old list, widgets, selection and TTL intact.
        previous = (page.items, page.selected(), page.status.get_label(), page.checked_at)
        previous_checks = list(page.checks)
        cache_bytes = page.cache.path.read_bytes()
        cancel = window.service.cancel
        for partial in ((), tuple(items[:1])):
            pending = []
            window.service.check_updates = lambda _p, done, _f, pending=pending, **_kwargs: (
                pending.append(done)
            )
            window.service.cancel = lambda pending=pending, partial=partial: pending[0](
                UpdateReport(partial, ("Interrupted check",), cancelled=True)
            )
            page.refresh_button.emit("clicked")
            assert window.operation_active
            assert window.cancel_button.get_sensitive()
            window._progress("A backend phase cannot stop immediately", 0.1, False)
            assert window.cancel_button.get_sensitive()
            window._progress("Checking synthetic updates", 0.2, True)
            window.cancel_button.emit("clicked")
            assert not window.operation_active and window.task_dialog is None
            assert (
                page.items,
                page.selected(),
                page.status.get_label(),
                page.checked_at,
            ) == previous
            assert page.checks == previous_checks and page.selected_button.get_sensitive()
            assert page.cache.path.read_bytes() == cache_bytes
        window.service.cancel = cancel
        window.service.check_updates = check
        # A previous successful empty result is also retained on cancellation.
        empty.checked(UpdateReport(tuple(items[:1]), cancelled=True))
        assert not empty.items and empty.empty.get_title() == "You're Up to Date"
        checked_at = page.checked_at
        with patch("housekeeper.ui.updates.time.time", return_value=checked_at + 86399):
            page.enter()
            assert len(check_calls) == 2
        with patch("housekeeper.ui.updates.time.time", return_value=checked_at + 86400):
            # Inventory/focus refresh alone must not start an expired-cache check.
            page.inventory_ready()
            assert len(check_calls) == 2
            window.service.scanning = True
            page.enter()
            page.enter()
            assert page.check_when_ready and len(check_calls) == 2
            window.service.scanning = False
            page.inventory_ready()
            assert len(check_calls) == 3 and page.checked_at == checked_at + 86400
            page.enter()
            assert len(check_calls) == 3
        # Loading an expired empty cache at startup remains offline until entry.
        expired = UpdatesPage(window)
        expired.cache.path = Path(cache_directory.name) / "expired.json"
        expired.cache.save(UpdateReport(()), window.records, time.time() - 86401)
        expired.inventory_ready()
        assert len(check_calls) == 3
        expired.enter()
        assert len(check_calls) == 4
        # Leaving while the initial inventory is pending cancels the entry request.
        expired.checked_at -= 86401
        window.service.scanning = True
        expired.enter()
        window.section = "apps"
        window.service.scanning = False
        expired.inventory_ready()
        assert len(check_calls) == 4 and not expired.check_when_ready
        window.section = "updates"
        # Restore real time after the simulated next-day expiry check above.
        page.checked(page.report)
        check_update_confirmation_content(page.items)
        page.selected_button.emit("clicked")
        assert window.confirm_dialog.get_default_response() == "cancel"
        assert window.confirm_dialog.get_heading() == "Update Boxes?"
        assert "system authentication dialog" not in window.confirm_dialog.get_body()
        details = window.confirm_dialog.get_extra_child().get_last_child()
        assert not details.toggle.get_active()
        assert "system authentication dialog" in details.preview.get_label()
        window.confirm_dialog.response("cancel")
        assert not window.operation_active
        item = replace(
            page.items[0],
            names=(page.items[0].app.name, "Boxes - URL Handler", "Boxes - URL Handler"),
        )
        page.confirm((item,))
        assert window.confirm_dialog.get_heading() == "Update Boxes?"
        assert "Also includes: Boxes - URL Handler" in window.confirm_dialog.get_body()
        assert window.confirm_dialog.get_body().count("Boxes - URL Handler") == 1
        details = window.confirm_dialog.get_extra_child().get_last_child()
        assert "Also includes:" not in details.preview.get_label()
        window.confirm_dialog.response("cancel")

        # New sandbox access is named before it is granted, not hidden behind Details.
        granting = replace(
            page.items[0], plan=replace(page.items[0].plan, permissions=("sockets: x11",))
        )
        page.confirm((granting,))
        assert "Requests 1 new sandbox permission" in window.confirm_dialog.get_body()
        card = window.confirm_dialog.get_extra_child().get_first_child()
        # Widened sandbox access is always a caution, whatever else the update does.
        assert {"card", "notice", "notice-warning"} <= set(card.get_css_classes())
        warning = card.get_first_child()
        assert warning.get_first_child().get_label() == "New Permissions"
        assert "• sockets: x11" in warning.get_last_child().get_label()
        window.confirm_dialog.response("cancel")

        # A package update names the running programs whose files it would replace.
        in_use = replace(
            page.items[0],
            plan=replace(
                page.items[0].plan,
                provider="rpm",
                running=("/usr/bin/boxes",),
                # A core library is in use nearly everywhere, so Details stays bounded.
                in_use=("/usr/lib64/libboxes.so.1",)
                + tuple(f"/usr/share/boxes/data{i}" for i in range(11)),
            ),
        )
        page.confirm((in_use,))
        # The card states one action. The paths it applies to stay in Details.
        assert "Running now" not in window.confirm_dialog.get_body()
        content = window.confirm_dialog.get_extra_child()
        assert "notice-warning" in content.get_first_child().get_css_classes()
        notice = content.get_first_child().get_first_child()
        assert notice.get_first_child().get_label() == "In Use Right Now"
        assert "notice-heading" in notice.get_first_child().get_css_classes()
        card = notice.get_last_child().get_label()
        assert card == "Running now. Quit it before updating, or restart it afterwards."
        details = content.get_last_child()
        assert not details.toggle.get_active()
        preview = details.preview.get_label()
        assert "Running now:\n/usr/bin/boxes" in preview
        assert "Replaced files in use:\n/usr/lib64/libboxes.so.1" in preview
        assert "and 2 more" in preview and "/usr/share/boxes/data9" not in preview
        window.confirm_dialog.response("cancel")

        # Files in use without a running subject still say what to do afterwards.
        others = replace(in_use, plan=replace(in_use.plan, running=()))
        page.confirm((others,))
        notice = window.confirm_dialog.get_extra_child().get_first_child().get_first_child()
        assert notice.get_last_child().get_label().startswith("Replaced files are in use.")
        window.confirm_dialog.response("cancel")

        # A running Flatpak keeps its own deployment, so it is disclosed without alarm.
        live = replace(
            page.items[0],
            plan=replace(page.items[0].plan, provider="flatpak", running=("org.gnome.Boxes",)),
        )
        page.confirm((live,))
        card = window.confirm_dialog.get_extra_child().get_first_child()
        # Its deployment survives, so this one stays informational rather than a caution.
        assert "notice" in card.get_css_classes()
        assert "notice-warning" not in card.get_css_classes()
        notice = card.get_first_child()
        assert (
            notice.get_last_child().get_label() == "Running now. Reopen it to use the new version."
        )
        window.confirm_dialog.response("cancel")

        page.confirm((page.items[0],))
        assert "sandbox permission" not in window.confirm_dialog.get_body()
        assert "Running now" not in window.confirm_dialog.get_body()
        assert isinstance(window.confirm_dialog.get_extra_child().get_first_child(), UpdateDetails)
        window.confirm_dialog.response("cancel")

        window.set_size_request(1040, 720)
        window.set_default_size(1040, 720)

    def check_update_confirmation_content(items):
        page = window.updates_page
        original = items[0]
        for provider, old, new, expected in (
            ("rpm", "0:1.2.3-1.fc44", "0:1.2.4-1.fc44", "1.2.3 → 1.2.4"),
            ("rpm", "0:1.2.3-1.fc44", "0:1.2.3-2.fc44", "0:1.2.3-1.fc44 → 0:1.2.3-2.fc44"),
            ("flatpak", "a" * 64, "b" * 64, "aaaaaaaaaaaa → bbbbbbbbbbbb"),
            ("rpm", "", "", "New version available"),
        ):
            app = replace(original.app, provider=provider, source=Source(provider))
            change = replace(
                original.plan.changes[0],
                identity=app.identity if provider == "flatpak" else "fixture.x86_64",
                current_version=old,
                target_version=new,
            )
            item = replace(
                original,
                app=app,
                plan=replace(original.plan, provider=provider, changes=(change,) if new else ()),
            )
            page.render((item,))
            subtitle = page.checks[0][1].get_child().get_last_child().get_last_child()
            assert subtitle.get_label().split("\n")[-1] == expected
            page.confirm((item,))
            assert window.confirm_dialog.get_body().split("\n")[0] == expected
            details = window.confirm_dialog.get_extra_child().get_last_child()
            assert f"Target: {item.plan.target}" in details.preview.get_label()
            window.confirm_dialog.response("cancel")
        page.render(items)
        page.checks[0][1].set_active(True)

        # A details-page update must disclose other launchers of the same installation
        # before Details is opened, and retain the exact installation/target there.
        installation = InstallationInstance(
            "shared-package", "rpm", "Host RPM database", "shared", "1"
        )
        target = ManagementTarget("shared-target", installation.id, "rpm", "shared.x86_64")
        app = replace(original.app, installation=installation, target=target)
        alias = replace(app, key="shared-launcher", name="Shared Companion")
        hidden = replace(app, key="hidden-launcher", name="Hidden Companion", visible=False)
        unverifiable = replace(
            app,
            key="unverified-launcher",
            name="Unverified Companion",
            update_action=UpdateAction.INSTRUCTIONS,
        )
        records = window.records
        window.records = [app, alias, hidden, unverifiable]
        assert window._begin_operation(app, "update")
        window._confirm(app, original.plan, update=True)
        dialog = window.confirm_dialog
        assert "Also includes: Shared Companion" in dialog.get_body()
        assert hidden.name not in dialog.get_body() and unverifiable.name not in dialog.get_body()
        details = dialog.get_extra_child().get_last_child()
        assert not details.toggle.get_active()
        preview = details.preview.get_label()
        assert "Installation: Host RPM database" in preview
        assert "Target: shared.x86_64" in preview and original.plan.message in preview
        dialog.response("cancel")
        window.records = records

        # Batch summaries also expose grouped launchers without opening Details.
        grouped = replace(original, names=(original.app.name, alias.name))
        page.confirm((grouped, items[1]))
        content = window.confirm_dialog.get_extra_child()
        summaries = content.get_first_child().get_child().get_child()
        assert (
            "Also includes: Shared Companion" in summaries.get_first_child().get_child().get_label()
        )
        assert not content.get_last_child().toggle.get_active()
        window.confirm_dialog.response("cancel")

    def assert_centered_update_details(details):
        bounds = details.toggle.compute_bounds(details)[1]
        assert abs(bounds.get_x() + bounds.get_width() / 2 - details.get_width() / 2) <= 1
        # EXPANDED is a tristate: GTK reads it with g_value_get_int and drops bools.
        recorded = []
        update_state, details.toggle.update_state = (
            details.toggle.update_state,
            lambda states, values: recorded.append((states, values)),
        )
        try:
            details._toggled()
        finally:
            details.toggle.update_state = update_state
        states, values = recorded[0]
        assert states == [Gtk.AccessibleState.EXPANDED]
        assert values == [int(details.toggle.get_active())]
        assert all(not isinstance(value, bool) for value in values)

    def check_updates_narrow():
        assert not window.split.get_collapsed()
        capture(window, "updates-light.png")
        page = window.updates_page
        assert_updates_footer()
        assert window.updates_row.compute_bounds(window)[1].get_y() > window.get_height() / 2
        page.all_button.emit("clicked")
        assert window.confirm_dialog.get_heading() == "Update 3 apps?"
        assert window.confirm_dialog.get_body() == "Personal data is kept."
        content = window.confirm_dialog.get_extra_child()
        assert isinstance(content.get_first_child(), Gtk.ScrolledWindow)
        assert not content.get_last_child().toggle.get_active()
        window.confirm_dialog.response("cancel")
        window.set_size_request(1200, 720)
        window.set_default_size(1200, 720)

    def check_updates_wide():
        capture(window, "updates-wide.png")
        page = window.updates_page
        assert_updates_footer()
        assert window.get_width() >= 1150  # GTK excludes the window decoration from this size.
        for button in (page.all_button, page.selected_button):
            assert button.get_width() < 220
        assert page.actions.get_width() < 450
        window.set_size_request(360, 420)
        window.set_default_size(360, 640)

    def check_updates_batch():
        capture(window, "updates-narrow.png")
        page = window.updates_page
        assert_updates_footer()
        assert window.split.get_collapsed()
        for button in (page.all_button, page.selected_button):
            bounds = button.compute_bounds(window)[1]
            assert bounds.get_x() >= 0 and bounds.get_x() + bounds.get_width() <= window.get_width()
        calls = []
        original = page.items
        checked_at = page.checked_at
        page.checks[1][1].set_active(True)
        window.service.execute_updates = lambda items, _p, done: (
            calls.append(items),
            done(
                OperationResult(
                    Outcome.PARTIAL,
                    "Synthetic partial update.",
                    ("Boxes",),
                    ("Cancelled",),
                    completed_app_keys=(items[0].app.key,),
                )
            ),
        )
        with patch.object(window, "refresh"):
            page.all_button.emit("clicked")
            window.confirm_dialog.response("update")
        assert len(calls[0]) == 3 and not window.operation_active
        assert page.items == original[1:] and page.selected() == (original[1],)
        assert page.status.get_label() == "2 updates available"
        assert page.stack.get_visible_child_name() == "list"
        assert window.updates_count.get_label() == "2" and page.all_button.get_sensitive()
        assert page.checked_at == checked_at and "19 apps" in page.details
        # Inventory refresh after success must keep the list and persist its original TTL.
        records = window.records
        window.records = [
            replace(app, version="2.0") if app.key == original[0].app.key else app
            for app in records
        ]
        page.inventory_ready()
        assert page.status.get_label() == "2 updates available" and not page.stale
        restored = UpdatesPage(window)
        restored.inventory_ready()
        with patch.object(window.service, "check_updates") as check:
            restored.enter()
            check.assert_not_called()
        assert restored.items == original[1:] and not restored.stale
        assert restored.checked_at == checked_at
        close_messages()
        # Failed/cancelled attempts without any completed apps preserve the cache and selection.
        cache_bytes = page.cache.path.read_bytes()
        for outcome in (Outcome.CANCELLED, Outcome.FAILED):
            with patch.object(window, "refresh"):
                page.finished(OperationResult(outcome, "Stopped"))
            assert page.items == original[1:] and page.selected() == (original[1],)
            assert page.cache.path.read_bytes() == cache_bytes
            close_messages()
            assert window._begin_operation(original[1].app, "update")
            with patch.object(window, "refresh"):
                window._operation_finished(OperationResult(outcome, "Stopped"))
            assert page.items == original[1:] and page.cache.path.read_bytes() == cache_bytes
            close_messages()
        # A successful details-page update removes the same cached installation.
        assert window._begin_operation(original[1].app, "update")
        with patch.object(window, "refresh"):
            window._operation_finished(OperationResult(Outcome.SUCCESS, "Done"))
        assert page.items == original[2:] and not page.selected()
        assert page.status.get_label() == "1 update available"
        assert page.cache.load(window.records).report.items == original[2:]
        close_messages()
        # A details-page update may finish before the inventory is refreshed, so
        # inventory equality cannot be the only way completed rows disappear.
        with patch.object(window, "refresh"):
            page.finished(
                OperationResult(Outcome.SUCCESS, "Done", completed_app_keys=(original[2].app.key,))
            )
        assert not page.stale and page.empty.get_title() == "You're Up to Date"
        # Only records belonging to the completed installations may be rebased.
        completed_keys = {item.app.key for item in original[1:]}
        window.records = [
            replace(app, software_size=12345) if app.key in completed_keys else app
            for app in window.records
        ]
        page.inventory_ready()
        assert not page.items and not page.stale and page.empty.get_title() == "You're Up to Date"
        assert not page.all_button.get_sensitive() and window.updates_count.get_label() == ""
        restored = UpdatesPage(window)
        restored.inventory_ready()
        assert not restored.items and not restored.stale and restored.checked_at == checked_at
        page.inventory_ready()
        assert not page.stale and page.empty.get_title() == "You're Up to Date"
        # Later inventory changes still require a new check.
        window.records = [replace(app, software_size=54321) for app in window.records]
        page.inventory_ready()
        assert page.stale and page.empty.get_title() == "Check for Updates"
        window.records = records
        close_messages()
        check_update_inventory_boundaries(original, records)
        # Updating a second launcher for the same installation removes the grouped row,
        # while preserving check errors and pruning the previous successful disk report.
        alias = replace(original[0].app, key="update-alias")
        window.records = [*records, alias]
        page.checked(UpdateReport(original, unsupported=19))
        page.checked(UpdateReport(original, ("Offline",), unsupported=19))
        page.updates_completed((alias.key,))
        assert page.items == original[1:]
        assert "incomplete" in page.status.get_label() and "Offline" in page.details
        assert page.cache.load(window.records).report.items == original[1:]
        window.records = records
        status = page.status.get_label()
        page.checked(UpdateReport((), ("Offline",), cancelled=True))
        assert page.status.get_label() == status
        page.checked(UpdateReport((), ("Offline",)))
        assert "incomplete" in page.status.get_label() and page.errors_button.get_visible()
        page.checked(UpdateReport(original[:1], unsupported=1))
        assert page.status.get_label() == "1 update available"
        assert "1 app requires its own updater." in page.details
        page.checked(UpdateReport(()))
        assert page.empty.get_title() == "You're Up to Date"
        window._source_activated(window.sidebar, window.sidebar.get_row_at_index(0))
        assert window.navigation.get_visible_page() is window.overview_page
        assert window.updates_sidebar.get_selected_row() is None

    def check_update_inventory_boundaries(items, records):
        page = window.updates_page
        first, second = items[:2]
        added = replace(
            first.app,
            key="new-installation",
            identity="org.example.NewApp",
            installation=None,
            metadata={**first.app.metadata, "name": "new-installation"},
        )

        def change_record(key):
            window.records = [
                replace(app, software_size=12345) if app.key == key else app
                for app in window.records
            ]

        def assert_stale_and_restorable(checked_at):
            assert page.stale and page.empty.get_title() == "Check for Updates"
            cached = page.cache.load(window.records)
            assert cached.stale and cached.checked_at == checked_at
            restored = UpdatesPage(window)
            restored.inventory_ready()
            assert restored.stale and restored.empty.get_title() == "Check for Updates"
            assert restored.items == page.items and restored.checked_at == checked_at

        # A changed pending row is pruned before another app finishes updating.
        # Finishing the last visible row must not treat the pruned app as updated.
        window.records = records
        page.checked(UpdateReport((first, second)))
        checked_at = page.checked_at
        change_record(first.app.key)
        page.inventory_ready()
        assert page.stale and page.items == (second,)
        page.updates_completed((second.app.key,))
        assert not page.items and page.stale
        change_record(second.app.key)
        page.inventory_ready()
        assert_stale_and_restorable(checked_at)

        # An installation appearing during a successful batch has never been checked.
        window.records = records
        page.checked(UpdateReport((first,)))
        checked_at = page.checked_at
        page.updates_completed((first.app.key,))
        assert not page.stale and page.empty.get_title() == "You're Up to Date"
        change_record(first.app.key)
        window.records = [*window.records, added]
        page.inventory_ready()
        assert_stale_and_restorable(checked_at)

        # A details-page update after an empty report cannot explain another app's change.
        window.records = records
        page.checked(UpdateReport(()))
        checked_at = page.checked_at
        page.updates_completed((first.app.key,))
        change_record(first.app.key)
        change_record(second.app.key)
        page.inventory_ready()
        assert_stale_and_restorable(checked_at)

        # Observe an unrelated installation before a details-page update completes.
        # The live page and a restored cache must agree that another check is needed.
        window.records = records
        page.checked(UpdateReport(()))
        checked_at = page.checked_at
        window.records = [*records, added]
        page.inventory_ready()
        assert page.stale
        page.updates_completed((first.app.key,))
        change_record(first.app.key)
        page.inventory_ready()
        assert_stale_and_restorable(checked_at)
        window.records = records

    def assert_updates_footer():
        page = window.updates_page
        actions = page.actions.compute_bounds(window)[1]
        selection = page.selection_label.compute_bounds(window)[1]
        content = page.stack.compute_bounds(window)[1]
        assert actions.get_y() > window.get_height() - 130
        assert actions.get_y() + actions.get_height() <= window.get_height() - 8
        assert window.get_width() - actions.get_x() - actions.get_width() < 30
        assert selection.get_y() >= content.get_y() + content.get_height()
        assert selection.get_x() + selection.get_width() <= actions.get_x()

    def check_performance():
        assert window.source == "all", "Focus restoration changed the selected source"
        records = [
            replace(sample, key=f"{i}-{sample.key}", name=f"{sample.name} {i}")
            for i in range(100)
            for sample in examples()
        ]
        window._replace(records)
        begin = time.perf_counter()
        window.query = "maps"
        window.filter.changed(Gtk.FilterChange.DIFFERENT)
        assert window.filtered.get_n_items() == 100, (
            f"source={window.source}, query={window.query}, "
            f"filtered={window.filter_model.get_n_items()}, sorted={window.filtered.get_n_items()}, "
            f"pending={window.filtered.get_pending()}, incremental={window.filtered.get_incremental()}"
        )
        elapsed = (time.perf_counter() - begin) * 1000
        print(
            f"1,000-record search: {elapsed:.2f} ms; peak RSS: "
            f"{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.1f} MiB"
        )
        # Shared CI runners have no stable latency budget; correctness is asserted above.

    progress_window = {}

    def start_progress_window():
        assert window._begin_operation(None, "update")
        window._show_task("Checking for Updates", deferred_cancel=True)
        assert window.cancel_button.get_sensitive()
        progress_window["sensitivity"] = []
        window.cancel_button.connect(
            "notify::sensitive",
            lambda button, _: progress_window["sensitivity"].append(button.get_sensitive()),
        )
        for can_cancel in (False, True, False, True, False):
            window._progress("Checking backend phases", 0.0, can_cancel)
            assert window.cancel_button.get_sensitive()
        assert progress_window["sensitivity"] == []
        window._progress("Checking A (1 of 3)", 0.0, True)
        progress_window["dialog"] = window.task_dialog
        progress_window["bar"] = window.task_progress

    def grow_progress_message():
        dialog = window.task_dialog
        progress_window["size"] = (dialog.get_width(), dialog.get_height())
        window._progress(
            "Checking an application with a very long name (2 of 3)\n"
            + "Resolving configured software sources and package information " * 8,
            1 / 3,
            True,
        )

    def shrink_progress_message():
        dialog = window.task_dialog
        assert dialog is progress_window["dialog"]
        assert (dialog.get_width(), dialog.get_height()) == progress_window["size"]
        assert window.task_progress is progress_window["bar"]
        assert window.task_progress.get_fraction() == 1 / 3
        assert "Resolving configured software sources" in window.task_detail_label.get_label()
        assert window.task_label.get_tooltip_text().startswith(window.task_label.get_label())
        capture(dialog, "update-check-progress.png")
        window._progress("Checked 3 of 3 applications", 1.0, False)

    def finish_progress_window():
        dialog = window.task_dialog
        assert dialog is progress_window["dialog"]
        assert (dialog.get_width(), dialog.get_height()) == progress_window["size"]
        assert window.task_progress.get_fraction() == 1.0
        assert window.cancel_button.get_sensitive()
        assert progress_window["sensitivity"] == []
        requests = []
        original_cancel = window.service.cancel
        window.service.cancel = lambda: requests.append(True)
        window.cancel_button.emit("clicked")
        for can_cancel in (True, False, True):
            window._progress("Waiting for cancellation", 1.0, can_cancel)
        assert not window.cancel_button.get_sensitive()
        assert progress_window["sensitivity"] == [False]
        window.cancel_button.emit("clicked")
        assert requests == [True]
        window.service.cancel = original_cancel
        window._end_operation()

    def finish():
        print(f"GTK smoke completed in {time.monotonic() - started:.2f}s; screenshots: {output}")
        for w in list(Gtk.Window.get_toplevels()):
            if w is not window:
                w.destroy()
        window.close()
        app.quit()

    steps.extend(
        [
            start_sorting,
            check_size_sorting,
            check_grid_sorting,
            check_installation_sorting,
            finish_sorting,
            hover_list,
            hover_list_dark,
            finish_list_hover,
            check_list,
            hover_grid,
            hover_grid_dark,
            finish_grid_hover,
            selected_grid_hover,
            pressed_grid,
            finish_selected_grid,
            check_grid,
            hover_details_light,
            hover_details_dark,
            finish_details_hover,
            scroll_storage,
            capture_storage,
            scroll_appearance,
            capture_appearance,
            check_details,
            check_flatpak_removal_options,
            capture_flatpak_removal,
            check_open_actions,
            capture_update_badge,
            finish_update_badge,
            check_dark,
            check_narrow,
            check_narrow_details,
            check_update_preview,
            check_update_expanded,
            check_update_execute,
            check_operation,
            check_update_preferences,
            capture_update_preferences,
            check_updates_page,
            check_updates_narrow,
            check_updates_wide,
            check_updates_batch,
            start_icon_change,
            verify_icon_change,
            verify_icon_reset,
            start_progress_window,
            grow_progress_message,
            shrink_progress_message,
            finish_progress_window,
            check_performance,
            finish,
        ]
    )

    def step():
        if failed:
            app.quit()
            return False
        if not window.initialized:
            return True
        try:
            steps.pop(0)()
        except Exception as error:
            exception(type(error), error, error.__traceback__)
            app.quit()
            return False
        return bool(steps)

    GLib.timeout_add(700, step)


application.connect("activate", activate)
GLib.timeout_add_seconds(
    40, lambda: (failed.append("UI smoke timed out"), application.quit(), False)[2]
)
application.run([sys.argv[0]])
raise SystemExit(bool(failed))
