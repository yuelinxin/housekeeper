"""Unified installation dialog with a type selector and cancellable package suggestions."""

from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from housekeeper.appearance import load_icon_image
from housekeeper.i18n import _
from housekeeper.installations import InstallRequest, appimage_name, normalize_url
from housekeeper.models import ManagementError
from housekeeper.ui.icons import icon_image, set_icon


class InstallWindow(Adw.Window):
    def __init__(self, window):
        super().__init__(
            transient_for=window,
            modal=True,
            title=_("Install Application"),
            default_width=480,
            default_height=420,
        )
        self.owner = window
        self.source = window.native_source
        self.closed = False
        self.timer = 0
        self.serial = 0
        self.candidate = None
        self.file = None
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        self.add_button = Gtk.Button(label=_("Install"), sensitive=False)
        self.add_button.add_css_class("suggested-action")
        self.add_button.connect("clicked", self._install)
        header.pack_end(self.add_button)
        toolbar.add_top_bar(header)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            margin_start=18,
            margin_end=18,
            margin_top=18,
            margin_bottom=18,
        )
        scroll.set_child(self.content)
        toolbar.set_content(scroll)
        self.set_content(toolbar)
        self.types = [window.native_source, "flatpak", "web", "appimage", "steam", "snap"]
        self.type_row = Adw.ComboRow(
            title=_("Application Type"),
            model=Gtk.StringList.new([window.sources[k][0] for k in self.types]),
        )
        group = Adw.PreferencesGroup()
        group.add(self.type_row)
        self.content.append(group)
        self.type_row.connect("notify::selected", self._type_changed)
        self.form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.content.append(self.form)
        self.connect("close-request", self._closing)
        self._build_form()

    def _type_changed(self, row, _pspec):
        self.source = self.types[row.get_selected()]
        self._build_form()

    def _stop_search(self):
        self.serial += 1
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        self.owner.service.cancel_install_search()

    def _closing(self, *_args):
        self.closed = True
        self._stop_search()
        if self.owner.install_dialog is self:
            self.owner.install_dialog = None
        return False

    def _build_form(self):
        self._stop_search()
        while child := self.form.get_first_child():
            self.form.remove(child)
        self.candidate, self.file = None, None
        self.icon_file = ""
        self.add_button.set_sensitive(False)
        self.add_button.set_label(_("Install"))
        descriptions = {
            "web": _(
                "Enter a website to add it to your app menu. It opens in its own browser window."
            ),
            "appimage": _(
                "Choose an AppImage. A copy is kept in ~/AppImages and added to your app menu. The original file is kept."
            ),
            "steam": _(
                "Open Steam to install a game, then create its desktop shortcut from your Steam library."
            ),
            "snap": _("Enter a Snap package name to open its installation page in the Snap Store."),
        }
        description = Gtk.Label(
            label=descriptions.get(
                self.source,
                # Native and Flatpak searches use AppStream: app names and keywords match.
                _(
                    "Search by app name or keyword and select a match. Required dependencies are installed automatically."
                ),
            ),
            wrap=True,
            xalign=0,
        )
        self.form.append(description)
        self.group = Adw.PreferencesGroup()
        self.form.append(self.group)
        self.status = Gtk.Label(wrap=True, xalign=0)
        self.status.add_css_class("dim-label")
        if self.source == "web":
            self.value = self._entry(_("Website URL"))
            self.name = self._entry(_("Name (Optional)"))
            self.browser = Adw.ComboRow(
                title=_("Browser"), model=Gtk.StringList.new(["Google Chrome", "Chromium"])
            )
            self.group.add(self.browser)
            self.value.connect("changed", self._web_changed)
        elif self.source == "appimage":
            self.file_button = Gtk.Button()
            self.file_label = Gtk.Label(
                label=_("Choose AppImage…"),
                ellipsize=Pango.EllipsizeMode.MIDDLE,
                max_width_chars=30,
            )
            self.file_button.set_child(self.file_label)
            self.file_button.connect("clicked", self._choose_file)
            self.form.remove(self.group)
            self.form.append(self.file_button)
            self.form.append(self.group)
            self.group.set_visible(False)
            self.name = self._entry(_("Name"))
            self.name.connect(
                "changed",
                lambda entry: self.add_button.set_sensitive(
                    self.file is not None and bool(entry.get_text().strip())
                ),
            )
        elif self.source == "steam":
            self.add_button.set_label(_("Open Steam"))
            self.add_button.set_sensitive(True)
        else:
            if self.source == "snap":
                self.add_button.set_label(_("Open Store"))
            self.value = self._entry(_("Package Name") if self.source == "snap" else _("App Name"))
            self.value.connect("changed", self._query_changed)
            self.results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
            self.results.add_css_class("boxed-list")
            self.results.connect("row-selected", self._selected)
            self.results.set_visible(False)
            self.form.append(self.results)
            self.status.set_label(_("Type at least two characters to search configured sources."))
        if self.source in {"web", "appimage"}:
            self._build_icon_row()
        self.form.append(self.status)

    def _build_icon_row(self):
        self.default_icon = "web-browser" if self.source == "web" else "application-x-executable"
        self.default_icon_label = (
            _("Website icon (automatic)") if self.source == "web" else _("Executable icon")
        )
        self.icon_row = Adw.ActionRow(
            title=_("Desktop Icon (Optional)"), subtitle=self.default_icon_label, use_markup=False
        )
        self.icon_row.set_title_lines(2)
        self.icon_row.set_subtitle_lines(1)
        self.icon_preview = icon_image(self.default_icon, 40)
        self.icon_row.add_prefix(self.icon_preview)
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
        serial = self.serial

        def chosen(dialog, result):
            try:
                file = dialog.open_finish(result)
                if self.closed or serial != self.serial:
                    return
                path = file.get_path()
                if path is None:
                    raise ManagementError(_("Choose a local image file."))
                load_icon_image(path, 40)
                self.icon_file = path
                set_icon(self.icon_preview, path)
                self.icon_row.set_subtitle(Path(path).name)
                self.icon_row.set_tooltip_text(path)
                self.icon_reset.set_sensitive(True)
                self.status.set_label("")
            except (GLib.Error, ManagementError, OSError) as error:
                if self.closed or serial != self.serial:
                    return
                if isinstance(error, GLib.Error) and error.matches(
                    Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED
                ):
                    return
                self.status.set_label(str(error))

        chooser.open(self, None, chosen)

    def _entry(self, title):
        row = Adw.EntryRow(title=title)
        self.group.add(row)
        return row

    def _web_changed(self, entry):
        self.add_button.set_sensitive(bool(entry.get_text().strip()))
        self.status.set_label("")

    def _query_changed(self, entry):
        self._stop_search()
        self.candidate = None
        self.add_button.set_sensitive(False)
        while child := self.results.get_first_child():
            self.results.remove(child)
        self.results.set_visible(False)
        query = entry.get_text().strip()
        self.status.set_label(
            _("Searching…")
            if len(query) >= 2
            else _("Type at least two characters to search configured sources.")
        )
        if len(query) < 2:
            return
        serial = self.serial

        def searched(candidates):
            if self.closed or serial != self.serial:
                return
            for candidate in candidates:
                row = Adw.ActionRow(
                    title=GLib.markup_escape_text(candidate.title or candidate.name),
                    subtitle=GLib.markup_escape_text(candidate.description),
                    activatable=True,
                    selectable=True,
                )
                row.set_title_lines(1)
                row.set_subtitle_lines(2)
                if candidate.title:
                    # Catalogue apps carry their own icon; the rest keep the plain row.
                    icon = (
                        Gtk.Image.new_from_file(candidate.icon)
                        if candidate.icon
                        else Gtk.Image(icon_name="application-x-executable")
                    )
                    icon.set_pixel_size(32)
                    row.add_prefix(icon)
                row.candidate = candidate
                row.add_suffix(Gtk.Image(icon_name="list-add-symbolic"))
                self.results.append(row)
            self.results.unselect_all()
            self.results.set_visible(bool(candidates))
            self.status.set_label(
                _("Select a package to install.")
                if candidates
                else _(
                    "No matching packages found. Try a shorter name; the app may already be installed."
                )
            )

        def failed(error):
            if not self.closed and serial == self.serial:
                self.status.set_label(str(error))

        def search():
            self.timer = 0
            self.owner.service.search_install(self.source, query, searched, failed)
            return GLib.SOURCE_REMOVE

        self.timer = GLib.timeout_add(400, search)

    def _selected(self, _list, row):
        self.candidate = row.candidate if row is not None else None
        self.add_button.set_sensitive(self.candidate is not None)

    def _choose_file(self, *_args):
        chooser = Gtk.FileDialog(title=_("Choose AppImage"))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        appimages = Gtk.FileFilter(name=_("AppImage Files"))
        appimages.add_pattern("*.AppImage")
        appimages.add_pattern("*.appimage")
        filters.append(appimages)
        all_files = Gtk.FileFilter(name=_("All Files"))
        all_files.add_pattern("*")
        filters.append(all_files)
        chooser.set_filters(filters)
        serial = self.serial

        def chosen(dialog, result):
            try:
                file = dialog.open_finish(result)
                if self.closed or serial != self.serial:
                    return
                path = file.get_path()
                if not path:
                    self.status.set_label(_("Choose a local AppImage file."))
                    return
                self.file = path
                self.file_label.set_label(Path(path).name)
                self.file_button.set_tooltip_text(path)
                self.group.set_visible(True)
                self.name.set_text(appimage_name(path))
                self.status.set_label("")
                self.add_button.set_sensitive(bool(self.name.get_text().strip()))
            except GLib.Error as error:
                if (
                    not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED)
                    and not self.closed
                ):
                    self.status.set_label(str(error))

        chooser.open(self, None, chosen)

    def _install(self, *_args):
        if not self.add_button.get_sensitive():
            return
        if self.source in {"steam", "snap"}:
            import re

            if self.source == "snap":
                name = self.candidate.name
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
                    self.status.set_label(_("Enter a valid Snap package name."))
                    return
                uri = "snap://" + name
            else:
                uri = "steam://open/games"
            Gtk.UriLauncher.new(uri).launch(self.owner, None, self.owner._uri_opened)
            self.close()
            return
        if self.source == "web":
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
        elif self.source == "appimage":
            request = InstallRequest(
                "appimage", self.file, self.name.get_text(), icon=self.icon_file
            )
        else:
            request = InstallRequest(self.source, candidate=self.candidate)
        # This dialog covers the window's toasts, so report a refusal in its own status.
        if not self.owner._begin_operation(None, "install", self.status.set_label):
            return
        self.close()
        self.owner._show_task(_("Installing Application"))
        self.owner.service.install(
            request,
            self.owner._guard(self.owner._progress),
            self.owner._guard(self.owner._operation_finished),
        )
