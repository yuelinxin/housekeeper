"""Application icon information and per-launcher appearance controls."""

from pathlib import Path

from gi.repository import Adw, Gtk

from housekeeper.app_icons import has_app_icon
from housekeeper.appearance import can_reset_icon, launcher_theme
from housekeeper.i18n import _
from housekeeper.ui.icons import FALLBACK_ICON, icon_image

ICON_REFRESH_NOTICE = _(
    "Custom icons apply to all launchers for this app and to its unique desktop icon names, "
    "including those used by docks and task panels. Housekeeper adds an icon theme layer "
    "that inherits your current theme. Shared icon names are left unchanged. "
    "If a running window still shows the old icon, close and reopen the app. "
    "Windows must be associated with the correct app; some extensions keep their own icons."
)


class AppearanceGroup(Adw.PreferencesGroup):
    def __init__(self, window, app):
        super().__init__(
            title=_("Appearance"),
            description=_(
                "Use the same icon in the app grid, dock and task panels. "
                "Other icons keep your current theme."
            ),
        )
        self.window = window
        self.app = app
        self.connections = []
        self.buttons = []
        self.icon_theme = Adw.ActionRow(title=_("Icon Theme"), use_markup=False)
        self.icon_theme.set_subtitle_lines(0)
        self.add(self.icon_theme)
        # The details page puts this row inside Technical Details.
        self.icon_file = Adw.ActionRow(title=_("Displayed Icon File"), use_markup=False)
        self.icon_file.set_subtitle_lines(3)
        for entry in app.entries:
            theme = launcher_theme(entry)
            if theme:
                row = Adw.ActionRow(
                    title=_("Launcher GTK Theme Override"), subtitle=theme, use_markup=False
                )
                row.set_subtitle_lines(0)
                self.add(row)
        if app.entries:
            entry = app.entries[0]
            row = Adw.ActionRow(
                title=_("Application Icon"),
                subtitle=_("App grid, dock and task panels"),
                use_markup=False,
            )
            row.set_subtitle_lines(0)
            row.add_prefix(icon_image(entry.icon, 32))
            change = Gtk.Button(
                icon_name="document-edit-symbolic",
                tooltip_text=_("Change Icon"),
                valign=Gtk.Align.CENTER,
            )
            change.add_css_class("flat")
            change.connect("clicked", lambda _b, e=entry: window.choose_icon(app, e))
            row.add_suffix(change)
            reset = Gtk.Button(
                icon_name="edit-undo-symbolic",
                tooltip_text=_("Restore Original Icon"),
                valign=Gtk.Align.CENTER,
            )
            reset.add_css_class("flat")
            reset.connect("clicked", lambda _b, e=entry: window.change_icon(app, e, None))
            row.add_suffix(reset)
            resettable = any(can_reset_icon(e) for e in app.entries) or has_app_icon(app)
            self.buttons.extend([(change, True), (reset, resettable)])
            self.add(row)
        if not app.entries:
            self.add(
                Adw.ActionRow(
                    title=_("Launcher Icon"),
                    subtitle=_("No desktop launcher is available to edit."),
                )
            )
        self.set_actions_sensitive(not window.operation_active)
        self.connect("map", self._watch)
        self.connect("unmap", self._unwatch)

    def set_actions_sensitive(self, sensitive):
        for button, available in self.buttons:
            button.set_sensitive(sensitive and available)

    def _watch(self, *_args):
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        self.connections.append((theme, theme.connect("changed", self._refresh)))
        self._refresh()

    def _unwatch(self, *_args):
        for obj, handler in self.connections:
            obj.disconnect(handler)
        self.connections.clear()

    def _refresh(self, *_args):
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        icon = self.app.icon
        if Path(icon).is_absolute() and Path(icon).is_file():
            value = icon
            self.icon_theme.set_title(_("Icon Source"))
            custom = any(entry.icon == icon and can_reset_icon(entry) for entry in self.app.entries)
            self.icon_theme.set_subtitle(_("Custom Icon") if custom else _("Image File"))
        else:
            self.icon_theme.set_title(_("Icon Theme"))
            self.icon_theme.set_subtitle(theme.get_theme_name() or _("Unknown"))
            found = theme.has_icon(icon)
            paintable = theme.lookup_icon(
                icon if found else FALLBACK_ICON,
                None,
                96,
                self.get_scale_factor(),
                self.get_direction(),
                Gtk.IconLookupFlags(0),
            )
            file = paintable.get_file()
            value = (file.get_path() or file.get_uri()) if file else _("Built-in icon")
            if not found:
                value = _("Fallback icon: %s") % value
        self.icon_file.set_subtitle(value)
        self.icon_file.set_tooltip_text(value)
