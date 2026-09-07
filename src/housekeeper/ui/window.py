"""Adaptive native inventory, details, and management confirmation windows."""

import logging
import shlex
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from housekeeper import APP_ID, VERSION
from housekeeper.i18n import _
from housekeeper.models import Action, Outcome, Source
from housekeeper.services import InventoryService

SOURCES = {
    "all": (_("All Apps"), "view-app-grid-symbolic"),
    "rpm": (_("System Packages"), "package-x-generic-symbolic"),
    "flatpak": (_("Flatpak"), "application-x-addon-symbolic"),
    "web": (_("Web Apps"), "web-browser-symbolic"),
    "appimage": (_("AppImage"), "application-x-executable-symbolic"),
    "steam": (_("Steam"), "applications-games-symbolic"),
    "other": (_("Other"), "folder-symbolic"),
}
BADGES = {
    "rpm": "RPM",
    "flatpak": "Flatpak",
    "web": _("Web App"),
    "appimage": "AppImage",
    "steam": "Steam",
    "other": _("Other"),
}


def dispatch(callback, *args):
    def invoke():
        callback(*args)
        return GLib.SOURCE_REMOVE

    GLib.idle_add(invoke)


def icon_image(icon, size):
    image = Gtk.Image(pixel_size=size)
    set_icon(image, icon)
    return image


def set_icon(image, icon):
    if icon and Path(icon).is_absolute() and Path(icon).is_file():
        image.set_from_file(icon)
    else:
        theme = Gtk.IconTheme.get_for_display(image.get_display())
        name = icon if icon and theme.has_icon(icon) else "application-x-executable"
        image.set_from_icon_name(name)


def label(text, **kwargs):
    return Gtk.Label(label=text, xalign=0, **kwargs)


class AppObject(GObject.Object):
    def __init__(self, record):
        super().__init__()
        self.record = record
        self.search = record.search_text
        self.sort_key = (record.name.casefold(), record.key)


