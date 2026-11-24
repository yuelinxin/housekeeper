# Changelog

## 0.4.0 — 2026-11-24

- Add native packages for Ubuntu 24.04, Debian 13 and current Arch Linux, attached
  to each GitHub release with the generated PKGBUILD. DEB and Pacman apps show their
  package, version, size and installation date; native updates, removal and Pacman
  installation stay in the system package manager. These packages are a preview:
  they are tested in containers, and real desktop sessions are only partly checked.
- Bundle the interface's symbolic icons so the sidebar, toolbar and status icons are
  present and consistent in KDE Plasma, Hyprland and other desktops whose icon
  theme lacks them.
- Change the GNOME icon theme only in GNOME sessions. Custom icons set or restored in
  another desktop change its launchers and are applied to GNOME's docks and panels the
  next time Housekeeper starts there, so a restored icon no longer stays in GNOME.
- Skip desktop files for links and services, such as one KDE installs, instead of
  reporting them as unreadable applications.
- Refuse Pacman installation through PackageKit, which also upgrades unrelated system
  packages; the details page explains the pacman command and still offers Flatpak.
- Count fileless Arch metapackages such as `base` as valid package records.
- Offer to open only the system's own package type from the file manager: RPM on
  Fedora, DEB on Ubuntu and Debian, and neither on Arch.

## 0.3.6 — 2026-11-10

- Support Fedora 45. Checking for updates, and updating, installing and removing
  system packages, were turned off there because RPM management only allowed Fedora
  43 and 44.
- Notice software source changes between an update preview and the update on Fedora
  45, which keeps its repositories in /usr/share/dnf5/repos.d, and in dnf5 repository
  overrides and configuration drop-ins.
