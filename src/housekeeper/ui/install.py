"""Install Apps: one search across sources, and one install page for every kind of app."""

import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from housekeeper.appearance import load_icon_image
from housekeeper.i18n import _
from housekeeper.installations import (
    InstallRequest,
    app_key,
    appimage_name,
    file_kind,
    group_candidates,
    looks_like_website,
    normalize_url,
    search_query,
    source_order,
)
from housekeeper.models import ManagementError
from housekeeper.ui.activity import ActivityProgress, StatusSpinner, spinner
from housekeeper.ui.icons import icon_image, set_icon


def sidebar_header(window, header):
    toggle = Gtk.Button(icon_name="sidebar-show-symbolic", tooltip_text=_("Show Sources"))
    toggle.connect("clicked", lambda _b: window.split.set_show_sidebar(True))
    window.split.bind_property("collapsed", toggle, "visible", GObject.BindingFlags.SYNC_CREATE)
    window.split.bind_property(
        "collapsed", header, "show-start-title-buttons", GObject.BindingFlags.SYNC_CREATE
    )
    header.pack_start(toggle)


def list_body():
    """A full-width scrollable column, inset like the app list and its search field."""
    box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=18,
        margin_start=12,
        margin_end=12,
        margin_top=12,
        margin_bottom=12,
    )
    return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=box), box


def details_body():
    """The app details page's clamped column, so both kinds of details pages align."""
    box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=24,
        margin_top=28,
        margin_bottom=32,
        margin_start=24,
        margin_end=24,
    )
    scroll = Gtk.ScrolledWindow(
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        child=Adw.Clamp(maximum_size=720, tightening_threshold=500, child=box),
    )
    return scroll, box


