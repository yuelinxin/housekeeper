"""Compact update confirmation with complete transaction details on demand."""

from gi.repository import GLib, Gtk, Pango

from housekeeper.i18n import _
from housekeeper.updates import authorization_notice


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
        _("Update %s?") % items[0].app.name if single else _("Update %d apps?") % len(items)
    )
    dialog.set_body(
        (version_change(items[0]) + "\n" if single else "") + _("Personal data is kept.")
    )
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    if not single:
        apps = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        apps.add_css_class("boxed-list")
        for item in items:
            label = Gtk.Label(
                label=f"{item.app.name}\n{version_change(item)}",
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
        aliases = tuple(name for name in item.names if name != app.name)
        if aliases:
            lines.append(_("Also includes: %s") % ", ".join(aliases))
        if app.installation:
            lines.append(_("Installation: %s") % app.installation.context)
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
    details = Gtk.Expander(label=_("Details"), expanded=False)
    # Center the arrow and label together without narrowing the expanded content.
    details.get_label_widget().get_parent().set_halign(Gtk.Align.CENTER)
    details.set_child(
        Gtk.ScrolledWindow(
            child=preview,
            max_content_height=240,
            propagate_natural_height=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
    )
    content.append(details)
    dialog.set_extra_child(content)
