"""Load application icons without GTK's background SVG preloading path."""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk

FALLBACK_ICON = "application-x-executable"


def icon_image(icon, size):
    image = Gtk.Image(pixel_size=size)
    set_icon(image, icon)
    return image


def set_icon(image, icon):
    if not hasattr(image, "_housekeeper_icon"):
        image._housekeeper_icon_theme = None
        image.connect("realize", _watch_theme)
        image.connect("unrealize", _unwatch_theme)
        image.connect("notify::scale-factor", _reload_icon)
        image.connect("notify::pixel-size", _reload_icon)
        image.connect("direction-changed", _reload_icon)
    image._housekeeper_icon = icon or FALLBACK_ICON
    if image.get_realized():
        _watch_theme(image)
    else:
        _reload_icon(image)


def _watch_theme(image):
    if image._housekeeper_icon_theme is None:
        theme = Gtk.IconTheme.get_for_display(image.get_display())
        handler = theme.connect("changed", lambda _theme: _reload_icon(image))
        image._housekeeper_icon_theme = (theme, handler)
    _reload_icon(image)


def _unwatch_theme(image):
    connection = image._housekeeper_icon_theme
    if connection is not None:
        theme, handler = connection
        theme.disconnect(handler)
        image._housekeeper_icon_theme = None


def _reload_icon(image, *_args):
    icon = image._housekeeper_icon
    size = max(16, image.get_pixel_size())
    scale = image.get_scale_factor()
    if Path(icon).is_absolute() and Path(icon).is_file():
        paintable = Gtk.IconPaintable.new_for_file(Gio.File.new_for_path(icon), size, scale)
    else:
        theme = Gtk.IconTheme.get_for_display(image.get_display())
        name = icon if theme.has_icon(icon) else FALLBACK_ICON
        paintable = theme.lookup_icon(
            name, None, size, scale, image.get_direction(), Gtk.IconLookupFlags(0)
        )
    # Gtk.Image icon-name storage requests PRELOAD. With GTK 4.22.4, SVG text can
    # then race with main-thread font rendering and crash inside Pango/Cairo.
    # A paintable without PRELOAD loads on snapshot on the GTK thread instead.
    image.set_from_paintable(paintable)