def choose_file(window, title, filter_name, extensions, chosen, failed):
    chooser = Gtk.FileDialog(title=title)
    filters = Gio.ListStore.new(Gtk.FileFilter)
    installable = Gtk.FileFilter(name=filter_name)
    for extension in extensions:
        # File chooser patterns are case-sensitive; downloads use either spelling.
        for spelling in {extension, extension.lower(), extension.upper()}:
            installable.add_pattern("*" + spelling)
    filters.append(installable)
    all_files = Gtk.FileFilter(name=_("All Files"))
    all_files.add_pattern("*")
    filters.append(all_files)
    chooser.set_filters(filters)

    def done(dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error as error:
            if not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                failed(str(error))
            return
        path = file.get_path()
        if not path:
            failed(_("Choose a local file."))
            return
        chosen(path)

    chooser.open(window, None, done)


def choose_appimage(window, chosen, failed):
    choose_file(window, _("Choose AppImage"), _("AppImage Files"), (".AppImage",), chosen, failed)


class InstallPage(Adw.NavigationPage):
    def __init__(self, window):
        super().__init__(title=_("Install Apps"), tag="install")
        self.owner = window
        self.closed = False
        self.timer = 0
        self.serial = 0
        self.query = ""
        self.order = []
        self.pending = []
        self.found = {}
        self.errors = {}
        self.packages_expanded = False
        toolbar = Adw.ToolbarView()
        self.set_child(toolbar)
        header = Adw.HeaderBar(show_back_button=False)
        sidebar_header(window, header)
        toolbar.add_top_bar(header)
        # Searching is what this page is for, so typing can start as soon as it shows,
        # including on return from an install page.
        self.connect("shown", lambda _page: self.search.grab_focus())
        scroll, content = list_body()
        toolbar.set_content(scroll)
        self.search = Gtk.SearchEntry(
            placeholder_text=_("Search apps, or enter a website address"), hexpand=True
        )
        self.search.add_css_class("page-search")
        # The page debounces searches itself, so skip the entry's own delay.
        self.search.connect("changed", self._query_changed)
        content.append(self.search)
        self.website_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, visible=False)
        self.website_list.add_css_class("boxed-list")
        self.website_row = Adw.ActionRow(
            subtitle=_("Opens in its own browser window"), activatable=True
        )
        self.website_row.add_prefix(icon_image("web-browser", 32))
        self.website_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.website_row.connect("activated", lambda _r: self.open_website(self.query))
        self.website_list.append(self.website_row)
        content.append(self.website_list)
        self.results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, visible=False)
        self.results.add_css_class("boxed-list")
        self.results.connect("row-activated", lambda _l, row: self.open_choice(row.choice))
        content.append(self.results)
        self.packages = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, visible=False)
        self.packages.add_css_class("boxed-list")
        content.append(self.packages)
        # Later sources are still being searched below the results already shown.
        self.progress = spinner(
            visible=False, halign=Gtk.Align.CENTER, width_request=32, height_request=32
        )
        self.progress.update_property([Gtk.AccessibleProperty.LABEL], [_("Searching")])
        content.append(self.progress)
        self.notice = Gtk.Label(wrap=True, xalign=0, visible=False)
        self.notice.add_css_class("dim-label")
        self.notice.add_css_class("caption")
        content.append(self.notice)
        self.placeholder = Adw.StatusPage(vexpand=True)
        self.placeholder.add_css_class("page-status")
        placeholder_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER
        )
        # Searching shows a spinner in place of the icon.
        self.spinner = StatusSpinner(self.placeholder, placeholder_box)
        self.placeholder_file = Gtk.Button(label=_("Install from File…"))
        self.placeholder_file.add_css_class("pill")
        self.placeholder_file.connect("clicked", self.choose_file)
        placeholder_box.append(self.placeholder_file)
        self.placeholder.set_child(placeholder_box)
        content.append(self.placeholder)
        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", self._dropped)
        self.add_controller(drop)
        window.settings.connect(
            "changed::install-source-preference", lambda *_: self._query_changed(self.search)
        )
        self._show_placeholder("idle")

    def _stop_search(self):
        self.serial += 1
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        self.owner.service.cancel_install_search()

    def close(self):
        self.closed = True
        self._stop_search()

    def installed(self):
        """Start over once the requested app is installed; failures keep the input."""
        if self.closed:
            return
        if self.get_parent() is not None and self.owner.navigation.get_visible_page() is not self:
            self.owner.navigation.pop_to_page(self)
        self.search.set_text("")

    def search_sources(self):
        from housekeeper.providers.snap import SNAP_SOCKET

        order = source_order(
            self.owner.settings.get_string("install-source-preference"), self.owner.native_source
        )
        # Most systems have no snapd; a source that cannot exist is not an error.
        return [source for source in order if source != "snap" or SNAP_SOCKET.exists()]

    def _show_placeholder(self, state, detail=""):
        searching = state == "searching"
        self.placeholder_file.set_visible(state == "idle")
        title, icon, description = {
            "idle": (
                _("Find Apps to Install"),
                "install-apps-symbolic",
                _("Search for an app, enter a website, or open a downloaded file."),
            ),
            "searching": ("", None, ""),
            "empty": (
                _("No Results Found"),
                "edit-find-symbolic",
                _("Try a shorter name; the app may already be installed."),
            ),
            "error": (_("Search Failed"), "dialog-warning-symbolic", detail),
        }[state]
        self.placeholder.set_title(title)
        self.placeholder.set_icon_name(icon)
        self.spinner.set_active(searching)
        self.placeholder.set_description(GLib.markup_escape_text(description))
        self.placeholder.set_visible(True)

    def _query_changed(self, entry):
        self._stop_search()
        text = entry.get_text().strip()
        self.query = text
        self.found, self.errors, self.pending = {}, {}, []
        website = looks_like_website(text)
        self.website_list.set_visible(website)
        if website:
            self.website_row.set_title(GLib.markup_escape_text(_("Add “%s” as a Web App") % text))
        try:
            query = search_query(text)
        except ManagementError:
            query = None
        if query is not None:
            self.order = self.search_sources()
            self.pending = list(self.order)
        self._render()
        if query is None:
            return
        serial = self.serial

        def found(source, candidates, error):
            if self.closed or serial != self.serial:
                return
            if source in self.pending:
                self.pending.remove(source)
            self.found[source] = candidates
            if error:
                self.errors[source] = error
            self._render()

        def finished():
            if not self.closed and serial == self.serial:
                self.pending = []
                self._render()

        def failed(error):
            if not self.closed and serial == self.serial:
                self.pending = []
                self._render(failure=error)

        def search():
            self.timer = 0
            self.owner.service.search_apps(text, self.order, found, finished, failed)
            return GLib.SOURCE_REMOVE

        self.timer = GLib.timeout_add(400, search)

    def _render(self, failure=""):
        while row := self.results.get_first_child():
            self.results.remove(row)
        while row := self.packages.get_first_child():
            self.packages.remove(row)
        # Sources list installed copies; the inventory also knows those, e.g. Snap's.
        found = {
            source: tuple(
                c if c.installed or self.installed_app(c) is None else replace(c, installed=True)
                for c in candidates
            )
            for source, candidates in self.found.items()
        }
        choices = group_candidates(self.query, found, self.order)
        apps = [choice for choice in choices if choice.is_app]
        packages = [choice for choice in choices if not choice.is_app]
        for choice in apps:
            self.results.append(self._result_row(choice))
        self.results.set_visible(bool(apps))
        if packages:
            expander = Adw.ExpanderRow(
                title=_("More Packages"),
                subtitle=_("Packages without an app listing, such as tools and libraries"),
                expanded=self.packages_expanded,
            )
            expander.connect(
                "notify::expanded",
                lambda row, _p: setattr(self, "packages_expanded", row.get_expanded()),
            )
            for choice in packages:
                row = self._result_row(choice)
                row.connect("activated", lambda r: self.open_choice(r.choice))
                expander.add_row(row)
            self.packages.append(expander)
        self.packages.set_visible(bool(packages))
        sources = self.owner.sources
        shown = bool(choices) or self.website_list.get_visible()
        searching = bool(self.pending)
        self.progress.set_visible(searching and shown)
        failed = [sources[source][0] for source in self.order if source in self.errors]
        self.notice.set_visible(bool(failed) and bool(choices))
        self.notice.set_label(_("Could not search: %s") % ", ".join(failed))
        self.notice.set_tooltip_text("\n".join(self.errors.values()) or None)
        if failure or (failed and not choices and not searching and not shown):
            self._show_placeholder("error", failure or "\n".join(self.errors.values()))
        elif shown:
            self.placeholder.set_visible(False)
        elif searching:
            self._show_placeholder("searching")
        elif self.found:
            self._show_placeholder("empty")
        else:
            self._show_placeholder("idle")

    def _result_row(self, choice):
        row = Adw.ActionRow(
            title=GLib.markup_escape_text(choice.title),
            subtitle=GLib.markup_escape_text(choice.summary),
            activatable=True,
        )
        row.set_title_lines(1)
        row.set_subtitle_lines(2)
        if choice.icon:
            icon = Gtk.Image.new_from_file(choice.icon)
        else:
            icon = Gtk.Image(
                icon_name="application-x-executable" if choice.is_app else "package-x-generic"
            )
        icon.set_pixel_size(32)
        row.add_prefix(icon)
        if choice.installed:
            installed = Gtk.Label(label=_("Installed"), valign=Gtk.Align.CENTER)
            installed.add_css_class("installed-badge")
            row.add_suffix(installed)
        names = dict.fromkeys(self.owner.sources[o.source][0] for o in choice.options)
        badge = Gtk.Label(label=" · ".join(names), valign=Gtk.Align.CENTER)
        badge.add_css_class("dim-label")
        badge.add_css_class("caption")
        row.add_suffix(badge)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.choice = choice
        return row

    def installed_app(self, candidate):
        """The inventory's app for an installed search result, or None."""

        def same(app):
            if app.source.value != candidate.source:
                return False
            if candidate.source == "flatpak":
                installation = app.metadata.get("installation")
                return (app.identity, installation) == (candidate.target, candidate.context)
            return app.metadata.get("name") == candidate.name

        apps = [app for app in self.owner.records if same(app)]
        # A package may hold several apps; prefer the one its catalogue entry names.
        key = app_key(candidate)
        named = (
            app
            for app in apps
            if any(e.desktop_id.casefold().removesuffix(".desktop") == key for e in app.entries)
        )
        return next(named, apps[0] if apps else None)

    def _open(self, page):
        self.owner.navigation.push(page)
        return page

    def open_choice(self, choice):
        return self._open(InstallDetailsPage(self, "app", choice=choice))

    def open_website(self, url):
        return self._open(InstallDetailsPage(self, "web", value=url))

    def open_appimage(self, path):
        return self._open(InstallDetailsPage(self, "appimage", value=path))

    def open_file(self, path):
        """Open the install page that suits a downloaded file; False if none does."""
        kind = file_kind(path)
        if kind is None:
            self.owner.toast(_("This type of file cannot be installed here."))
            return False
        if kind == "appimage":
            self.open_appimage(path)
        elif kind == "flatpakrepo":
            # A repository file adds a source, which Software Sources reviews.
            self.owner.preferences("software-sources", import_file=path)
        else:
            self._open(InstallDetailsPage(self, "file", value=path))
        return True

    def _dropped(self, _target, file, _x, _y):
        path = file.get_path()
        if not path:
            self.owner.toast(_("Choose a local file."))
            return False
        return self.open_file(path)

    def choose_file(self, *_args):
        native = self.owner.native_source
        extensions = [".AppImage", ".flatpakref", ".flatpak", ".flatpakrepo"]
        if native in {"rpm", "deb"}:
            extensions.append("." + native)
        choose_file(
            self.owner,
            _("Install from File"),
            _("Installable Files"),
            extensions,
            lambda path: None if self.closed else self.open_file(path),
            self.owner.toast,
        )


