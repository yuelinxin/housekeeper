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
from housekeeper.models import (
    Action,
    AppRecord,
    DesktopEntry,
    OperationCancelled,
    OperationResult,
    Outcome,
    Source,
    UpdateAction,
    UpdateChange,
    UpdateCheckResult,
    UpdatePlan,
    UpdateState,
)
from housekeeper.services import InventoryService

Gio.resources_register(Gio.Resource.load(str(BUILD / "data/housekeeper.gresource")))
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

    def check_list():
        assert window.filtered.get_n_items() == 10, (
            f"source={window.source}, query={window.query}, count={window.filtered.get_n_items()}"
        )
        assert window.views.get_visible_child_name() == "list"
        capture(window, "list-light.png")
        all_row = window.sidebar.get_row_at_index(0)
        flatpak_row = window.sidebar.get_row_at_index(2)
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
        assert window.update_button.get_label() == "Check for Updates"
        assert window.manage_button.get_label() == "Uninstall"
        assert window.manage_button.has_css_class("destructive-action")
        assert not window.update_button.has_css_class("destructive-action")
        assert window.update_button.get_allocation().height > 0
        capture(window, "details-light.png")
        changed = list(window.records)
        changed[0] = replace(changed[0], version="2.0")
        window._complete(changed, [], [], {})
        assert window.detail_app.version == "2.0"
        window._complete(changed[1:], [], [], {})
        assert window.detail_notice.get_revealed()
        assert not window.manage_button.get_sensitive()
        assert not window.update_button.get_sensitive()
        window._complete(examples(), [], [], {})
        window.navigation.pop_to_tag("overview")
        window.view_buttons["list"].set_active(True)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def hover_details_light():
        for button in (window.update_button, window.manage_button):
            button.get_parent().set_state_flags(Gtk.StateFlags.PRELIGHT, False)
            button.set_state_flags(Gtk.StateFlags.PRELIGHT, False)

    def hover_details_dark():
        capture(window, "details-hover-light.png")
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def finish_details_hover():
        capture(window, "details-hover-dark.png")
        for button in (window.update_button, window.manage_button):
            button.unset_state_flags(Gtk.StateFlags.PRELIGHT)
            button.get_parent().unset_state_flags(Gtk.StateFlags.PRELIGHT)
            button.grab_focus()
            assert window.get_focus() is button
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

    def check_dark():
        capture(window, "list-dark.png")
        window.set_default_size(360, 640)

    def check_narrow():
        assert window.split.get_collapsed()
        capture(window, "list-narrow.png")
        window.show_details(window.records[0])

    def check_narrow_details():
        update = window.update_button.compute_bounds(window)[1]
        remove = window.manage_button.compute_bounds(window)[1]
        assert update.get_x() >= 0 and update.get_x() + update.get_width() <= window.get_width()
        assert remove.get_x() >= 0 and remove.get_x() + remove.get_width() <= window.get_width()
        assert remove.get_y() > update.get_y(), "Narrow action buttons should stack"
        window.update_button.grab_focus()
        assert window.update_button.has_focus()
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
        window.update_button.emit("clicked")
        assert not window.operation_active and window.manage_button.get_sensitive()
        close_messages()
        window.service.prepare_update = lambda _a, _p, _c, failed: failed(
            OperationCancelled("Cancelled")
        )
        window.update_button.emit("clicked")
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
        window.update_button.emit("clicked")
        assert window.operation_active and not window.manage_button.get_sensitive()
        assert window.confirm_dialog.get_default_response() == "cancel"
        assert (
            window.confirm_dialog.get_response_appearance("update")
            == Adw.ResponseAppearance.SUGGESTED
        )
        window.refresh()
        assert window.refresh_pending
        window.confirm_dialog.response("cancel")
        assert not window.operation_active
        window.update_button.emit("clicked")
        saved[0]("Stale progress must be ignored", 1, False)
        assert window.confirm_dialog is not None

    def check_update_execute():
        capture(window.confirm_dialog, "update-preview.png")
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

    def check_updates_page():
        close_messages()
        window.set_default_size(1040, 720)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        items = []
        for record in (window.records[0], window.records[2], window.records[3]):
            target = (
                record.identity if record.provider == "flatpak" else record.identity + ".x86_64"
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

        def check(_progress, done, _failed):
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
            window.service.check_updates = lambda _p, done, _f, pending=pending: pending.append(
                done
            )
            window.service.cancel = lambda pending=pending, partial=partial: pending[0](
                UpdateReport(partial, ("Interrupted check",), cancelled=True)
            )
            page.refresh_button.emit("clicked")
            assert window.operation_active
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
        page.selected_button.emit("clicked")
        assert window.confirm_dialog.get_default_response() == "cancel"
        assert window.confirm_dialog.get_heading() == "Update Boxes?"
        assert "system authentication dialog" in window.confirm_dialog.get_body()
        window.confirm_dialog.response("cancel")
        assert not window.operation_active

        window.set_size_request(1040, 720)
        window.set_default_size(1040, 720)

    def check_updates_narrow():
        assert not window.split.get_collapsed()
        capture(window, "updates-light.png")
        page = window.updates_page
        assert_updates_footer()
        assert window.updates_row.compute_bounds(window)[1].get_y() > window.get_height() / 2
        page.all_button.emit("clicked")
        assert window.confirm_dialog.get_heading() == "Update 3 apps?"
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
        window.service.execute_updates = lambda items, _p, done: (
            calls.append(items),
            done(
                OperationResult(
                    Outcome.PARTIAL, "Synthetic partial update.", ("Boxes",), ("Cancelled",)
                )
            ),
        )
        page.all_button.emit("clicked")
        window.confirm_dialog.response("update")
        assert len(calls[0]) == 3 and not window.operation_active and not page.items
        close_messages()
        status = page.status.get_label()
        page.checked(UpdateReport((), ("Offline",), cancelled=True))
        assert page.status.get_label() == status
        page.checked(UpdateReport((), ("Offline",)))
        assert "incomplete" in page.status.get_label() and page.errors_button.get_visible()
        page.checked(UpdateReport(()))
        assert page.empty.get_title() == "You're Up to Date"
        window._source_activated(window.sidebar, window.sidebar.get_row_at_index(0))
        assert window.navigation.get_visible_page() is window.overview_page
        assert window.updates_sidebar.get_selected_row() is None

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
        assert elapsed < 100, "Inventory filtering exceeded the reference budget"

    def finish():
        print(f"GTK smoke completed in {time.monotonic() - started:.2f}s; screenshots: {output}")
        for w in list(Gtk.Window.get_toplevels()):
            if w is not window:
                w.destroy()
        window.close()
        app.quit()

    steps.extend(
        [
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
            check_details,
            check_dark,
            check_narrow,
            check_narrow_details,
            check_update_preview,
            check_update_execute,
            check_operation,
            check_updates_page,
            check_updates_narrow,
            check_updates_wide,
            check_updates_batch,
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
    25, lambda: (failed.append("UI smoke timed out"), application.quit(), False)[2]
)
application.run([sys.argv[0]])
raise SystemExit(bool(failed))
