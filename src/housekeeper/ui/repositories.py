"""Repository management and update-source preferences on the Software Sources page."""

from gi.repository import Adw, Gio, GLib, Gtk

from housekeeper.i18n import _


class SoftwareSourcesPage(Adw.PreferencesPage):
    def __init__(self, owner, preferences):
        super().__init__(
            title=_("Software Sources"),
            icon_name="network-server-symbolic",
            name="software-sources",
        )
        self.owner = owner
        self.preferences = preferences
        self.closed = False
        self.busy = False
        self.loading = False
        self.loaded = False
        self.native_sources_expanded = False
        self.groups = []
        self.controls = []
        self.snapshot = ()
        self.rows = {}
        update_sources = Adw.PreferencesGroup(
            title=_("Update Sources"),
            description=_(
                "Choose application types for the Updates page. This does not change repository settings or individual app checks."
            ),
        )
        for source, title in (("rpm", _("RPM Packages")), ("flatpak", _("Flatpak"))):
            row = Adw.SwitchRow(title=title)
            owner.settings.bind(
                "update-source-" + source, row, "active", Gio.SettingsBindFlags.DEFAULT
            )
            update_sources.add(row)
        self.add(update_sources)
        divider = Adw.PreferencesGroup()
        divider.add(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, css_classes=["card"]))
        self.add(divider)
        self.overview = Adw.PreferencesGroup(
            title=_("Install Sources"),
            description=_(
                "Repositories used to install applications and check for updates. Disabling a source keeps its installed applications."
            ),
        )
        self.refresh_button = Gtk.Button(
            icon_name="view-refresh-symbolic",
            tooltip_text=_("Refresh Software Sources"),
            valign=Gtk.Align.CENTER,
        )
        self.refresh_button.connect("clicked", lambda *_: self.reload())
        self.overview.set_header_suffix(self.refresh_button)
        self.status = Adw.ActionRow(title=_("Open this page to read configured sources."))
        self.status.set_title_lines(0)
        self.spinner = Gtk.Spinner()
        self.status.add_prefix(self.spinner)
        self.status_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"]
        )
        self.status_list.append(self.status)
        self.overview.add(self.status_list)
        self.add(self.overview)
        self.connect(
            "map", lambda *_: self.reload() if not self.loaded and not self.loading else None
        )
        preferences.connect("close-request", self._close)
        preferences.connect("unrealize", lambda *_: setattr(self, "closed", True))

    def _close(self, *_args):
        if self.busy:
            self.status.set_title(_("Wait for the software source operation to finish."))
            return True
        self.closed = True
        return False

    def _status(self, text, working=False):
        self.status.set_title(GLib.markup_escape_text(text))
        self.status.set_visible(bool(text))
        # Hide the whole card so an empty status leaves no shadow below the heading.
        self.status_list.set_visible(bool(text))
        self.spinner.set_spinning(working)
        self.spinner.set_visible(working)
        self.refresh_button.set_sensitive(not working)
        for control in self.controls:
            control.set_sensitive(not working)

    def reload(self, notice=""):
        if self.closed or self.loading or self.busy:
            return
        self.loading = True
        self._status(_("Reading software sources…"), True)

        def complete(groups):
            self.loading = False
            if self.closed:
                return
            self.loaded = True
            self.snapshot = groups
            self._render(groups)
            self._status(notice)

        def failed(error):
            self.loading = False
            if not self.closed:
                self._status(str(error))

        self.owner.service.software_sources(complete, failed)

    def _render(self, groups):
        for group in self.groups:
            self.remove(group)
        self.groups, self.controls, self.rows = [], [], {}
        for record in groups:
            group = Adw.PreferencesGroup(title=record.title)
            hidden_sources = []
            if record.provider == "native":
                group.set_description(
                    _("Use your system's software manager to add or remove system repositories.")
                )
            if record.can_add:
                button = Gtk.Button(
                    icon_name="list-add-symbolic",
                    tooltip_text=_("Add Flatpak Source"),
                    valign=Gtk.Align.CENTER,
                )
                button.connect("clicked", lambda _button, current=record: self.add_source(current))
                group.set_header_suffix(button)
                self.controls.append(button)
            if record.error or not record.sources:
                row = Adw.ActionRow(
                    title=_("Source Information Unavailable")
                    if record.error
                    else _("No Software Sources"),
                    subtitle=GLib.markup_escape_text(
                        record.error or _("Add a source to find applications here.")
                    ),
                )
                row.set_subtitle_lines(0)
                group.add(row)
            for source in sorted(
                record.sources,
                key=lambda value: (
                    record.provider == "native" and not value.enabled,
                    value.title.casefold(),
                    value.identifier,
                ),
            ):
                details = [source.identifier]
                if source.url:
                    details.append(source.url)
                row = Adw.SwitchRow(
                    title=GLib.markup_escape_text(source.title),
                    subtitle=GLib.markup_escape_text(" · ".join(details)),
                    active=source.enabled,
                )
                row.set_title_lines(2)
                row.set_subtitle_lines(2)
                row.set_tooltip_text("\n".join(details))
                if record.provider == "native" and not source.enabled:
                    row.set_visible(self.native_sources_expanded)
                    hidden_sources.append(row)
                row.connect("notify::active", self._toggled, source)
                group.add(row)
                self.controls.append(row)
                self.rows[(source.provider, source.context, source.identifier)] = row
            if hidden_sources:
                more = Adw.ActionRow(
                    title=_("Show Fewer Sources")
                    if self.native_sources_expanded
                    else _("Show Disabled Sources"),
                    activatable=True,
                )
                arrow = Gtk.Image(
                    icon_name="pan-up-symbolic"
                    if self.native_sources_expanded
                    else "pan-down-symbolic"
                )
                more.add_suffix(arrow)
                more.connect("activated", self._toggle_native_sources, hidden_sources, arrow)
                group.add(more)
                self.controls.append(more)
            self.groups.append(group)
            self.add(group)

    def _toggle_native_sources(self, row, hidden_sources, arrow):
        if self.closed or self.busy or self.loading:
            return
        self.native_sources_expanded = not self.native_sources_expanded
        for source_row in hidden_sources:
            source_row.set_visible(self.native_sources_expanded)
        row.set_title(
            _("Show Fewer Sources") if self.native_sources_expanded else _("Show Disabled Sources")
        )
        arrow.set_from_icon_name(
            "pan-up-symbolic" if self.native_sources_expanded else "pan-down-symbolic"
        )

    def _toggled(self, row, _pspec, source):
        enabled = row.get_active()
        if enabled == source.enabled:
            return
        self._change(source, enabled)

    def _begin(self, title):
        if (
            self.closed
            or self.busy
            or self.loading
            or not self.owner._begin_operation(None, "sources", self._status)
        ):
            return False
        self.busy = True
        self._status(title, True)
        return True

    def _finish(self):
        self.busy = False
        self.owner._end_operation()

    def _change(self, source, enabled):
        if not self._begin(_("Saving software source…")):
            self._render(self.snapshot)
            return

        def complete(_result):
            self._finish()
            self.owner.updates_page.invalidate()
            self.owner.refresh()
            self.reload(_("Software source saved."))

        def failed(error):
            self._finish()
            # Reload the real configuration: failure or cancellation can occur
            # after the underlying manager has already saved a change.
            self.owner.updates_page.invalidate()
            self.reload(str(error))

        self.owner.service.change_software_source(source, enabled, complete, failed)

    def add_source(self, record):
        if self.closed or self.busy or self.loading:
            return
        dialog = Adw.MessageDialog(
            transient_for=self.preferences,
            heading=_("Add Flatpak Source"),
            body=_(
                "Paste an HTTPS .flatpakrepo URL or choose a file. The source will be added to %s."
            )
            % record.title,
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        fields = Adw.PreferencesGroup()
        location = Adw.EntryRow(title=_("Source URL or File"))
        name = Adw.EntryRow(title=_("Name (Optional)"))
        fields.add(location)
        fields.add(name)
        box.append(fields)
        choose = Gtk.Button(label=_("Choose .flatpakrepo File…"))
        box.append(choose)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("review", _("Review Source"))
        dialog.set_response_appearance("review", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_enabled("review", False)
        dialog.set_default_response("review")
        dialog.set_close_response("cancel")
        location.connect(
            "changed",
            lambda entry: dialog.set_response_enabled("review", bool(entry.get_text().strip())),
        )

        def chosen(chooser, result):
            try:
                file = chooser.open_finish(result)
                if not self.closed and dialog.get_visible() and file.get_path():
                    location.set_text(file.get_path())
            except GLib.Error as error:
                if (
                    not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED)
                    and not self.closed
                ):
                    dialog.set_body(str(error))

        def browse(*_args):
            filters = Gio.ListStore.new(Gtk.FileFilter)
            file_filter = Gtk.FileFilter(name=_("Flatpak Repository Files"))
            file_filter.add_pattern("*.flatpakrepo")
            filters.append(file_filter)
            chooser = Gtk.FileDialog(title=_("Choose Flatpak Source"), filters=filters)
            chooser.open(dialog, None, chosen)

        choose.connect("clicked", browse)

        def responded(_dialog, response):
            if response != "review" or not self._begin(_("Reading source details…")):
                return

            def ready(plan):
                self._finish()
                if not self.closed:
                    self._status("")
                    self._confirm_add(record, plan)

            def failed(error):
                self._finish()
                if not self.closed:
                    self._status(str(error))

            self.owner.service.prepare_software_source(
                record.context, location.get_text(), name.get_text(), ready, failed
            )

        dialog.connect("response", responded)
        dialog.present()
        return dialog

    def _confirm_add(self, record, plan):
        body = "\n\n".join(
            (
                record.title,
                _("Name: %s") % plan.name,
                _("Repository: %s") % plan.url,
                _("Signature verification: Enabled")
                if plan.verified
                else _("Signature verification: Disabled"),
            )
        )
        dialog = Adw.MessageDialog(
            transient_for=self.preferences, heading=_("Add %s?") % plan.title, body=body
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("add", _("Add Source"))
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")
        dialog.set_default_response("add")
        dialog.connect(
            "response",
            lambda _dialog, response: self._change(plan, None) if response == "add" else None,
        )
        dialog.present()
        return dialog