class InstallDetailsPage(Adw.NavigationPage):
    """Confirms one installation: where it comes from, its options and Install."""

    def __init__(self, page, kind, choice=None, value=""):
        super().__init__(title=_("Install"))
        self.page = page
        self.owner = page.owner
        self.kind = kind
        self.choice = choice
        self.file = None
        self.icon_file = ""
        self.package = None
        toolbar = Adw.ToolbarView()
        self.set_child(toolbar)
        toolbar.add_top_bar(Adw.HeaderBar())
        scroll, content = details_body()
        toolbar.set_content(scroll)
        # Laid out like an installed app's details: a centred header with the action,
        # then anything to heed before acting, then the details.
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        self.icon_preview = icon_image("application-x-executable", 96)
        self.icon_preview.set_halign(Gtk.Align.CENTER)
        hero.append(self.icon_preview)
        self.heading = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.heading.add_css_class("title-1")
        hero.append(self.heading)
        self.summary = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=60)
        self.summary.add_css_class("dim-label")
        hero.append(self.summary)
        self.install_button = Gtk.Button(label=_("Install"), halign=Gtk.Align.CENTER)
        self.install_button.add_css_class("pill")
        self.install_button.add_css_class("suggested-action")
        self.install_button.connect("clicked", self._install)
        hero.append(self.install_button)
        # The install started here takes the button's place until it finishes.
        self.activity_id = None
        self.progress = ActivityProgress(
            self.owner.activities,
            self.owner.cancel_operation,
            lambda activity: activity.id == self.activity_id,
            self,
            width_request=280,
        )
        self.progress.connect(
            "notify::visible",
            lambda progress, _p: self.install_button.set_visible(not progress.get_visible()),
        )
        hero.append(self.progress)
        content.append(hero)
        self.status = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, visible=False)
        self.status.add_css_class("error")
        self.status.connect(
            "notify::label", lambda label, _p: label.set_visible(bool(label.get_label()))
        )
        content.append(self.status)
        self.warning = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)
        content.append(self.warning)
        self.group = Adw.PreferencesGroup(title=_("Installation"))
        content.append(self.group)
        {
            "app": self._build_app,
            "web": self._build_web,
            "appimage": self._build_appimage,
            "file": self._build_file,
        }[kind](value)

    def _option_label(self, candidate):
        if candidate.source == "snap":
            label = candidate.origin or _("Snap Store")
        else:
            title = self.owner.sources[candidate.source][0]
            label = f"{title} · {candidate.origin}" if candidate.origin else title
        return _("%s (Installed)") % label if candidate.installed else label

    def _build_app(self, _value):
        choice = self.choice
        self.set_title(choice.title)
        self.heading.set_label(choice.title)
        self.summary.set_label(choice.summary)
        set_icon(
            self.icon_preview,
            choice.icon or ("application-x-executable" if choice.is_app else "package-x-generic"),
        )
        labels = [self._option_label(option) for option in choice.options]
        if len(choice.options) > 1:
            self.source_row = Adw.ComboRow(
                title=_("Install From"), model=Gtk.StringList.new(labels)
            )
            self.source_row.connect("notify::selected", lambda *_: self._option_changed())
        else:
            self.source_row = Adw.ActionRow(
                title=_("Install From"), subtitle=GLib.markup_escape_text(labels[0])
            )
        self.group.add(self.source_row)
        self.details_row = Adw.ActionRow(title=_("Package"))
        self.details_row.set_subtitle_selectable(True)
        self.group.add(self.details_row)
        self._option_changed()
        # The installed app may have been removed from its page meanwhile.
        self.connect("shown", lambda _page: self._option_changed())

    @property
    def candidate(self):
        if self.kind != "app":
            return None
        index = self.source_row.get_selected() if len(self.choice.options) > 1 else 0
        return self.choice.options[index]

    def _option_changed(self):
        candidate = self.candidate
        snap = candidate.source == "snap"
        if candidate.source in {"snap", "flatpak"}:
            package = candidate.target
        else:
            # A PackageKit ID reads "name;version;arch;repository".
            name, version, arch, *_rest = [*candidate.target.split(";"), "", "", ""]
            package = " · ".join(v for v in (f"{name} {version}".strip(), arch) if v)
        self.details_row.set_subtitle(GLib.markup_escape_text(package))
        if candidate.installed:
            # An installed copy is managed from its app page, not installed again.
            app = self.page.installed_app(candidate)
            self.install_button.set_label(_("View Details") if app else _("Installed"))
            self.install_button.set_sensitive(app is not None)
            self.install_button.remove_css_class("suggested-action")
            others = any(not option.installed for option in self.choice.options)
            self.group.set_description(
                _("Already installed from this source. Choose another source to install it there.")
                if others
                else _("Already installed.")
            )
            return
        self.install_button.set_label(_("Open in Snap Store") if snap else _("Install"))
        self.install_button.set_sensitive(True)
        self.install_button.add_css_class("suggested-action")
        self.group.set_description(
            _("Installation continues in the Snap Store.")
            if snap
            else _("Required dependencies are installed automatically.")
        )

    def _build_web(self, value):
        self.set_title(_("Web App"))
        self.group.set_title(_("Web App"))
        self.value = self._entry(_("Website URL"))
        self.value.set_input_purpose(Gtk.InputPurpose.URL)
        self.name = self._entry(_("Name (Optional)"))
        self.browser = Adw.ComboRow(
            title=_("Browser"), model=Gtk.StringList.new(["Google Chrome", "Chromium"])
        )
        self.group.add(self.browser)
        self.value.connect("changed", self._web_changed)
        self.name.connect("changed", self._web_changed)
        for row in (self.value, self.name):
            row.connect("entry-activated", self._install)
        self._build_icon_row()
        self.group.set_description(_("The web app opens in its own browser window."))
        self.value.set_text(value)
        self._web_changed()

    def _web_changed(self, *_args):
        url = self.value.get_text().strip()
        try:
            host = urlsplit(url if "://" in url else "//" + url).hostname or ""
        except ValueError:
            host = ""
        self.heading.set_label(self.name.get_text().strip() or host or _("Web App"))
        self.summary.set_label(_("Web app for %s") % host if host else "")
        self.install_button.set_sensitive(bool(url))
        self.status.set_label("")

    def _build_appimage(self, path):
        self.set_title(_("AppImage"))
        self.group.set_title(_("AppImage"))
        self.file_row = Adw.ActionRow(title=_("AppImage File"))
        self.file_row.set_subtitle_lines(1)
        self.file_button = Gtk.Button(label=_("Change…"), valign=Gtk.Align.CENTER)
        self.file_button.connect(
            "clicked",
            lambda _b: choose_appimage(self.owner, self._set_file, self.status.set_label),
        )
        self.file_row.add_suffix(self.file_button)
        self.file_row.set_activatable_widget(self.file_button)
        self.group.add(self.file_row)
        self.name = self._entry(_("Name"))
        self.name.connect("changed", self._appimage_changed)
        self.name.connect("entry-activated", self._install)
        self._build_icon_row()
        self.group.set_description(
            _(
                "A copy is kept in ~/AppImages and added to your app menu. The original file is kept."
            )
        )
        self._set_file(path)

    def _set_file(self, path):
        if self.page.closed:
            return
        self.file = path
        self.file_row.set_subtitle(GLib.markup_escape_text(Path(path).name))
        self.file_row.set_tooltip_text(path)
        self.summary.set_label(Path(path).name)
        self.name.set_text(appimage_name(path))
        self.status.set_label("")

    def _appimage_changed(self, entry):
        name = entry.get_text().strip()
        self.heading.set_label(name or _("AppImage"))
        self.install_button.set_sensitive(self.file is not None and bool(name))

    @staticmethod
    def _selected(row):
        return row.get_selected() if isinstance(row, Adw.ComboRow) else 0

    def _choice_row(self, title, labels):
        """A drop-down when there is a choice, otherwise a plain row naming the only one."""
        if len(labels) > 1:
            row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
        else:
            row = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(labels[0]))
        self.group.add(row)
        return row

    def _row(self, title, value, tooltip=None):
        row = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(value))
        row.set_subtitle_selectable(True)
        row.set_tooltip_text(tooltip)
        self.group.add(row)
        return row

    def _build_file(self, path):
        name = Path(path).name
        self.set_title(name)
        self.heading.set_label(name)
        self.summary.set_label(_("Reading the file…"))
        set_icon(self.icon_preview, "package-x-generic")
        self.install_button.set_sensitive(False)
        self.spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.START)
        self.group.add(self.spinner)

        def failed(error):
            if self.page.closed:
                return
            self.group.remove(self.spinner)
            self.summary.set_label("")
            self.status.set_label(error)
            self.install_button.set_visible(False)

        self.owner.service.inspect_install_file(path, self._file_ready, failed)

    def _file_ready(self, package):
        if self.page.closed:
            return
        self.group.remove(self.spinner)
        self.package = package
        flatpak = package.kind in {"flatpakref", "flatpak-bundle"}
        self.set_title(package.title)
        self.heading.set_label(package.title)
        self.summary.set_label(package.summary)
        if package.icon:
            try:
                self.icon_preview.set_from_paintable(
                    Gdk.Texture.new_from_bytes(GLib.Bytes.new(package.icon))
                )
            except GLib.Error:
                set_icon(self.icon_preview, "application-x-executable")
        else:
            set_icon(
                self.icon_preview, "application-x-executable" if flatpak else "package-x-generic"
            )
        native = self.owner.sources[package.kind][0] if not flatpak else ""
        source = {
            "flatpakref": _("Flatpak reference from %s") % package.origin,
            "flatpak-bundle": _("Flatpak bundle"),
        }.get(package.kind, _("%s package file") % native)
        self._row(_("Source"), source)
        self._row(_("Package"), " · ".join(v for v in (package.name, package.version) if v))
        self._row(_("File"), Path(package.path).name, package.path)
        if flatpak:
            self.target_row = self._choice_row(
                _("Install For"), [label for _path, label in package.installations]
            )
        else:
            self.untrusted_row = Adw.SwitchRow(
                title=_("Allow Unverified Package"),
                subtitle=_(
                    "Needed when the package is not signed by a key this system trusts. "
                    "Asks for administrator authorization."
                ),
            )
            self.group.add(self.untrusted_row)
        self._show_file_warning(package)
        self.group.set_description(_("Required dependencies are installed automatically."))
        self.install_button.set_sensitive(True)

    def _show_file_warning(self, package):
        from housekeeper.ui.update_confirmation import notice_card

        lines = [
            _(
                "This file is not from one of your software sources. Only install files "
                "from publishers you trust."
            )
        ]
        heading = _("Not From Your Software Sources")
        if package.kind == "flatpakref":
            if package.adds_remote:
                heading = _("Adds a Software Source")
                lines.append(
                    _(
                        "If %(host)s is not set up yet, it is added as the Flatpak source "
                        "“%(name)s”, which provides this app and its updates."
                    )
                    % {"host": package.origin, "name": package.adds_remote}
                )
            if not package.verified:
                lines.append(_("The file includes no signing key, so downloads are not verified."))
        elif package.kind == "flatpak-bundle":
            lines.append(_("Flatpak cannot verify who published a bundle."))
            lines.append(
                _("Updates come from %s.") % package.origin
                if package.origin
                else _("The bundle names no update source, so it will not receive updates.")
            )
        else:
            lines.append(
                _(
                    "It is updated only if one of your sources also provides it, or if the "
                    "package adds a source of its own."
                )
            )
        if package.runtime_repo:
            lines.append(_("A Flatpak source may be added for the runtime the app needs."))
        self.warning.append(notice_card(heading, "\n\n".join(lines), warning=True))
        self.warning.set_visible(True)

    def _build_icon_row(self):
        self.default_icon = "web-browser" if self.kind == "web" else "application-x-executable"
        self.default_icon_label = (
            _("Website icon (automatic)") if self.kind == "web" else _("Executable icon")
        )
        set_icon(self.icon_preview, self.default_icon)
        self.icon_row = Adw.ActionRow(
            title=_("Desktop Icon (Optional)"), subtitle=self.default_icon_label, use_markup=False
        )
        self.icon_row.set_title_lines(2)
        self.icon_row.set_subtitle_lines(1)
        self.icon_button = Gtk.Button(
            icon_name="document-edit-symbolic",
            tooltip_text=_("Choose Icon"),
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        self.icon_button.connect("clicked", self._choose_icon)
        self.icon_row.add_suffix(self.icon_button)
        self.icon_row.set_activatable_widget(self.icon_button)
        self.icon_reset = Gtk.Button(
            icon_name="edit-undo-symbolic",
            tooltip_text=_("Use Default Icon"),
            valign=Gtk.Align.CENTER,
            sensitive=False,
            css_classes=["flat"],
        )
        self.icon_reset.connect("clicked", self._reset_icon)
        self.icon_row.add_suffix(self.icon_reset)
        self.group.add(self.icon_row)

    def _reset_icon(self, *_args):
        self.icon_file = ""
        set_icon(self.icon_preview, self.default_icon)
        self.icon_row.set_subtitle(self.default_icon_label)
        self.icon_row.set_tooltip_text(None)
        self.icon_reset.set_sensitive(False)

    def _choose_icon(self, *_args):
        filters = Gio.ListStore.new(Gtk.FileFilter)
        images = Gtk.FileFilter(name=_("Images"))
        images.add_pixbuf_formats()
        filters.append(images)
        chooser = Gtk.FileDialog(title=_("Choose Desktop Icon"), filters=filters)

        def chosen(dialog, result):
            try:
                file = dialog.open_finish(result)
                if self.page.closed:
                    return
                path = file.get_path()
                if path is None:
                    raise ManagementError(_("Choose a local image file."))
                load_icon_image(path, 96)
                self.icon_file = path
                set_icon(self.icon_preview, path)
                self.icon_row.set_subtitle(Path(path).name)
                self.icon_row.set_tooltip_text(path)
                self.icon_reset.set_sensitive(True)
                self.status.set_label("")
            except (GLib.Error, ManagementError, OSError) as error:
                if self.page.closed:
                    return
                if isinstance(error, GLib.Error) and error.matches(
                    Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED
                ):
                    return
                self.status.set_label(str(error))

        chooser.open(self.owner, None, chosen)

    def _entry(self, title):
        row = Adw.EntryRow(title=title)
        self.group.add(row)
        return row

    def _install(self, *_args):
        if not self.install_button.get_sensitive():
            return
        candidate = self.candidate
        if candidate is not None and candidate.installed:
            app = self.page.installed_app(candidate)
            if app is not None:
                self.owner.show_details(app)
            return
        if candidate is not None and candidate.source == "snap":
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", candidate.name):
                self.status.set_label(_("This Snap package name is not valid."))
                return
            Gtk.UriLauncher.new("snap://" + candidate.name).launch(
                self.owner, None, self.owner._uri_opened
            )
            return
        if self.kind == "web":
            try:
                url = normalize_url(self.value.get_text())
            except ManagementError as error:
                self.status.set_label(str(error))
                return
            request = InstallRequest(
                "web",
                url,
                self.name.get_text(),
                ("chrome", "chromium")[self.browser.get_selected()],
                icon=self.icon_file,
            )
        elif self.kind == "appimage":
            request = InstallRequest(
                "appimage", self.file, self.name.get_text(), icon=self.icon_file
            )
        elif self.kind == "file":
            package = self.package
            flatpak = package.kind in {"flatpakref", "flatpak-bundle"}
            request = InstallRequest(
                "file",
                package=package,
                context=package.installations[self._selected(self.target_row)][0]
                if flatpak
                else "",
                untrusted=not flatpak and self.untrusted_row.get_active(),
            )
        else:
            request = InstallRequest(candidate.source, candidate=candidate)
        if not self.owner._begin_operation(None, "install", subject=self.heading.get_label()):
            return
        self.activity_id = self.owner.activity_id
        self.owner._start_task(_("Installing %s") % self.heading.get_label())
        self.owner.service.install(
            request,
            self.owner._guard(self.owner._progress),
            self.owner._guard(self.owner._operation_finished),
        )
