"""Updates navigation page; checks are initiated only by explicit user interaction."""

from gi.repository import Adw, GLib, GObject, Gtk

from housekeeper.i18n import _
from housekeeper.models import Outcome
from housekeeper.ui.icons import icon_image
from housekeeper.updates import authorization_notice


class UpdatesPage(Adw.NavigationPage):
    def __init__(self, window):
        super().__init__(title=_("Updates"), tag="updates")
        self.window = window
        self.items = ()
        self.checks = []
        self.visited = False
        self.check_when_ready = False
        toolbar = Adw.ToolbarView()
        self.set_child(toolbar)
        header = Adw.HeaderBar(show_back_button=False)
        toggle = Gtk.Button(icon_name="sidebar-show-symbolic", tooltip_text=_("Show Sources"))
        toggle.connect("clicked", lambda _b: window.split.set_show_sidebar(True))
        window.split.bind_property("collapsed", toggle, "visible", GObject.BindingFlags.SYNC_CREATE)
        window.split.bind_property(
            "collapsed", header, "show-start-title-buttons", GObject.BindingFlags.SYNC_CREATE
        )
        header.pack_start(toggle)
        self.refresh_button = Gtk.Button(
            icon_name="view-refresh-symbolic", tooltip_text=_("Check for Updates")
        )
        self.refresh_button.connect("clicked", lambda _b: self.check())
        header.pack_end(self.refresh_button)
        toolbar.add_top_bar(header)
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_start=16,
            margin_end=16,
            margin_top=12,
            margin_bottom=16,
        )
        toolbar.set_content(content)
        title = Gtk.Label(label=_("Keep your apps up to date"), xalign=0, wrap=True)
        title.add_css_class("title-2")
        content.append(title)
        self.status = Gtk.Label(
            label=_("Check your configured software sources for updates."), xalign=0, wrap=True
        )
        self.status.add_css_class("dim-label")
        content.append(self.status)
        self.errors_button = Gtk.Button(
            label=_("Check Details"), halign=Gtk.Align.START, visible=False
        )
        self.errors_button.connect(
            "clicked", lambda _b: window.message(_("Update Check Details"), self.details)
        )
        content.append(self.errors_button)
        self.actions = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            min_children_per_line=1,
            max_children_per_line=2,
            column_spacing=8,
            row_spacing=8,
        )
        self.selected_button = Gtk.Button(label=_("Update Selected"))
        self.all_button = Gtk.Button(label=_("Update All"))
        self.all_button.add_css_class("suggested-action")
        self.selected_button.connect("clicked", lambda _b: self.confirm(self.selected()))
        self.all_button.connect("clicked", lambda _b: self.confirm(self.items))
        for button in (self.selected_button, self.all_button):
            self.actions.insert(button, -1)
        content.append(self.actions)
        self.selection_label = Gtk.Label(label=_("No apps selected"), xalign=0)
        content.append(self.selection_label)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        self.list.set_valign(Gtk.Align.START)
        self.empty = Adw.StatusPage(
            title=_("Check for Updates"),
            icon_name="software-update-available-symbolic",
            description=_("Available app and runtime updates will appear here."),
        )
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self.empty, "empty")
        self.stack.add_named(
            Gtk.ScrolledWindow(child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER), "list"
        )
        content.append(self.stack)
        self.details = ""
        self._selection_changed()

    def enter(self):
        if not self.visited:
            if self.window.service.scanning:
                self.check_when_ready = True
                self.status.set_label(_("Waiting for the application inventory."))
            else:
                self.check()

    def inventory_ready(self):
        current = {a.key: a for a in self.window.records}
        kept = tuple(item for item in self.items if current.get(item.app.key) == item.app)
        if kept != self.items:
            self.render(kept)
            self.status.set_label(
                _("Installed applications changed. Check again for remaining updates.")
            )
        if self.check_when_ready:
            self.check_when_ready = False
            self.check()

    def selected(self):
        return tuple(item for item, check in self.checks if check.get_active())

    def _selection_changed(self, *_args):
        count = len(self.selected())
        self.selection_label.set_label(_("%d selected") % count if count else _("No apps selected"))
        active = self.window.operation_active
        self.selected_button.set_sensitive(bool(count) and not active)
        self.all_button.set_sensitive(bool(self.items) and not active)
        self.refresh_button.set_sensitive(not active)
        for _item, check in self.checks:
            check.set_sensitive(not active)

    def check(self):
        window = self.window
        if not window._begin_operation(None, "update"):
            return
        self.visited = True
        self._selection_changed()
        window._show_task(_("Checking for Updates"))
        window.service.check_updates(
            window._guard(window._progress), window._guard(self.checked), window._guard(self.failed)
        )

    def checked(self, report):
        self.window._end_operation()
        self.render(report.items)
        self.details = "\n\n".join(report.errors)
        self.errors_button.set_visible(bool(report.errors))
        if report.cancelled:
            text = _("Check cancelled. Results below may be incomplete.")
            empty_title = _("Check Cancelled")
        elif report.errors:
            text = _("Some apps could not be checked. Results below may be incomplete.")
            empty_title = _("Could Not Check All Apps")
        elif report.items:
            text = _(
                "%d updates available. Select apps or update all, then review the changes."
            ) % len(report.items)
            empty_title = _("Updates Available")
        else:
            text = _("No updates are available from the configured software sources.")
            empty_title = _("You're Up to Date")
        if report.unsupported:
            text += (
                "\n"
                + _(
                    "%d apps require their own updater. Open their details for update instructions."
                )
                % report.unsupported
            )
        self.status.set_label(text)
        self.empty.set_title(empty_title)
        self.empty.set_description(text)

    def failed(self, error):
        self.window._end_operation()
        self._selection_changed()
        self.status.set_label(_("The update check failed. Previous results may be out of date."))
        self.window.message(_("Could Not Check Updates"), str(error))

    def render(self, items):
        selected = {item.app.key for item in self.selected()}
        self.items = tuple(items)
        self.checks = []
        while child := self.list.get_first_child():
            self.list.remove(child)
        for item in items:
            app, plan = item.app, item.plan
            row = Gtk.ListBoxRow(activatable=False)
            check = Gtk.CheckButton(margin_start=12, margin_end=12, margin_top=12, margin_bottom=12)
            box = Gtk.Box(spacing=12)
            box.append(icon_image(app.icon, 40))
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
            name = Gtk.Label(label=", ".join(item.names), xalign=0, wrap=True)
            name.add_css_class("heading")
            text.append(name)
            primary = next((c for c in plan.changes if c.identity == app.identity), None)
            if app.provider == "rpm":
                primary = next((c for c in plan.changes if c.target == plan.target), None)
            if primary:
                old, new = primary.current_version, primary.target_version
                if app.provider == "flatpak":
                    old, new = old[:12], new[:12]
                version = f"{old} → {new}"
            else:
                version = _("Runtime or extension updates only")
            subtitle = Gtk.Label(
                label=f"{app.source.value.upper()} · {app.scope}\n{version}", xalign=0, wrap=True
            )
            subtitle.add_css_class("dim-label")
            text.append(subtitle)
            box.append(text)
            check.set_child(box)
            check.update_property([Gtk.AccessibleProperty.LABEL], [", ".join(item.names)])
            check.set_active(app.key in selected)
            check.connect("toggled", self._selection_changed)
            row.set_child(check)
            self.list.append(row)
            self.checks.append((item, check))
        self.stack.set_visible_child_name("list" if items else "empty")
        self.window.updates_count.set_label(str(len(items)) if items else "")
        self._selection_changed()

    def confirm(self, items):
        if not items or not self.window._begin_operation(None, "update"):
            return
        self._selection_changed()
        items = tuple(items)
        dialog = Adw.MessageDialog(
            transient_for=self.window,
            modal=True,
            heading=(
                _("Update %s?") % ", ".join(items[0].names)
                if len(items) == 1
                else _("Update %d apps?") % len(items)
            ),
            body=_(
                "Review all application and dependency changes. Updates run in order and stop if a plan changes or an update fails. Personal application data is kept."
            ),
        )
        notice = next(
            (authorization_notice(item.app) for item in items if authorization_notice(item.app)), ""
        )
        if notice:
            dialog.set_body(dialog.get_body() + "\n\n" + notice)
        self.window.confirm_dialog = dialog
        lines = []
        for item in items:
            lines.append(f"{', '.join(item.names)} · {item.app.scope}\n{item.plan.message}")
            for change in item.plan.changes:
                old, new = change.current_version, change.target_version
                if item.app.provider == "flatpak":
                    old, new = old[:12], new[:12]
                operation = _("Install") if change.operation == "install" else _("Update")
                lines.append(
                    f"{operation}: {change.identity}\n{old or _('Not installed')} → {new}\n{change.source}"
                )
            if item.plan.download_size is not None:
                lines.append(
                    _("Estimated download: %s") % GLib.format_size(item.plan.download_size)
                )
        preview = Gtk.Label(label="\n\n".join(lines), wrap=True, selectable=True, xalign=0)
        dialog.set_extra_child(
            Gtk.ScrolledWindow(
                child=preview,
                max_content_height=300,
                propagate_natural_height=True,
                hscrollbar_policy=Gtk.PolicyType.NEVER,
            )
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("update", _("Update"))
        dialog.set_response_appearance("update", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(_dialog, choice):
            self.window.confirm_dialog = None
            if choice != "update":
                self.window._end_operation()
                self._selection_changed()
                return
            self.window._show_task(_("Updating Applications"))
            self.window.service.execute_updates(
                items, self.window._guard(self.window._progress), self.window._guard(self.finished)
            )

        dialog.connect("response", self.window._guard(response))
        dialog.present()

    def finished(self, result):
        self.window._end_operation()
        # Dependencies and app identities can overlap: refresh before another explicit check.
        self.render(())
        self.status.set_label(_("Check again to find any remaining updates."))
        self.empty.set_title(_("Update Results"))
        self.empty.set_description(self.status.get_label())
        titles = {
            Outcome.SUCCESS: _("Updates Complete"),
            Outcome.PARTIAL: _("Updates Partially Complete"),
            Outcome.CANCELLED: _("Updates Cancelled"),
            Outcome.FAILED: _("Updates Failed"),
        }
        text = result.message
        if result.completed:
            text += "\n\n" + _("Completed:") + "\n" + "\n".join(result.completed)
        if result.errors:
            text += "\n\n" + "\n".join(result.errors)
        if result.restart_hint:
            text += "\n\n" + result.restart_hint
        self.window.message(titles[result.outcome], text)
        self.window.refresh()
