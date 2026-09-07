# Intermittent SVG startup crash

On September 7, 2026, an intermittent startup segmentation fault was investigated
on Fedora 44 with GTK 4.22.4, Pango 1.57.1, and Cairo 1.18.4. The existing core dump
identified a GTK icon-loading worker, rather than a Python exception:

```text
load_icon_thread
  icon_ensure_node__locked
    gtk_svg_snapshot_with_weight
      gtk_snapshot_add_layout
        gsk_reload_font
          pango_fc_font_key_copy
            cairo_font_options_copy
              _cairo_font_options_init_copy
                strdup / strlen
```

The main thread was concurrently rendering text. A standalone reproducer using
180 generated SVG icons containing text triggered a heap-corruption abort on its
second fresh startup, with the same GTK SVG/font worker path. The reproducer does
not run inventory discovery or package-management providers.

## Application mitigation

Housekeeper supplies `Gtk.IconPaintable` objects directly to application-icon
images. Named icon lookup uses zero flags, so it does not request `PRELOAD`.
GTK then loads the paintable when the main thread snapshots it. File icons use
`Gtk.IconPaintable.new_for_file()` through the same display path. No extra image
library, renderer override, persistent icon cache, or dependency version increase
is required.

The distinction matters because setting an icon name directly on `Gtk.Image`
allows its internal helper to request background preloading. This behavior is
visible in the [GTK 4.22.4 icon helper source](https://github.com/GNOME/gtk/blob/4.22.4/gtk/gtkiconhelper.c).
The [lookup flags documentation](https://docs.gtk.org/gtk4/flags.IconLookupFlags.html)
describes `PRELOAD`; [icon lookup](https://docs.gtk.org/gtk4/method.IconTheme.lookup_icon.html)
and [file paintables](https://docs.gtk.org/gtk4/ctor.IconPaintable.new_for_file.html)
provide the public API used by the mitigation. Use `Gtk.IconLookupFlags(0)` to retain
compatibility with GTK versions predating the named `NONE` enum member.

Icon names are checked before fallback lookup so a generic icon in the current
theme does not take precedence over an application's icon in an inherited theme.
Images re-resolve on theme, size, direction, and scale changes. Theme listeners are
attached while widgets are realized and disconnected when they are unrealized.
The views remain virtualized, and GTK retains its normal icon lookup cache.

## Regression coverage

`tests/stress_icons.py` uses only temporary generated fixtures. It verifies the
actual themed icon identity as well as process survival, avoiding a misleading
pass caused by displaying generic fallback icons. Its lifecycle checks cover
file icons, theme notifications, missing icons, resizing, and listener cleanup.
Fresh processes are essential because a warm in-process icon cache can conceal
startup failures.

This is an application-level mitigation for the observed native crash path, not a
claim that every upstream GTK/font-rendering defect has been fixed. Retain the
regression test when changing icon loading. For a different future crash, capture
the corresponding `coredumpctl` stack before changing unrelated code or disabling
graphics acceleration.
