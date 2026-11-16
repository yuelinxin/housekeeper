# Bundled UI icons

Housekeeper's sidebar, controls and status indicators use private
`housekeeper-…-symbolic` names. The GResource aliases these names to the SVG files
in this directory, so the desktop theme cannot replace them with missing or
different artwork. Symbolic recoloring still follows light/dark mode and widget
state. Directional variants are included for right-to-left layouts.

This does not change the desktop's icon theme or the icons of inventoried apps.
App artwork, including user overrides, continues to resolve through the system
theme using its original name or file path.

## Adwaita artwork

`adwaita/*.svg` is an unmodified subset of
[Adwaita Icon Theme 51.0](https://gitlab.gnome.org/GNOME/adwaita-icon-theme/-/tree/51.0/Adwaita/symbolic),
copyright the [GNOME Project](https://www.gnome.org/). Original filenames are
preserved; only the exported GResource aliases have a Housekeeper prefix.

The upstream license offers GNU LGPL version 3 or CC-BY-SA 3.0. Housekeeper
distributes this subset under LGPL-3.0-only. The upstream notice and complete
LGPL/GPL texts are included as `adwaita/COPYING`, `adwaita/COPYING_LGPL` and
`adwaita/COPYING_GPL`, and installed with the application under `icon-licenses`.
The original editable SVGs are included in the source distribution.

## Additional artwork

`scalable/actions/flatpak-symbolic.svg` comes from GNOME's
[Icon Development Kit](https://gitlab.gnome.org/Teams/Design/icon-development-kit),
distributed through Icon Library. It is licensed under
[CC0-1.0](https://gitlab.gnome.org/Teams/Design/icon-development-kit/-/blob/main/COPYING.md).

The SVGs are bundled in Housekeeper's GResource. GtkApplication discovers them
under the application's resource base path automatically; a global icon-theme
override is neither needed nor applied.
