"""Compact update confirmation with complete transaction details on demand."""

from dataclasses import replace

from gi.repository import GLib, Gtk, Pango

from housekeeper.i18n import _, ngettext
from housekeeper.ui.disclosure import DetailsDisclosure, details_label
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
        # Show packaging revisions only when they are the only version change.
        versions = tuple(value.split(":", 1)[-1].rsplit("-", 1)[0] for value in (old, new))
        if versions[0] != versions[1]:
            old, new = versions
    elif app.provider == "flatpak":
        old, new = old[:12], new[:12]
    return f"{old} → {new}"


def listed(paths, limit=10):
    """A bounded list; a transaction touching a core library can be in use everywhere."""
    shown = "\n".join(paths[:limit])
    remaining = len(paths) - limit
    if remaining <= 0:
        return shown
    return shown + "\n" + ngettext("and %d more", "and %d more", remaining) % remaining


def changes_text(changes):
    """Count what the transaction does before naming it; a new install is never implied."""
    installs = tuple(change for change in changes if change.operation == "install")
    heading = (
        ngettext(
            "%(total)d change, %(new)d newly installed:",
            "%(total)d changes, %(new)d newly installed:",
            len(changes),
        )
        % {"total": len(changes), "new": len(installs)}
        if installs
        else ngettext("%d change:", "%d changes:", len(changes)) % len(changes)
    )
    entries = [
        (_("Install %s") if change.operation == "install" else _("Update %s")) % change.identity
        + "\n"
        + f"{change.current_version or _('Not installed')} → {change.target_version}"
        + f" · {change.source}"
        for change in changes
    ]
    return heading + "\n" + listed(entries, limit=20)


def permission_text(items):
    """Name the sandbox access an update adds before it is granted, never only afterwards."""
    granting = tuple(item for item in items if item.plan.permissions)
    if not granting:
        return ""
    lines = [
        _("These updates ask for access their current versions do not have.")
        if len(granting) > 1
        else _("This update asks for access the installed version does not have.")
    ]
    for item in granting:
        lines.append(
            (f"{item.app.name}\n" if len(items) > 1 else "")
            + "\n".join("• " + permission for permission in item.plan.permissions)
        )
    return "\n\n".join(lines)


def running_text(items):
    """State what to do about a program still using these files. Details names them."""
    lines = []
    for item in items:
        plan = item.plan
        prefix = f"{item.app.name}: " if len(items) > 1 else ""
        if plan.running and plan.provider == "flatpak":
            lines.append(prefix + _("Running now. Reopen it to use the new version."))
        elif plan.running:
            lines.append(
                prefix + _("Running now. Quit it before updating, or restart it afterwards.")
            )
        elif plan.in_use:
            lines.append(
                prefix + _("Replaced files are in use. Restart affected programs afterwards.")
            )
    return "\n".join(lines)


def replaced_while_running(items):
    """A Flatpak keeps its own deployment; only a replaced file is a caution."""
    return any(
        item.plan.in_use or (item.plan.running and item.plan.provider != "flatpak")
        for item in items
    )


def notice_card(heading_text, text, *, warning=False):
    content = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=6,
        margin_start=12,
        margin_end=12,
        margin_top=12,
        margin_bottom=12,
    )
    heading = Gtk.Label(label=heading_text, xalign=0, wrap=True)
    heading.add_css_class("heading")
    heading.add_css_class("notice-heading")
    content.append(heading)
    content.append(
        Gtk.Label(
            label=text,
            xalign=0,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            max_width_chars=40,
            selectable=True,
        )
    )
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    card.add_css_class("card")
    card.add_css_class("notice")
    if warning:
        card.add_css_class("notice-warning")
    card.append(content)
    return card


# Quitting a program is the advice the dialog gives, so it must notice when that happens.
RUNNING_POLL_SECONDS = 2


def details_text(items):
    single = len(items) == 1
    lines = []
    if not single:
        lines.append(_("Updates run in order and stop if an update fails or its plan changes."))
    notice = next((notice for item in items if (notice := authorization_notice(item.app))), "")
    if notice:
        lines.append(notice)
    for item in items:
        app, plan = item.app, item.plan
        installation = app.installation.context if app.installation else plan.installation
        target = app.target.value if app.target else plan.target
        lines.append(
            f"{app.name} · {app.scope}\n{plan.message}\n"
            + _("Installation: %s") % installation
            + "\n"
            + _("Target: %s") % target
        )
        if plan.download_size is not None:
            lines.append(_("Estimated download: %s") % GLib.format_size(plan.download_size))
        if plan.changes:
            lines.append(changes_text(plan.changes))
        for heading, paths in (
            (_("Running now:"), plan.running),
            (_("Replaced files in use:"), plan.in_use),
        ):
            if paths:
                lines.append(heading + "\n" + listed(paths))
    return "\n\n".join(lines)


def configure_update_confirmation(dialog, items, check_running=None):
    """Fill the dialog and return a function giving the items as currently shown.

    With `check_running`, programs using each update are re-read while the dialog is
    open, and the returned items carry what the person last saw when they confirm.
    """
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
        if item.plan.permissions:
            text += "\n" + ngettext(
                "Requests %d new sandbox permission",
                "Requests %d new sandbox permissions",
                len(item.plan.permissions),
            ) % len(item.plan.permissions)
        return text

    dialog.set_body((summary(items[0]) + "\n" if single else "") + _("Personal data is kept."))
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    permissions = permission_text(items)
    permission_card = notice_card(_("New Permissions"), permissions, warning=True)
    if permissions:
        content.append(permission_card)
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

    details = details_label(details_text(items))
    content.append(DetailsDisclosure(details))
    dialog.set_extra_child(content)
    shown = {"items": tuple(items), "card": None}

    def show_running(current):
        shown["items"] = current
        text = running_text(current)
        card = shown["card"]
        if card is not None and not text:
            content.remove(card)
            card = None
        elif text and card is None:
            card = notice_card(_("In Use Right Now"), text)
            # Keep the order: new permissions, then programs in use, then the rest.
            content.insert_child_after(
                card, permission_card if permission_card.get_parent() is content else None
            )
        elif text:
            card.get_first_child().get_last_child().set_label(text)
        if card is not None:
            if replaced_while_running(current):
                card.add_css_class("notice-warning")
            else:
                card.remove_css_class("notice-warning")
        shown["card"] = card
        details.set_label(details_text(current))

    show_running(shown["items"])
    if check_running is not None:
        _watch_running(dialog, items, check_running, show_running)
    return lambda: shown["items"]


def _watch_running(dialog, items, check_running, show_running):
    """Poll off the UI thread until the dialog is answered or destroyed."""
    watch = {"source": 0, "pending": False}

    def received(states):
        watch["pending"] = False
        if not watch["source"]:
            return
        show_running(
            tuple(
                replace(item, plan=replace(item.plan, running=running, in_use=in_use))
                for item, (running, in_use) in zip(items, states, strict=True)
            )
        )

    def failed(*_args):
        watch["pending"] = False

    def poll():
        if not watch["pending"]:
            watch["pending"] = True
            check_running(items, received, failed)
        return GLib.SOURCE_CONTINUE

    def stop(*_args):
        if watch["source"]:
            GLib.source_remove(watch["source"])
            watch["source"] = 0

    watch["source"] = GLib.timeout_add_seconds(RUNNING_POLL_SECONDS, poll)
    # A cached plan can be hours old, so check once right away as well.
    poll()
    dialog.connect("response", stop)
    # GTK emits destroy only at dispose; a closed window is unmapped right away.
    dialog.connect("unmap", stop)
    dialog.connect("destroy", stop)
