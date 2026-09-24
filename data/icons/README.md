# Bundled UI icons

`scalable/actions/flatpak-symbolic.svg` comes from GNOME's
[Icon Development Kit](https://gitlab.gnome.org/Teams/Design/icon-development-kit),
distributed through Icon Library. It is licensed under
[CC0-1.0](https://gitlab.gnome.org/Teams/Design/icon-development-kit/-/blob/main/COPYING.md).

The SVG is bundled in Housekeeper's GResource so it is available independently of
the installed icon theme. GtkApplication discovers it under the application's
resource base path automatically.