@Gtk.Template(resource_path="/io/github/yuelinxin/housekeeper/window.ui")
class HousekeeperWindow(Adw.ApplicationWindow):
    __gtype_name__ = "HousekeeperWindow"
    toast_overlay = Gtk.Template.Child()
    split = Gtk.Template.Child()
    sidebar = Gtk.Template.Child()
    sidebar_button = Gtk.Template.Child()
    navigation = Gtk.Template.Child()
    overview_page = Gtk.Template.Child()
    overview_box = Gtk.Template.Child()
    overview_toolbar = Gtk.Template.Child()
    header = Gtk.Template.Child()

    def __init__(self, application, settings):
        super().__init__(application=application)
        self.settings = settings
        self.service = InventoryService(dispatch)
        self.source = "all"
        self.query = ""
        self.records = []
        self.monitors = []
        self.monitor_paths = set()
        self.refresh_source = 0
        self.refresh_pending = False
        self.initialized = False
        self.closed = False
        self.operation_active = False
        self.detail_app = None
        self.warnings = []
        self.set_default_size(settings.get_int("window-width"), settings.get_int("window-height"))
        if settings.get_boolean("maximized"):
            self.maximize()
        self._build_overview()
        self._actions()
        # Focus restoration can change selection; only activation changes the source.
        self.sidebar_handler = self.sidebar.connect("row-activated", self._source_activated)
        self.sidebar_button.connect("clicked", lambda _b: self.split.set_show_sidebar(True))
        self.connect("close-request", self._close)
        self.connect("notify::is-active", self._activated)
        self.refresh()

    def _build_overview(self):
        self.search = Gtk.SearchEntry(
            placeholder_text=_("Search apps, packages, and paths"),
            hexpand=True,
            margin_start=16,
            margin_end=16,
            margin_bottom=12,
        )
        self.search.connect("search-changed", self._search_changed)
        self.overview_box.append(self.search)
        self.banner = Adw.Banner(
            title=_("Some sources could not be read"), button_label=_("Details")
        )
        self.banner.connect(
            "button-clicked",
            lambda _b: self.message(_("Inventory Details"), "\n\n".join(self.warnings)),
        )
        self.overview_box.append(self.banner)
        self.status_box = Gtk.Box(spacing=8, margin_start=20, margin_end=20, margin_bottom=8)
        self.spinner = Gtk.Spinner(spinning=True)
        self.summary = label(
            _("Finding your applications…"), hexpand=True, ellipsize=Pango.EllipsizeMode.END
        )
        self.summary.add_css_class("dim-label")
        self.status_box.append(self.spinner)
        self.status_box.append(self.summary)
        self.overview_box.append(self.status_box)

        self.store = Gio.ListStore.new(AppObject)
        self.filter = Gtk.CustomFilter.new(self._matches)
        self.filter_model = Gtk.FilterListModel(model=self.store, filter=self.filter)
        self.sorter = Gtk.CustomSorter.new(self._compare)
        self.filtered = Gtk.SortListModel(model=self.filter_model, sorter=self.sorter)
        self.selection = Gtk.SingleSelection(
            model=self.filtered, autoselect=False, can_unselect=True
        )
        self.views = Gtk.Stack(vexpand=True)
        self.scrolls = {}
        for mode in ("list", "grid"):
            factory = Gtk.SignalListItemFactory()
            factory.connect("setup", self._factory_setup, mode)
            factory.connect("bind", self._factory_bind, mode)
            if mode == "list":
                view = Gtk.ListView(
                    model=self.selection, factory=factory, single_click_activate=True
                )
            else:
                view = Gtk.GridView(
                    model=self.selection,
                    factory=factory,
                    single_click_activate=True,
                    min_columns=2,
                    max_columns=8,
                )
            view.add_css_class("inventory-" + mode)
            view.connect("activate", self._open_position)
            scroll = Gtk.ScrolledWindow(
                hscrollbar_policy=Gtk.PolicyType.NEVER,
                child=view,
                margin_start=12,
                margin_end=12,
                margin_bottom=12,
            )
            self.scrolls[mode] = scroll
            self.views.add_named(scroll, mode)
        self.empty = Adw.StatusPage(
            title=_("Finding Applications"),
            description=_("Installed applications will appear here."),
            icon_name="view-app-grid-symbolic",
        )
        self.views.add_named(self.empty, "empty")
        self.overview_box.append(self.views)
        self.view_buttons = {}
        group = None
        controls = Gtk.Box()
        controls.add_css_class("linked")
        for mode, icon, title in (
            ("list", "view-list-symbolic", _("List View")),
            ("grid", "view-grid-symbolic", _("Grid View")),
        ):
            button = Gtk.ToggleButton(icon_name=icon, tooltip_text=title)
            if group:
                button.set_group(group)
            group = button
            button.connect("toggled", self._view_toggled, mode)
            self.view_buttons[mode] = button
            controls.append(button)
        self.header.pack_end(controls)
        self.split.bind_property(
            "collapsed",
            controls,
            "visible",
            GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.INVERT_BOOLEAN,
        )
        self.view_buttons[self.settings.get_string("view-mode")].set_active(True)

    def _actions(self):
        for name, callback in (
            ("refresh", lambda *_: self.refresh()),
            ("search", lambda *_: self.search.grab_focus()),
            ("preferences", lambda *_: self.preferences()),
            ("about", lambda *_: self.about()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        hidden = self.settings.create_action("show-hidden")
        self.add_action(hidden)
        self.add_action(self.settings.create_action("view-mode"))
        self.settings.connect(
            "changed::view-mode",
            lambda *_: self.view_buttons[self.settings.get_string("view-mode")].set_active(True),
        )
        self.settings.connect("changed::show-hidden", lambda *_: self._refilter())
        menu = Gio.Menu()
        menu.append(_("Refresh"), "win.refresh")
        menu.append(_("List View"), "win.view-mode::list")
        menu.append(_("Grid View"), "win.view-mode::grid")
        menu.append(_("Show Hidden and Auxiliary Entries"), "win.show-hidden")
        menu.append(_("Preferences"), "win.preferences")
        menu.append(_("About Housekeeper"), "win.about")
        self.header.pack_end(
            Gtk.MenuButton(
                icon_name="open-menu-symbolic", menu_model=menu, tooltip_text=_("Main Menu")
            )
        )
        self.get_application().set_accels_for_action("win.search", ["<Primary>f"])
        self.get_application().set_accels_for_action("win.refresh", ["<Primary>r", "F5"])

    def _factory_setup(self, _factory, item, mode):
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL if mode == "list" else Gtk.Orientation.VERTICAL,
            spacing=14 if mode == "list" else 10,
        )
        box.add_css_class("app-row" if mode == "list" else "app-tile")
        picture = Gtk.Image(pixel_size=40 if mode == "list" else 64)
        box.append(picture)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        name = label("", ellipsize=Pango.EllipsizeMode.END, max_width_chars=32)
        name.add_css_class("heading")
        subtitle = label("", ellipsize=Pango.EllipsizeMode.END, max_width_chars=35)
        subtitle.add_css_class("dim-label")
        if mode == "grid":
            name.set_xalign(0.5)
            subtitle.set_xalign(0.5)
            box.set_size_request(130, 150)
        text.append(name)
        text.append(subtitle)
        box.append(text)
        badge = label("", valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
        badge.add_css_class("source-badge")
        if mode == "list":
            box.append(badge)
        item.set_child(box)
        item.widgets = (picture, name, subtitle, badge)

    def _factory_bind(self, _factory, item, mode):
        app = item.get_item().record
        picture, name, subtitle, badge = item.widgets
        set_icon(picture, app.icon)
        name.set_label(app.name)
        subtitle.set_label(
            app.status or (BADGES[app.source.value] if mode == "grid" else app.scope)
        )
        badge.set_label(BADGES[app.source.value])
        item.get_child().set_tooltip_text(
            f"{app.name}\n{BADGES[app.source.value]} · {app.scope}"
            + (f"\n{app.status}" if app.status else "")
        )

    def _matches(self, obj):
        app = obj.record
        return (
            (app.visible or self.settings.get_boolean("show-hidden"))
            and (self.source == "all" or app.source.value == self.source)
            and all(word in obj.search for word in self.query.split())
        )

    @staticmethod
    def _compare(first, second, _data=None):
        left, right = first.sort_key, second.sort_key
        return (left > right) - (left < right)

    def _refilter(self):
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self._sidebar()
        self._update_count()

    def _sidebar(self):
        counts = {key: 0 for key in SOURCES}
        for app in self.records:
            if app.visible or self.settings.get_boolean("show-hidden"):
                counts["all"] += 1
                counts[app.source.value] += 1
        if counts.get(self.source, 0) == 0:
            self.source = "all"
            self.filter.changed(Gtk.FilterChange.DIFFERENT)
        # Rebuilding navigation rows must not activate another source.
        self.sidebar.handler_block(self.sidebar_handler)
        try:
            child = self.sidebar.get_first_child()
            while child:
                following = child.get_next_sibling()
                self.sidebar.remove(child)
                child = following
            for key, (title, icon) in SOURCES.items():
                if key != "all" and not counts[key]:
                    continue
                row = Gtk.ListBoxRow()
                row.source = key
                box = Gtk.Box(
                    spacing=10, margin_top=9, margin_bottom=9, margin_start=6, margin_end=6
                )
                box.append(Gtk.Image(icon_name=icon))
                box.append(label(title, hexpand=True))
                count = label(str(counts[key]))
                count.add_css_class("dim-label")
                box.append(count)
                row.set_child(box)
                self.sidebar.append(row)
                if key == self.source:
                    self.sidebar.select_row(row)
        finally:
            self.sidebar.handler_unblock(self.sidebar_handler)
        self.overview_page.set_title(SOURCES[self.source][0])

    def _source_activated(self, _list, row):
        if row is None:
            return
        self.source = row.source
        self.overview_page.set_title(SOURCES[self.source][0])
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_count()
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)
        if self.detail_app is not None:
            self.navigation.pop_to_tag("overview")
            self.detail_app = None

    def _search_changed(self, entry):
        self.query = entry.get_text().casefold()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_count()

    def _update_count(self):
        count = self.filtered.get_n_items()
        if not self.service.scanning:
            self.summary.set_label(_("%d applications") % count)
        if count:
            self.views.set_visible_child_name(self.settings.get_string("view-mode"))
        else:
            self.empty.set_title(_("No Results") if self.query else _("No Applications"))
            self.empty.set_description(
                _("Try a different search or source.")
                if self.query
                else _("Refresh, or enable hidden and auxiliary entries.")
            )
            self.views.set_visible_child_name("empty")

    def _view_toggled(self, button, mode):
        if button.get_active():
            self.settings.set_string("view-mode", mode)
            self._update_count()

    def refresh(self):
        if self.closed:
            return
        if self.service.scanning or self.service.busy or self.operation_active:
            self.refresh_pending = True
            return
        self.refresh_pending = False
        self.spinner.set_visible(True)
        self.spinner.start()
        self.summary.set_label(_("Reading installed applications…"))
        self.service.scan(self._partial, self._complete, self._scan_failed)

    def _replace(self, records):
        if self.closed:
            return
        self.records = records
        self.store.splice(0, self.store.get_n_items(), [AppObject(a) for a in records])
        # Rebuilding source rows must not navigate away from an open detail page.
        saved = self.detail_app
        self.detail_app = None
        self._sidebar()
        self.detail_app = saved
        self._update_count()

    def _partial(self, records):
        if not self.initialized:
            self._replace(records)
            self.summary.set_label(_("Identifying installation sources…"))

    def _complete(self, records, warnings, roots, installations):
        if self.closed:
            return
        unchanged = self.initialized and self.records == records
        self.initialized = True
        if not unchanged:
            self._replace(records)
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.summary.set_label(_("%d applications") % self.filtered.get_n_items())
        self.warnings = warnings
        self.banner.set_revealed(bool(warnings))
        self._monitor(roots, installations)
        if self.detail_app:
            match = next((r for r in records if r.key == self.detail_app.key), None)
            if match is None:
                self.detail_notice.set_title(_("This application is no longer in the inventory."))
                self.detail_notice.set_revealed(True)
                self.manage_button.set_sensitive(False)
            else:
                if self.detail_app != match:
                    self.show_details(match, replace=True)
        if self.refresh_pending:
            self.refresh_pending = False
            self._schedule_refresh()

    def _scan_failed(self, error):
        if self.closed:
            return
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.summary.set_label(_("Inventory could not be refreshed"))
        self.warnings = [error]
        self.banner.set_revealed(True)

    def _monitor(self, roots, installations):
        for root in roots:
            target = root if root.is_dir() else root.parent
            if str(target) in self.monitor_paths:
                continue
            try:
                monitor = Gio.File.new_for_path(str(target)).monitor_directory(
                    Gio.FileMonitorFlags.NONE, None
                )
                monitor.connect("changed", lambda *_: self._schedule_refresh())
                self.monitors.append(monitor)
                self.monitor_paths.add(str(target))
            except GLib.Error:
                logging.getLogger(__name__).debug("Could not monitor an application directory")
        for path, installation in installations.items():
            key = "flatpak:" + path
            if key not in self.monitor_paths:
                try:
                    monitor = installation.create_monitor(None)
                    monitor.connect("changed", lambda *_: self._schedule_refresh())
                    self.monitors.append(monitor)
                    self.monitor_paths.add(key)
                except GLib.Error:
                    pass

    def _schedule_refresh(self):
        if self.closed:
            return
        if self.refresh_source:
            GLib.source_remove(self.refresh_source)

        def run():
            self.refresh_source = 0
            self.refresh()
            return GLib.SOURCE_REMOVE

        self.refresh_source = GLib.timeout_add(750, run)

    def _activated(self, *_):
        if self.is_active() and self.initialized:
            self._schedule_refresh()

    def _open_position(self, _view, position):
        if self.service.scanning:
            self.toast(_("Installation details are still loading."))
            return
        obj = self.filtered.get_item(position)
        if obj:
            self.show_details(obj.record)

    def _property(self, group, title, value, path=False):
        if not value:
            return
        row = Adw.ActionRow(title=title, subtitle=value)
        row.set_subtitle_lines(3)
        copy = Gtk.Button(
            icon_name="edit-copy-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Copy %s") % title,
        )
        copy.add_css_class("flat")
        copy.connect("clicked", lambda _b: self._copy(value))
        row.add_suffix(copy)
        if path and Path(value).exists():
            open_button = Gtk.Button(
                icon_name="folder-open-symbolic",
                valign=Gtk.Align.CENTER,
                tooltip_text=_("Open File Location"),
            )
            open_button.add_css_class("flat")
            open_button.connect("clicked", lambda _b: self.open_location(value))
            row.add_suffix(open_button)
        group.add(row)

    def show_details(self, app, replace=False):
        self.detail_app = app
        page = Adw.NavigationPage(title=app.name)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=24,
            margin_top=28,
            margin_bottom=32,
            margin_start=24,
            margin_end=24,
        )
        clamp = Adw.Clamp(maximum_size=720, tightening_threshold=500, child=body)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.detail_notice = Adw.Banner(title="")
        outer.append(self.detail_notice)
        outer.append(scroll)
        scroll.set_vexpand(True)
        toolbar.set_content(outer)
        page.set_child(toolbar)
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        hero.append(icon_image(app.icon, 96))
        title = Gtk.Label(label=app.name, wrap=True, justify=Gtk.Justification.CENTER)
        title.add_css_class("title-1")
        hero.append(title)
        subtitle = Gtk.Label(label=f"{BADGES[app.source.value]} · {app.scope}")
        subtitle.add_css_class("dim-label")
        hero.append(subtitle)
        action_titles = {
            Action.UNINSTALL: _("Uninstall…"),
            Action.TRASH: _("Move to Trash…"),
            Action.CHROME: _("Manage in Chrome")
            if "chromium" not in app.metadata.get("browser", "")
            else _("Manage in Chromium"),
            Action.STEAM: _("Manage in Steam"),
            Action.INSTRUCTIONS: _("Show Management Instructions"),
            Action.NONE: _("Show Management Instructions"),
        }
        self.manage_button = Gtk.Button(label=action_titles[app.action], halign=Gtk.Align.CENTER)
        self.manage_button.add_css_class("pill")
        if app.action in {Action.UNINSTALL, Action.TRASH}:
            self.manage_button.add_css_class("destructive-action")
        else:
            self.manage_button.add_css_class("suggested-action")
        self.manage_button.connect("clicked", lambda _b: self.manage(app))
        hero.append(self.manage_button)
        body.append(hero)
        if app.status:
            status = Gtk.Label(label=app.status, wrap=True)
            status.add_css_class("dim-label")
            body.append(status)
        installation = Adw.PreferencesGroup(title=_("Installation"))
        self._property(installation, _("Source"), BADGES[app.source.value])
        self._property(installation, _("Version"), app.version or _("Unknown"))
        self._property(installation, _("Scope"), app.scope)
        if app.scope == "Unknown":
            self._property(
                installation,
                _("Scope Details"),
                _("The installation scope could not be verified from its desktop entry."),
            )
        self._property(installation, _("Desktop Entry Scope"), app.metadata.get("entry_scope", ""))
        self._property(installation, _("Application or Package ID"), app.identity)
        self._property(installation, _("Origin"), app.origin)
        self._property(installation, _("Browser Profile"), app.metadata.get("profile", ""))
        body.append(installation)
        files = Adw.PreferencesGroup(title=_("File Locations"))
        self._property(files, _("Installation Location"), app.location, True)
        self._property(
            files, _("Browser Data Directory"), app.metadata.get("user_data_dir", ""), True
        )
        seen = set()
        for entry in app.entries:
            self._property(files, _("Desktop Entry"), str(entry.path), True)
            if entry.executable and entry.executable not in seen:
                self._property(
                    files,
                    _("Host Program") if app.source in {Source.WEB, Source.STEAM} else _("Program"),
                    entry.executable,
                    True,
                )
                seen.add(entry.executable)
                if entry.resolved_executable != entry.executable:
                    self._property(
                        files, _("Resolved Program Path"), entry.resolved_executable, True
                    )
        body.append(files)
        if app.entries:
            advanced = Adw.PreferencesGroup()
            expander = Adw.ExpanderRow(title=_("Technical Details"))
            for entry in app.entries:
                row = Adw.ActionRow(
                    title=entry.desktop_id, subtitle=entry.command or _("D-Bus activation")
                )
                row.set_subtitle_lines(0)
                expander.add_row(row)
            advanced.add(expander)
            body.append(advanced)
        if replace:
            self.navigation.replace([self.overview_page, page])
        else:
            self.navigation.push(page)
        self.detail_app = app
        page.connect("hidden", self._detail_hidden, app.key)

    def _detail_hidden(self, _page, key):
        if (
            self.detail_app
            and self.detail_app.key == key
            and self.navigation.get_visible_page() is self.overview_page
        ):
            self.detail_app = None

    def _copy(self, value):
        self.get_clipboard().set(value)
        self.toast(_("Copied to Clipboard"))

    def open_location(self, value):
        file = Gio.File.new_for_path(value)
        if not file.query_exists(None):
            self.message(
                _("Location Unavailable"), _("This file no longer exists. Refresh the inventory.")
            )
            return
        try:
            Gtk.FileLauncher.new(file).open_containing_folder(self, None, self._location_opened)
        except GLib.Error as error:
            self.message(_("Could Not Open Location"), str(error))

    def _location_opened(self, launcher, result):
        try:
            launcher.open_containing_folder_finish(result)
        except GLib.Error as error:
            self.message(_("Could Not Open Location"), str(error))

    def manage(self, app):
        if app.action in {Action.UNINSTALL, Action.TRASH}:
            self.manage_button.set_sensitive(False)
            self.toast(_("Preparing a removal preview…"))
            self.service.prepare(app, lambda plan: self._confirm(app, plan), self._preview_failed)
        elif app.action == Action.CHROME:
            try:
                Gio.Subprocess.new(list(app.management), Gio.SubprocessFlags.NONE)
                self.toast(_("In the browser, open the app menu and choose Uninstall."))
            except GLib.Error as error:
                self.message(_("Could Not Open Browser"), str(error))
        elif app.action == Action.STEAM:
            Gtk.UriLauncher.new(app.management[0]).launch(self, None, self._uri_opened)
            self.toast(_("In Steam, select the game, then Manage → Uninstall."))
        else:
            instructions = app.metadata.get("management_reason", "")
            if app.provider == "firefoxpwa":
                instructions = _(
                    "Open your browser's PWAsForFirefox extension, find this app, "
                    "and use its remove action. Housekeeper keeps browser profiles intact."
                )
            self.message(
                _("Management Instructions"),
                instructions
                or _(
                    "Review the publisher's uninstall instructions and the file locations shown here."
                ),
                help_url="https://pwasforfirefox.filips.si/user-guide/console/"
                if app.provider == "firefoxpwa"
                else None,
            )

    def _uri_opened(self, launcher, result):
        try:
            launcher.launch_finish(result)
        except GLib.Error as error:
            self.message(_("Could Not Open Manager"), str(error))

    def _preview_failed(self, error):
        if self.closed:
            return
        self.manage_button.set_sensitive(True)
        if self.detail_app and self.detail_app.provider == "rpm":
            package = self.detail_app.metadata.get("name")
            if package:
                error += "\n\n" + _("Review this package in a terminal:") + "\n\n"
                error += "sudo dnf remove -- " + shlex.quote(package)
        self.message(_("Removal Unavailable"), error)
        if self.refresh_pending:
            self._schedule_refresh()

    def _confirm(self, app, plan):
        if self.closed:
            return
        self.manage_button.set_sensitive(True)
        dialog = Adw.MessageDialog(
            transient_for=self, heading=_("Remove %s?") % app.name, body=plan.message
        )
        affected = Gtk.Label(label="\n".join(plan.affected), wrap=True, selectable=True, xalign=0)
        scroll = Gtk.ScrolledWindow(
            child=affected,
            max_content_height=220,
            propagate_natural_height=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        dialog.set_extra_child(scroll)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response(
            "remove", _("Move to Trash") if app.action == Action.TRASH else _("Uninstall")
        )
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(_dialog, result):
            if result == "remove":
                self._execute(app, plan)

        dialog.connect("response", response)
        dialog.present()

    def _execute(self, app, plan):
        self.operation_active = True
        self.task_dialog = Adw.Window(
            transient_for=self,
            modal=True,
            title=_("Removing %s") % app.name,
            default_width=420,
            resizable=False,
        )
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(
            Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        )
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            margin_start=24,
            margin_end=24,
            margin_top=24,
            margin_bottom=24,
        )
        self.task_label = Gtk.Label(label=_("Waiting for the application manager…"), wrap=True)
        box.append(self.task_label)
        self.task_progress = Gtk.ProgressBar()
        box.append(self.task_progress)
        self.cancel_button = Gtk.Button(label=_("Cancel Operation"), sensitive=False)
        self.cancel_button.connect("clicked", lambda _b: self.service.cancel())
        box.append(self.cancel_button)
        toolbar.set_content(box)
        self.task_dialog.set_content(toolbar)
        self.task_dialog.connect("close-request", lambda *_: self.operation_active)
        self.task_dialog.present()
        self.service.execute(app, plan, self._progress, self._operation_finished)

    def _progress(self, message, fraction, can_cancel):
        self.task_label.set_label(message)
        if fraction is None:
            self.task_progress.pulse()
        else:
            self.task_progress.set_fraction(fraction)
        self.cancel_button.set_sensitive(can_cancel)

    def _operation_finished(self, result):
        self.operation_active = False
        self.task_dialog.destroy()
        title = {
            Outcome.SUCCESS: _("Removal Complete"),
            Outcome.PARTIAL: _("Partially Completed"),
            Outcome.CANCELLED: _("Operation Cancelled"),
            Outcome.FAILED: _("Removal Failed"),
        }[result.outcome]
        body = result.message
        if result.completed:
            body += "\n\n" + _("Completed:") + "\n" + "\n".join(result.completed)
        if result.errors:
            body += "\n\n" + "\n".join(result.errors)
        self.message(title, body)
        self.refresh()

    def message(self, heading, body, help_url=None):
        dialog = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        dialog.add_response("close", _("Close"))
        dialog.set_close_response("close")
        if help_url:
            dialog.add_response("help", _("Open Management Guide"))
            dialog.connect(
                "response",
                lambda _d, response: (
                    Gtk.UriLauncher.new(help_url).launch(self, None, self._uri_opened)
                    if response == "help"
                    else None
                ),
            )
        dialog.present()

    def toast(self, text):
        self.toast_overlay.add_toast(Adw.Toast(title=text, timeout=5))

    def preferences(self):
        window = Adw.PreferencesWindow(transient_for=self, modal=True, title=_("Preferences"))
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title=_("Application Inventory"))
        row = Adw.SwitchRow(
            title=_("Show Hidden and Auxiliary Entries"),
            subtitle=_("Include entries normally omitted by the desktop launcher."),
        )
        self.settings.bind("show-hidden", row, "active", Gio.SettingsBindFlags.DEFAULT)
        group.add(row)
        page.add(group)
        window.add(page)
        window.present()

    def about(self):
        Adw.AboutWindow(
            transient_for=self,
            modal=True,
            application_name="Housekeeper",
            application_icon=APP_ID,
            version=VERSION,
            developer_name="Yuelin Xin",
            developers=["Yuelin Xin"],
            copyright="© 2026 Yuelin Xin",
            license_type=Gtk.License.MIT_X11,
            comments=_(
                "Understand where your applications come from and manage them with confidence."
            ),
            website="https://github.com/yuelinxin/housekeeper",
            issue_url="https://github.com/yuelinxin/housekeeper/issues",
        ).present()

    def _close(self, _window):
        if self.operation_active or self.service.busy:
            self.toast(_("Wait for the current operation, or cancel it when available."))
            return True
        self.settings.set_boolean("maximized", self.is_maximized())
        if not self.is_maximized():
            self.settings.set_int("window-width", max(360, min(10000, self.get_width())))
            self.settings.set_int("window-height", max(420, min(10000, self.get_height())))
        self.closed = True
        for monitor in self.monitors:
            monitor.cancel()
        if self.refresh_source:
            GLib.source_remove(self.refresh_source)
        self.service.close()
        return False
