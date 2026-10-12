"""The sidebar's Activity button: running work with progress, then recent results."""

import time

from gi.repository import Adw, GLib, Gtk, Pango

from housekeeper.activity import elapsed
from housekeeper.i18n import _
from housekeeper.models import Outcome

OUTCOME_ICONS = {
    Outcome.SUCCESS: ("object-select-symbolic", "success"),
    Outcome.PARTIAL: ("dialog-warning-symbolic", "warning"),
    Outcome.FAILED: ("dialog-error-symbolic", "error"),
    Outcome.CANCELLED: ("process-stop-symbolic", "dim-label"),
}


class ProgressBar(Gtk.ProgressBar):
    """Shows a fraction, or pulses by itself while there is none to show."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.tick = 0
        self.pulsed = 0

    def show_fraction(self, fraction):
        if fraction is None:
            # Tick callbacks run only while the bar is on screen.
            if not self.tick:
                self.tick = self.add_tick_callback(self._pulse)
            return
        if self.tick:
            self.remove_tick_callback(self.tick)
            self.tick = 0
        self.set_fraction(fraction)

    def _pulse(self, _bar, clock):
        now = clock.get_frame_time()
        if now - self.pulsed >= 100_000:
            self.pulsed = now
            self.pulse()
        return GLib.SOURCE_CONTINUE


def spinner(**kwargs):
    """libadwaita's spinner, or GTK's where libadwaita predates it (1.6)."""
    if getattr(Adw, "Spinner", None) is not None:
        return Adw.Spinner(**kwargs)
    return Gtk.Spinner(spinning=True, **kwargs)


class StatusSpinner:
    """A spinner in a status page's icon slot while active.

    libadwaita before 1.6 has no spinner paintable, so there it shows above `actions`.
    """

    def __init__(self, page, actions):
        self.page = page
        self.active = False
        if getattr(Adw, "SpinnerPaintable", None) is not None:
            self.paintable = Adw.SpinnerPaintable(widget=page)
            self.widget = None
        else:
            self.paintable = None
            self.widget = spinner(
                width_request=64, height_request=64, halign=Gtk.Align.CENTER, visible=False
            )
            actions.prepend(self.widget)

    def set_active(self, active):
        self.active = active
        if self.paintable is not None:
            self.page.set_paintable(self.paintable if active else None)
        else:
            self.widget.set_visible(active)


def cancel_button():
    button = Gtk.Button(
        icon_name="window-close-symbolic", tooltip_text=_("Cancel"), valign=Gtk.Align.CENTER
    )
    button.add_css_class("flat")
    button.add_css_class("circular")
    return button


def section_title(text):
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("heading")
    return label


class ActivityButton(Gtk.MenuButton):
    """Spins while anything runs; its popover lists what runs and what finished."""

    def __init__(self, log, cancel):
        # Its icon is a child (it becomes a spinner), so it needs flat header styling set.
        super().__init__(tooltip_text=_("Activity"), has_frame=False)
        self.log = log
        self.cancel = cancel
        self.icon = Gtk.Image(icon_name="activity-symbolic")
        # The same spinner as the pages' progress.
        self.spinner = spinner()
        self.set_child(self.icon)
        self.rows = {}
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=6,
            margin_bottom=6,
            margin_start=6,
            margin_end=6,
            width_request=320,
        )
        self.running_title = section_title(_("In Progress"))
        box.append(self.running_title)
        self.running = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.running.add_css_class("boxed-list")
        box.append(self.running)
        self.recent_header = recent_header = Gtk.Box(spacing=6)
        self.recent_title = section_title(_("Recent"))
        self.recent_title.set_hexpand(True)
        recent_header.append(self.recent_title)
        self.clear_button = Gtk.Button(label=_("Clear"))
        self.clear_button.add_css_class("flat")
        self.clear_button.connect("clicked", lambda _button: self.log.clear())
        recent_header.append(self.clear_button)
        box.append(recent_header)
        self.recent = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.recent.add_css_class("boxed-list")
        box.append(self.recent)
        self.empty = Gtk.Label(
            label=_("No recent activity"), margin_top=18, margin_bottom=18, wrap=True
        )
        self.empty.add_css_class("dim-label")
        box.append(self.empty)
        popover = Gtk.Popover(child=box)
        # Elapsed times are worded relative to when the list is opened.
        popover.connect("show", lambda _popover: self._render())
        self.set_popover(popover)
        log.connect(self._changed)
        self._render()

    def _changed(self, _log, structural):
        if structural:
            self._render()
            return
        for activity in self.log.running:
            if activity.id in self.rows:
                self._fill(self.rows[activity.id], activity)

    def _render(self):
        log = self.log
        busy = bool(log.running)
        if busy != (self.get_child() is self.spinner):
            self.set_child(self.spinner if busy else self.icon)
        label = _("Activity, in progress") if busy else _("Activity")
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self.running.remove_all()
        self.rows = {}
        for activity in log.running:
            row = self._running_row(activity)
            self.rows[activity.id] = row
            self.running.append(row)
        self.running_title.set_visible(busy)
        self.running.set_visible(busy)
        self.recent.remove_all()
        now = time.time()
        for activity in log.recent:
            self.recent.append(self._recent_row(activity, now))
        self.recent_header.set_visible(bool(log.recent))
        self.recent_header.set_margin_top(6 if busy else 0)
        self.recent.set_visible(bool(log.recent))
        self.empty.set_visible(not busy and not log.recent)

    def _running_row(self, activity):
        outer = Gtk.Box(spacing=6, margin_top=10, margin_bottom=10, margin_start=12, margin_end=6)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        outer.append(box)
        row = Gtk.ListBoxRow(child=outer, activatable=False)
        row.activity_id = activity.id
        row.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        box.append(row.title)
        row.detail = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        row.detail.add_css_class("dim-label")
        row.detail.add_css_class("caption")
        box.append(row.detail)
        row.bar = ProgressBar()
        box.append(row.bar)
        # The app list refresh is not an operation and cannot be cancelled.
        row.cancel = cancel_button()
        row.cancel.set_visible(activity.kind != "scan")
        row.cancel.connect("clicked", lambda _button: self.cancel())
        outer.append(row.cancel)
        self._fill(row, activity)
        return row

    @staticmethod
    def _fill(row, activity):
        row.title.set_label(activity.title)
        row.detail.set_label(activity.detail)
        row.detail.set_visible(bool(activity.detail))
        row.bar.show_fraction(activity.fraction)
        # Unavailable phases dim the button rather than hide it, so it does not flash.
        row.cancel.set_sensitive(activity.cancellable)

    def _recent_row(self, activity, now):
        parts = [activity.subject, elapsed(activity.finished, now)]
        row = Adw.ActionRow(
            title=GLib.markup_escape_text(activity.title),
            subtitle=GLib.markup_escape_text(" · ".join(p for p in parts if p)),
        )
        row.set_title_lines(2)
        icon, style = OUTCOME_ICONS[activity.outcome]
        image = Gtk.Image(icon_name=icon, valign=Gtk.Align.CENTER)
        image.add_css_class(style)
        row.add_prefix(image)
        if activity.detail:
            row.set_tooltip_text(activity.detail)
        return row


class ActivityFollower:
    """Calls `show` with the running activity `match` picks, or None, while `page` is
    on screen, and catches up when it returns."""

    def __init__(self, log, match, page, show):
        self.log = log
        self.match = match
        self.show = show
        self.listener = None
        page.connect("map", lambda _page: self._listen())
        page.connect("unmap", lambda _page: self._stop())

    def _listen(self):
        if self.listener is None:
            self.listener = self.log.connect(lambda _log, _structural: self.sync())
        self.sync()

    def _stop(self):
        self.log.disconnect(self.listener)
        self.listener = None

    def sync(self):
        self.show(next((a for a in self.log.running if self.match(a)), None))


def cancel_pill(cancel):
    button = Gtk.Button(label=_("Cancel"), halign=Gtk.Align.CENTER)
    button.add_css_class("pill")
    button.connect("clicked", lambda _button: cancel())
    return button


class ActivityProgress(Gtk.Box):
    """A spinner for the running activity a page started; it never blocks the page."""

    def __init__(self, log, cancel, match, page, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, visible=False, **kwargs)
        self.spinner = spinner(width_request=48, height_request=48, halign=Gtk.Align.CENTER)
        self.append(self.spinner)
        # Long messages are cut short instead of widening the page.
        self.label = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=1)
        self.label.add_css_class("dim-label")
        self.append(self.label)
        self.cancel = cancel_pill(cancel)
        self.append(self.cancel)
        self.follower = ActivityFollower(log, match, page, self._show)

    def _show(self, activity):
        if activity is not None:
            self.label.set_label(activity.detail or activity.title)
            self.label.set_tooltip_text(activity.title)
            # Unavailable phases dim Cancel rather than hide it, so it does not flash.
            self.cancel.set_sensitive(activity.cancellable)
            self.update_property([Gtk.AccessibleProperty.LABEL], [activity.title])
        if self.get_visible() != (activity is not None):
            self.set_visible(activity is not None)
