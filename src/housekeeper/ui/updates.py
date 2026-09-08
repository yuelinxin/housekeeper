"""Updates navigation page; checks are initiated only by explicit user interaction."""

import time
from dataclasses import replace

from gi.repository import Adw, GLib, GObject, Gtk

from housekeeper.batch_updates import UPDATE_PROVIDERS, installation_key
from housekeeper.i18n import _
from housekeeper.models import Outcome
from housekeeper.ui.icons import icon_image
from housekeeper.update_cache import UpdateCache, cache_expired
from housekeeper.updates import authorization_notice


class UpdatesPage(Adw.NavigationPage):
    def __init__(self, window):
        super().__init__(title=_("Updates"), tag="updates")
        self.window = window
        self.items = ()
        self.checks = []
        self.visited = False
        self.check_when_ready = False
        self.cache = UpdateCache()
        self.cache_loaded = False
        self.checked_at = None
        self.inventory_snapshot = None
        self.report = None
        self.stale = False
        self.updated_keys = set()
        self.source_revision = 0
        self.checking = False
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
        self.status = Gtk.Label(label=_("Check for Updates"), xalign=0, wrap=True)
        self.status.add_css_class("title-2")
        content.append(self.status)
        metadata = Gtk.Box(spacing=12, halign=Gtk.Align.START)
        content.append(metadata)
        self.last_checked = Gtk.Label(xalign=0, visible=False)
        self.last_checked.add_css_class("dim-label")
        metadata.append(self.last_checked)
        self.errors_button = Gtk.Button(label=_("Details"), halign=Gtk.Align.START, visible=False)
        self.errors_button.add_css_class("flat")
        self.errors_button.connect(
            "clicked", lambda _b: window.message(_("Update Check Details"), self.details)
        )
        metadata.append(self.errors_button)
        self.actions = Gtk.Box(spacing=8, homogeneous=True, halign=Gtk.Align.END)
        self.selected_button = Gtk.Button(label=_("Update Selected"))
        self.all_button = Gtk.Button(label=_("Update All"))
        self.all_button.add_css_class("suggested-action")
        self.selected_button.connect("clicked", lambda _b: self.confirm(self.selected()))
        self.all_button.connect("clicked", lambda _b: self.confirm(self.items))
        for button in (self.selected_button, self.all_button):
            self.actions.append(button)
        self.selection_label = Gtk.Label(
            label=_("No apps selected"), xalign=0, wrap=True, hexpand=True
        )
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
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.footer = Gtk.Box(
            spacing=12,
            margin_start=16,
            margin_end=16,
            margin_top=12,
            margin_bottom=16,
        )
        self.footer.append(self.selection_label)
        self.footer.append(self.actions)
        footer.append(self.footer)
        footer_bin = Adw.BreakpointBin(
            child=footer,
            width_request=280,
            height_request=footer.measure(Gtk.Orientation.VERTICAL, -1)[0],
        )
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 480sp"))
        breakpoint.add_setter(self.actions, "orientation", Gtk.Orientation.VERTICAL)
        footer_bin.add_breakpoint(breakpoint)
        toolbar.add_bottom_bar(footer_bin)
        self.details = ""
        for key in ("update-check-mode", "update-check-interval"):
            window.settings.connect("changed::" + key, self._check_policy_changed)
        for source in UPDATE_PROVIDERS:
            window.settings.connect("changed::update-source-" + source, self._sources_changed)
        self._check_policy_changed()
        if not self.enabled_providers():
            self._sources_changed()
        self._selection_changed()

    def enabled_providers(self):
        return tuple(
            source
            for source in UPDATE_PROVIDERS
            if self.window.settings.get_boolean("update-source-" + source)
        )

    def _check_policy_changed(self, *_args):
        manual = self.window.settings.get_string("update-check-mode") == "manual"
        if manual:
            self.check_when_ready = False
        weekly = self.window.settings.get_string("update-check-interval") == "weekly"
        self.last_checked.set_tooltip_text(
            _("Updates are checked manually.")
            if manual
            else _("Checks refresh on entry after 7 days.")
            if weekly
            else _("Checks refresh on entry after 24 hours.")
        )

    def _sources_changed(self, *_args):
        self.source_revision += 1
        self.cache.clear()
        self.cache_loaded = True
        self.check_when_ready = False
        self.visited = False
        self.checked_at = None
        self.report = None
        self.stale = False
        self.updated_keys.clear()
        self.last_checked.set_visible(False)
        self.details = ""
        self.errors_button.set_visible(False)
        self.render(())
        text = (
            _("Update sources changed. Check again for available updates.")
            if self.enabled_providers()
            else _("Enable an update source in Preferences to check for updates.")
        )
        self.status.set_label(text)
        self.empty.set_title(
            _("Check for Updates") if self.enabled_providers() else _("No Update Sources Enabled")
        )
        self.empty.set_description(text)
        if self.checking:
            self.window.service.cancel()

    def enter(self):
        if (
            self.window.settings.get_string("update-check-mode") == "manual"
            or not self.enabled_providers()
        ):
            return
        ttl = (
            7 if self.window.settings.get_string("update-check-interval") == "weekly" else 1
        ) * 86400
        if not self.visited or (
            self.checked_at is not None and cache_expired(self.checked_at, ttl=ttl)
        ):
            if self.window.service.scanning:
                if not self.check_when_ready:
                    self.window.toast(_("Waiting for the application inventory."))
                self.check_when_ready = True
            else:
                self.check()

    def inventory_ready(self):
        if not self.cache_loaded:
            self.cache_loaded = True
            cached = self.cache.load(self.window.records, providers=self.enabled_providers())
            if cached is not None:
                self.visited = True
                self._show_report(cached.report)
                self._set_checked_at(cached.checked_at)
                if cached.stale:
                    self._show_stale()
        current = {a.key: a for a in self.window.records}
        kept = tuple(item for item in self.items if current.get(item.app.key) == item.app)
        previous = self.inventory_snapshot
        changed_keys = (
            {
                key
                for key in previous.keys() | current.keys()
                if previous.get(key) != current.get(key)
            }
            if previous is not None
            else set()
        )
        self.inventory_snapshot = current
        if kept != self.items:
            self._retain_items(kept)
        if self.visited and changed_keys - self.updated_keys:
            self._show_stale()
        if self.updated_keys:
            # A known successful update changes the inventory, not the check time.
            self.cache.reconcile(
                self.window.records,
                updated_keys=self.updated_keys,
                providers=self.enabled_providers(),
            )
        self.updated_keys.clear()
        if self.check_when_ready:
            self.check_when_ready = False
            if self.window.section == "updates":
                # Re-evaluate after loading the cache: a fresh saved result may
                # satisfy the entry request without contacting a provider.
                self.enter()

    def _show_stale(self):
        self.stale = True
        text = _("Installed applications changed. Refresh to check for remaining updates.")
        self.status.set_label(text)
        self.empty.set_title(_("Check for Updates"))
        self.empty.set_description(text)

    def _set_checked_at(self, timestamp):
        self.checked_at = timestamp
        date = GLib.DateTime.new_from_unix_local(int(timestamp))
        if date is None:
            return
        self.last_checked.set_label(_("Last checked: %s") % date.format("%x %H:%M"))
        self.last_checked.set_visible(True)

    def invalidate(self):
        self.cache.clear()
        self.cache_loaded = True
        self.check_when_ready = False
        self.report = None
        self.updated_keys.clear()
        self.render(())
        self._show_stale()

    def _retain_items(self, items):
        items = tuple(items)
        stale = self.stale
        if self.report is not None:
            self._show_report(replace(self.report, items=items))
        else:
            self.render(items)
        if stale:
            self._show_stale()

    def updates_completed(self, app_keys):
        if not app_keys:
            return
        installations = {
            installation_key(app) for app in self.window.records if app.key in app_keys
        }
        keys = set(app_keys) | {
            app.key for app in self.window.records if installation_key(app) in installations
        }
        self.updated_keys.update(keys)
        self._retain_items(item for item in self.items if item.app.key not in keys)
        self.cache.reconcile(
            self.window.records, completed_keys=keys, providers=self.enabled_providers()
        )

    def selected(self):
        return tuple(item for item, check in self.checks if check.get_active())

    def _selection_changed(self, *_args):
        count = len(self.selected())
        self.selection_label.set_label(_("%d selected") % count if count else _("No apps selected"))
        active = self.window.operation_active
        self.selected_button.set_sensitive(bool(count) and not active)
        self.all_button.set_sensitive(bool(self.items) and not active)
        self.refresh_button.set_sensitive(not active and bool(self.enabled_providers()))
        for _item, check in self.checks:
            check.set_sensitive(not active)

    def check(self):
        window = self.window
        providers = self.enabled_providers()
        if not providers:
            return
        if not window._begin_operation(None, "update"):
            return
        self.checking = True
        self.visited = True
        self._selection_changed()
        window._show_task(_("Checking for Updates"), deferred_cancel=True)
        revision = self.source_revision
        window.service.check_updates(
            window._guard(window._progress),
            window._guard(lambda report: self.checked(report, revision=revision)),
            window._guard(lambda error: self.failed(error, revision=revision)),
            providers=providers,
        )

    def checked(self, report, *, revision=None):
        self.checking = False
        self.window._end_operation()
        if revision is not None and revision != self.source_revision:
            self._selection_changed()
            return
        if report.cancelled:
            # A cancelled check is not a replacement snapshot, even if some
            # providers returned results before cancellation was acknowledged.
            self.window.toast(_("Update check cancelled."))
            return
        self._show_report(report)
        self.inventory_snapshot = {a.key: a for a in self.window.records}
        self.updated_keys.clear()
        if not report.errors:
            checked_at = time.time()
            self._set_checked_at(checked_at)
            self.cache.save(
                report, self.window.records, checked_at, providers=self.enabled_providers()
            )

    def _show_report(self, report):
        self.report = report
        self.stale = False
        self.render(report.items)
        details = list(report.errors)
        if report.errors:
            text = _("Update check incomplete")
            empty_title = _("Could Not Check All Apps")
        elif report.items:
            text = _("%d updates available") % len(report.items)
            empty_title = _("Updates Available")
        else:
            text = _("No updates available")
            empty_title = _("You're Up to Date")
        if report.unsupported:
            details.append(
                _("%d apps require their own updater. Open their details for update instructions.")
                % report.unsupported
            )
        self.details = "\n\n".join(details)
        self.errors_button.set_visible(bool(details))
        self.status.set_label(text)
        self.empty.set_title(empty_title)
        self.empty.set_description(text)

    def failed(self, error, *, revision=None):
        self.checking = False
        self.window._end_operation()
        self._selection_changed()
        if revision is not None and revision != self.source_revision:
            return
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
        self.updates_completed(result.completed_app_keys)
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
