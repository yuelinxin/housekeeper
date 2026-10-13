"""Updates navigation page; checks are initiated only by explicit user interaction."""

import time
from dataclasses import replace

from gi.repository import Adw, GLib, GObject, Gtk

from housekeeper.batch_updates import UPDATE_PROVIDERS, installation_key
from housekeeper.i18n import _, ngettext
from housekeeper.models import OperationCancelled, Outcome
from housekeeper.ui.activity import ActivityFollower, ProgressBar, StatusSpinner, cancel_pill
from housekeeper.ui.icons import icon_image
from housekeeper.ui.update_confirmation import configure_update_confirmation, version_change
from housekeeper.update_cache import UpdateCache, cache_expired


def report_summary(report):
    if report.errors:
        return _("Update check incomplete")
    if report.items:
        count = len(report.items)
        return ngettext("%d update available", "%d updates available", count) % count
    return _("No updates available")


class UpdatesPage(Adw.NavigationPage):
    def __init__(self, window):
        super().__init__(title=_("Updates"), tag="updates")
        self.window = window
        self.items = ()
        self.checks = []
        self.bars = {}
        # An update batch shows on its rows instead of covering the list. `batch` maps
        # each confirmed item still to be updated to its fraction, None while unknown.
        self.executing = False
        self.batch = {}
        self.batch_current = None
        self.batch_total = 0
        self.visited = False
        self.check_when_ready = False
        self.inventory_pending = False
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
        toolbar.add_top_bar(header)
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_start=12,
            margin_end=12,
            margin_top=12,
            margin_bottom=12,
        )
        toolbar.set_content(content)
        # The summary above the list; without updates the status page says it all.
        self.summary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.append(self.summary)
        title = Gtk.Box(spacing=12)
        self.summary.append(title)
        self.status = Gtk.Label(label=_("Check for Updates"), xalign=0, wrap=True, hexpand=True)
        self.status.add_css_class("title-2")
        title.append(self.status)
        self.refresh_button = Gtk.Button(label=_("Check Again"), valign=Gtk.Align.CENTER)
        self.refresh_button.connect("clicked", lambda _b: self.check())
        title.append(self.refresh_button)
        metadata = Gtk.Box(spacing=12, halign=Gtk.Align.START)
        self.summary.append(metadata)
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
        self.cancel_button = Gtk.Button(label=_("Cancel"), visible=False)
        self.cancel_button.connect("clicked", lambda _b: window.cancel_operation())
        for button in (self.selected_button, self.all_button, self.cancel_button):
            self.actions.append(button)
        self.selection_label = Gtk.Label(
            label=_("No apps selected"), xalign=0, wrap=True, hexpand=True
        )
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        self.list.set_valign(Gtk.Align.START)
        self.empty = Adw.StatusPage(icon_name="software-update-available-symbolic")
        self.empty.add_css_class("page-status")
        empty_actions = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER
        )
        self.check_button = Gtk.Button(label=_("Check for Updates"))
        self.check_button.add_css_class("pill")
        self.check_button.add_css_class("suggested-action")
        self.check_button.connect("clicked", lambda _b: self.check())
        empty_actions.append(self.check_button)
        self.empty_details = Gtk.Button(label=_("Details"), visible=False)
        self.empty_details.add_css_class("flat")
        self.empty_details.connect(
            "clicked", lambda _b: window.message(_("Update Check Details"), self.details)
        )
        empty_actions.append(self.empty_details)
        self.empty.set_child(empty_actions)
        # What the status page explains in place of the last check time, if anything.
        self.notice = ""
        self._set_empty(
            _("Check for Updates"), _("Available desktop app updates will appear here.")
        )
        # Checking for or installing updates from this page puts a spinner in place of
        # the status icon until it finishes.
        self.working_page = Adw.StatusPage()
        self.working_page.add_css_class("page-status")
        working_actions = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER
        )
        self.working_spinner = StatusSpinner(self.working_page, working_actions)
        self.working_spinner.set_active(True)
        self.working_cancel = cancel_pill(window.cancel_operation)
        working_actions.append(self.working_cancel)
        self.working_page.set_child(working_actions)
        self.working = False
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self.empty, "empty")
        self.stack.add_named(self.working_page, "working")
        self.stack.add_named(
            Gtk.ScrolledWindow(child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER), "list"
        )
        content.append(self.stack)
        self.follower = ActivityFollower(
            window.activities, lambda activity: activity.key == "update", self, self._working
        )
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.footer = Gtk.Box(
            spacing=12,
            margin_start=12,
            margin_end=12,
            margin_top=12,
            margin_bottom=12,
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
        self.footer_bin = footer_bin
        self.details = ""
        for key in ("update-check-mode", "update-check-interval"):
            window.settings.connect("changed::" + key, self._check_policy_changed)
        for source in UPDATE_PROVIDERS:
            window.settings.connect("changed::update-source-" + source, self._sources_changed)
        self._check_policy_changed()
        if not self.enabled_providers():
            self._sources_changed()
        self._selection_changed()
        self._show_stack()

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
        self.empty_details.set_visible(False)
        self.render(())
        text = (
            _("Update sources changed. Check again for available updates.")
            if self.enabled_providers()
            else _(
                "Enable an update source in Preferences → Software Sources to check for updates."
            )
        )
        self.status.set_label(text)
        self._set_empty(
            _("Check for Updates") if self.enabled_providers() else _("No Update Sources Enabled"),
            text,
        )
        if self.checking:
            self.window.service.cancel()

    def enter(self):
        if (
            self.checking
            or self.window.settings.get_string("update-check-mode") == "manual"
            or not self.enabled_providers()
        ):
            self.check_when_ready = False
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
                return
            # check() clears the request once it owns the operation; leaving it set when
            # another operation holds the service keeps the entry from being dropped.
            self.check()
            return
        self.check_when_ready = False

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
        if self.window.operation_active:
            # Pruning rows or calling the list stale while an operation is still running
            # would contradict it; _end_operation replays this against its result.
            self.inventory_pending = True
            return
        self.inventory_pending = False
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
            if self.window.section == "updates":
                # Re-evaluate after loading the cache: a fresh saved result may
                # satisfy the entry request without contacting a provider.
                self.enter()
            else:
                self.check_when_ready = False

    def _show_stale(self):
        self.stale = True
        text = _("Installed applications changed. Check again for remaining updates.")
        self.status.set_label(text)
        self._set_empty(_("Check for Updates"), text)

    def _set_empty(self, title, notice=""):
        self.empty.set_title(title)
        self.notice = notice
        self._describe_empty()

    def _describe_empty(self):
        checked = self.last_checked.get_visible() and self.last_checked.get_label()
        self.empty.set_description(GLib.markup_escape_text(self.notice or checked or ""))

    def _set_checked_at(self, timestamp):
        self.checked_at = timestamp
        date = GLib.DateTime.new_from_unix_local(int(timestamp))
        if date is None:
            return
        self.last_checked.set_label(_("Last checked: %s") % date.format("%x %H:%M"))
        self.last_checked.set_visible(True)
        self._describe_empty()

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

    @staticmethod
    def _row_toggled(check, row):
        if check.get_active():
            row.add_css_class("update-selected")
        else:
            row.remove_css_class("update-selected")

    def _selection_changed(self, *_args):
        count = len(self.selected())
        updating = bool(self.batch)
        if updating:
            # The first item still in the batch is the one being updated.
            index = self.batch_total - len(self.batch) + 1
            self.selection_label.set_label(
                _("Updating %(index)d of %(total)d") % {"index": index, "total": self.batch_total}
            )
        else:
            self.selection_label.set_label(
                _("%d selected") % count if count else _("No apps selected")
            )
        self.selected_button.set_visible(not updating)
        self.all_button.set_visible(not updating)
        self.cancel_button.set_visible(updating)
        active = self.window.operation_active
        self.selected_button.set_sensitive(bool(count) and not active)
        self.all_button.set_sensitive(bool(self.items) and not active)
        can_check = not active and bool(self.enabled_providers())
        self.refresh_button.set_sensitive(can_check)
        self.check_button.set_sensitive(can_check)
        for _item, check in self.checks:
            check.set_sensitive(not active)

    def check(self):
        window = self.window
        if self.checking:
            return
        providers = self.enabled_providers()
        if not providers:
            return
        if not window._begin_operation(None, "update"):
            return
        self.check_when_ready = False
        self.checking = True
        self.visited = True
        self._selection_changed()
        window._start_task(_("Checking for Updates"), deferred_cancel=True)
        revision = self.source_revision
        window.service.check_updates(
            window._guard(window._progress),
            window._guard(lambda report: self.checked(report, revision=revision)),
            window._guard(lambda error: self.failed(error, revision=revision)),
            providers=providers,
        )

    def checked(self, report, *, revision=None):
        self.checking = False
        if revision is not None and revision != self.source_revision:
            self.window._end_operation()
            self._selection_changed()
            return
        if report.cancelled:
            # A cancelled check is not a replacement snapshot, even if some
            # providers returned results before cancellation was acknowledged.
            self.window._end_operation(Outcome.CANCELLED, _("Update Check Cancelled"))
            self.window.toast(_("Update check cancelled."))
            return
        self.window._end_operation(
            Outcome.PARTIAL if report.errors else Outcome.SUCCESS,
            report_summary(report),
            "\n".join(report.errors),
        )
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
        text = report_summary(report)
        if report.errors:
            empty_title = _("Could Not Check All Apps")
        elif report.items:
            empty_title = _("Updates Available")
        else:
            empty_title = _("You're Up to Date")
        if report.unsupported:
            details.append(
                ngettext(
                    "%d app requires its own updater. Open its details for update instructions.",
                    "%d apps require their own updater. Open their details for update instructions.",
                    report.unsupported,
                )
                % report.unsupported
            )
        if report.self_note:
            details.append(_("Updating Housekeeper") + "\n" + report.self_note)
        self.details = "\n\n".join(details)
        self.errors_button.set_visible(bool(details))
        self.empty_details.set_visible(bool(details))
        self.status.set_label(text)
        self._set_empty(empty_title)
        self.window.self_update.show_report(report)

    def failed(self, error, *, revision=None):
        self.checking = False
        if revision is not None and revision != self.source_revision:
            self.window._end_operation()
            self._selection_changed()
            return
        if isinstance(error, OperationCancelled):
            self.window._end_operation(Outcome.CANCELLED, _("Update Check Cancelled"))
            self._selection_changed()
            # Withdrawn before a provider was contacted; previous results still stand.
            self.window.toast(_("Update check cancelled."))
            return
        self.window._end_operation(Outcome.FAILED, _("Could Not Check Updates"), str(error))
        self._selection_changed()
        self.status.set_label(_("The update check failed. Previous results may be out of date."))
        self.window.message(_("Could Not Check Updates"), str(error))

    def render(self, items):
        selected = {item.app.key for item in self.selected()}
        self.items = tuple(items)
        self.checks = []
        self.bars = {}
        while child := self.list.get_first_child():
            self.list.remove(child)
        for item in items:
            app = item.app
            row = Gtk.ListBoxRow(activatable=False)
            # Padded rather than inset, so the whole row toggles it.
            check = Gtk.CheckButton(hexpand=True)
            check.add_css_class("update-check")
            # Leave the checkbox as much room as the icon leaves its text.
            box = Gtk.Box(spacing=12, margin_start=8)
            box.append(icon_image(app.icon, 40))
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
            name = Gtk.Label(label=", ".join(item.names), xalign=0, wrap=True)
            name.add_css_class("heading")
            text.append(name)
            subtitle = Gtk.Label(
                label=f"{app.source.value.upper()} · {app.scope}\n{version_change(item)}",
                xalign=0,
                wrap=True,
            )
            subtitle.add_css_class("dim-label")
            text.append(subtitle)
            box.append(text)
            check.set_child(box)
            check.update_property([Gtk.AccessibleProperty.LABEL], [", ".join(item.names)])
            check.connect("toggled", self._row_toggled, row)
            check.set_active(app.key in selected)
            check.connect("toggled", self._selection_changed)
            content = Gtk.Box()
            content.append(check)
            # Outside the checkbox, so it is not dimmed with the checkbox while updating.
            bar = ProgressBar(valign=Gtk.Align.CENTER, margin_end=12, visible=False)
            bar.add_css_class("update-progress")
            bar.update_property(
                [Gtk.AccessibleProperty.LABEL], [_("Updating %s") % ", ".join(item.names)]
            )
            content.append(bar)
            row.set_child(content)
            self.list.append(row)
            self.checks.append((item, check))
            self.bars[app.key] = bar
        self._show_batch()
        self._show_stack()
        self.window.updates_count.set_label(str(len(items)) if items else "")
        self.window._refresh_update_badge()
        self._selection_changed()

    def _show_batch(self):
        for key, bar in self.bars.items():
            bar.set_visible(key in self.batch)
            if key not in self.batch:
                continue
            # Items waiting their turn stay empty and faint until it comes.
            current = key == self.batch_current
            bar.set_opacity(1.0 if current else 0.5)
            bar.show_fraction(self.batch[key] if current else 0.0)

    def _item_progress(self, key, fraction):
        if key not in self.batch:
            return
        self.batch_current = key
        self.batch[key] = fraction
        self._show_batch()
        self._selection_changed()

    def _item_completed(self, keys):
        for key in keys:
            self.batch.pop(key, None)
        self.updates_completed(keys)

    def _working(self, activity):
        if activity is not None and self.executing:
            # An update batch shows on its rows; only a check takes over the page.
            self.cancel_button.set_sensitive(activity.cancellable)
            activity = None
        self.working = activity is not None
        if activity is not None:
            self.working_page.set_title(GLib.markup_escape_text(activity.title))
            self.working_page.set_description(GLib.markup_escape_text(activity.detail))
            self.working_cancel.set_sensitive(activity.cancellable)
        self._show_stack()

    def _show_stack(self):
        name = "working" if self.working else ("list" if self.items else "empty")
        if self.stack.get_visible_child_name() != name:
            self.stack.set_visible_child_name(name)
        # The summary and the update buttons only concern a list of updates.
        self.summary.set_visible(name == "list")
        self.footer_bin.set_visible(name == "list")

    def confirm(self, items):
        items = tuple(items)
        subject = ngettext("%d app", "%d apps", len(items)) % len(items)
        if not items:
            return
        # Set first: starting the operation is what would show the check's spinner.
        self.executing = True
        if not self.window._begin_operation(None, "update", subject=subject):
            self.executing = False
            return
        self._selection_changed()
        dialog = Adw.MessageDialog(
            transient_for=self.window,
            modal=True,
        )
        self.window.confirm_dialog = dialog
        shown = configure_update_confirmation(dialog, items, self.window.service.check_running)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("update", _("Update"))
        dialog.set_response_appearance("update", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(_dialog, choice):
            self.window.confirm_dialog = None
            if choice != "update":
                self.executing = False
                self.window._end_operation()
                self._selection_changed()
                return
            # Confirm the running programs the dialog showed last, not those at preview time.
            confirmed = shown()
            self.batch = {item.app.key: None for item in confirmed}
            self.batch_current = None
            self.batch_total = len(confirmed)
            self._show_batch()
            self._selection_changed()
            self.window._start_task(_("Updating Applications"))
            guard = self.window._guard
            self.window.service.execute_updates(
                confirmed,
                guard(self.window._progress),
                guard(self.finished),
                item_progress=guard(self._item_progress),
                item_completed=guard(self._item_completed),
            )

        dialog.connect("response", self.window._guard(response))
        dialog.present()

    def finished(self, result):
        self.executing = False
        self.batch = {}
        self.batch_current = None
        self._show_batch()
        # Record the completed updates before releasing the page, so a scan that finished
        # mid-batch is reconciled against them instead of marking the list stale.
        self.updates_completed(result.completed_app_keys)
        titles = {
            Outcome.SUCCESS: _("Updates Complete"),
            Outcome.PARTIAL: _("Updates Partially Complete"),
            Outcome.CANCELLED: _("Updates Cancelled"),
            Outcome.FAILED: _("Updates Failed"),
        }
        self.window._end_operation(result.outcome, titles[result.outcome], result.message)
        text = result.message
        if result.completed:
            text += "\n\n" + _("Completed:") + "\n" + "\n".join(result.completed)
        if result.errors:
            text += "\n\n" + "\n".join(result.errors)
        if result.restart_hint:
            text += "\n\n" + result.restart_hint
        self.window.announce(result.outcome, titles[result.outcome], text)
        self.window.refresh()
