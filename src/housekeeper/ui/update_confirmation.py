"""Compact update confirmation with complete transaction details on demand."""

from gi.repository import GLib, Gtk, Pango

from housekeeper.i18n import _, ngettext
from housekeeper.updates import authorization_notice


class UpdateDetails(Gtk.Box):
    """A centered disclosure control with independently expanding transaction text."""

    def __init__(self, preview):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.preview = preview
        self.toggle = Gtk.ToggleButton(halign=Gtk.Align.CENTER)
        self.toggle.add_css_class("flat")
        title = Gtk.Box(spacing=4)
        self.arrow = Gtk.Image(icon_name="pan-end-symbolic")
        title.append(self.arrow)
        title.append(Gtk.Label(label=_("Details")))
        self.toggle.set_child(title)
        self.revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=150,
            child=Gtk.ScrolledWindow(
                child=preview,
                max_content_height=240,
                propagate_natural_height=True,
                hscrollbar_policy=Gtk.PolicyType.NEVER,
            ),
        )
        self.append(self.toggle)
        self.append(self.revealer)
        self.toggle.connect("toggled", self._toggled)
        self._toggled()

    def _toggled(self, *_args):
        expanded = self.toggle.get_active()
        self.arrow.set_from_icon_name("pan-down-symbolic" if expanded else "pan-end-symbolic")
        self.revealer.set_reveal_child(expanded)
        # EXPANDED is a tristate GTK reads as an int; a Python bool fails its getter.
        self.toggle.update_state([Gtk.AccessibleState.EXPANDED], [1 if expanded else 0])


def version_change(item):
    app, plan = item.app, item.plan
    primary = next(
        (
            change
            for change in plan.changes
            if change.identity == app.identity
            or (app.provider == "rpm" and change.target == plan.target)
        ),
        None,
    )
    if primary is None:
        return _("New version available")
    old, new = primary.current_version, primary.target_version
    if app.provider == "rpm":
        # Keep packaging revisions in Details unless they are the only version change.
        versions = tuple(value.split(":", 1)[-1].rsplit("-", 1)[0] for value in (old, new))
        if versions[0] != versions[1]:
            old, new = versions
    elif app.provider == "flatpak":
        old, new = old[:12], new[:12]
    return f"{old} → {new}"


def configure_update_confirmation(dialog, items):
    single = len(items) == 1
    dialog.set_heading(
        _("Update %s?") % items[0].app.name
        if single
        else ngettext("Update %d app?", "Update %d apps?", len(items)) % len(items)
    )

    def summary(item):
        text = version_change(item)
        aliases = tuple(dict.fromkeys(name for name in item.names if name != item.app.name))
        if aliases:
            text += "\n" + _("Also includes: %s") % ", ".join(aliases)
        return text

    dialog.set_body((summary(items[0]) + "\n" if single else "") + _("Personal data is kept."))
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    if not single:
        apps = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        apps.add_css_class("boxed-list")
        for item in items:
            label = Gtk.Label(
                label=f"{item.app.name}\n{summary(item)}",
                xalign=0,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
                max_width_chars=40,
                margin_start=12,
                margin_end=12,
                margin_top=8,
                margin_bottom=8,
            )
            apps.append(Gtk.ListBoxRow(child=label, activatable=False, selectable=False))
        content.append(
            Gtk.ScrolledWindow(
                child=apps,
                max_content_height=180,
                propagate_natural_height=True,
                hscrollbar_policy=Gtk.PolicyType.NEVER,
            )
        )

    lines = []
    if not single:
        lines.append(_("Updates run in order and stop if an update fails or its plan changes."))
    notice = next((notice for item in items if (notice := authorization_notice(item.app))), "")
    if notice:
        lines.append(notice)
    for item in items:
        app, plan = item.app, item.plan
        lines.append(f"{app.name} · {app.scope}\n{plan.message}")
        installation = app.installation.context if app.installation else plan.installation
        lines.append(_("Installation: %s") % installation)
        lines.append(_("Target: %s") % (app.target.value if app.target else plan.target))
        for change in plan.changes:
            operation = _("Install") if change.operation == "install" else _("Update")
            lines.append(
                f"{operation}: {change.identity}\n"
                f"{change.current_version or _('Not installed')} → {change.target_version}\n"
                f"{change.source}"
            )
        if plan.download_size is not None:
            lines.append(_("Estimated download: %s") % GLib.format_size(plan.download_size))
    preview = Gtk.Label(
        label="\n\n".join(lines),
        wrap=True,
        wrap_mode=Pango.WrapMode.WORD_CHAR,
        max_width_chars=48,
        selectable=True,
        xalign=0,
        margin_top=8,
    )
    content.append(UpdateDetails(preview))
    dialog.set_extra_child(content)
