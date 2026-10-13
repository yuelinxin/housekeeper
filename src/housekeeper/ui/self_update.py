"""Housekeeper's own update, offered in the sidebar apart from other apps' updates."""

from gi.repository import Adw, Gtk, Pango

from housekeeper import APP_ID
from housekeeper.i18n import _
from housekeeper.models import Outcome
from housekeeper.self_update import updated_self
from housekeeper.ui.update_confirmation import configure_update_confirmation, version_change

UPDATE = "self-update"


class SelfUpdateCard(Gtk.Revealer):
    """Revealed while an update to Housekeeper itself is available."""

    def __init__(self, window):
        # The pages list's bottom margin, kept here so it stays while this is hidden and
        # the divider sits as far below the pages as the row below it.
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_UP, margin_bottom=8)
        self.window = window
        self.item = None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(box)
        # Sets Housekeeper's own update apart from the pages above it, as wide as their
        # highlight: the list's 8px margin plus the row's 6px.
        box.append(Gtk.Separator(margin_start=14, margin_end=14, margin_top=6))
        # A row like the pages above, so it highlights the same way; activating it asks
        # to update and restart.
        self.list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            margin_start=8,
            margin_end=8,
            margin_top=6,
        )
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", lambda _list, _row: self.confirm())
        box.append(self.list)
        header = Gtk.Box(spacing=8, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        header.append(Gtk.Image(icon_name=APP_ID, pixel_size=24, valign=Gtk.Align.CENTER))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = Gtk.Label(label=_("Update Available"), xalign=0, wrap=True)
        title.add_css_class("heading")
        text.append(title)
        self.subtitle = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.subtitle.add_css_class("dim-label")
        self.subtitle.add_css_class("caption")
        text.append(self.subtitle)
        header.append(text)
        self.row = Gtk.ListBoxRow(child=header, tooltip_text=_("Update and Restart"))
        self.row.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Update and Restart Housekeeper")]
        )
        self.list.append(self.row)
        self._show()

    def _show(self):
        if self.item is not None:
            self.subtitle.set_label(version_change(self.item))
            self.subtitle.set_tooltip_text(self.subtitle.get_label())
        self.sync_sensitive()
        self.set_reveal_child(self.item is not None)
        self.window._refresh_update_badge()

    def sync_sensitive(self):
        # Not activatable while busy; progress shows in Activity, not here.
        available = self.item is not None and not self.window.operation_active
        self.row.set_activatable(available)
        self.row.set_tooltip_text(_("Update and Restart") if available else None)

    def set_item(self, item):
        self.item = item
        self._show()

    def show_report(self, report):
        """Follow an Updates-page check that also looked for Housekeeper's update."""
        if report.self_checked:
            self.set_item(report.self_update)

    def inventory_ready(self):
        # An open confirmation still needs its item; execution revalidates it anyway.
        if self.item is None or self.window.operation_active:
            return
        current = next((a for a in self.window.records if a.key == self.item.app.key), None)
        if current != self.item.app:
            # Updated or changed elsewhere; the next check finds out what remains.
            self.set_item(None)

    def confirm(self):
        window = self.window
        item = self.item
        if item is None or not window._begin_operation(None, UPDATE, subject="Housekeeper"):
            return
        self.sync_sensitive()
        dialog = Adw.MessageDialog(transient_for=window, modal=True)
        window.confirm_dialog = dialog
        shown = configure_update_confirmation(dialog, (item,), window.service.check_running)
        dialog.set_heading(_("Update Housekeeper?"))
        dialog.set_body(
            dialog.get_body() + "\n" + _("Housekeeper restarts when the update finishes.")
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("update", _("Update and Restart"))
        dialog.set_response_appearance("update", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(_dialog, choice):
            window.confirm_dialog = None
            if choice != "update":
                window._end_operation()
                return
            window._start_task(_("Updating Housekeeper"))
            # Confirm the running programs the dialog showed last.
            confirmed = shown()[0]
            window.service.execute_self_update(
                confirmed,
                window._guard(window._progress),
                window._guard(lambda result: self._finished(confirmed, result)),
            )

        dialog.connect("response", window._guard(response))
        dialog.present()

    def _finished(self, item, result):
        window = self.window
        titles = {
            Outcome.SUCCESS: _("Housekeeper Updated"),
            Outcome.PARTIAL: _("Update Partially Completed"),
            Outcome.CANCELLED: _("Update Cancelled"),
            Outcome.FAILED: _("Update Failed"),
        }
        window._end_operation(result.outcome, titles[result.outcome], result.message)
        body = "\n\n".join(part for part in (result.message, *result.errors) if part)
        if updated_self(item, result):
            # The running code no longer matches the installed files; nothing more may
            # load from them, so restart now instead of refreshing.
            self.item = None
            if result.outcome == Outcome.SUCCESS:
                window.restart()
                return
            dialog = Adw.MessageDialog(
                transient_for=window,
                heading=titles[result.outcome],
                body=body + "\n\n" + _("Housekeeper was updated and restarts now."),
            )
            dialog.add_response("restart", _("Restart Housekeeper"))
            dialog.set_close_response("restart")
            dialog.connect("response", lambda *_args: window.restart())
            dialog.present()
            return
        window.announce(result.outcome, titles[result.outcome], body, "Housekeeper")
        window.refresh()
