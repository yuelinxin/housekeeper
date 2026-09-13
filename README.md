# Housekeeper

A simple, unified app manager for the GNOME desktop. Bring your installed
applications together in one clean interface to browse, inspect, and manage them.

Housekeeper brings a consistent graphical interface to applications installed
from different sources, with update and removal actions where supported. It
recognizes RPM, DEB, Pacman and APK packages, Flatpak and Snap applications,
independent AppImages, Chrome and Chromium web apps, PWAsForFirefox, and Steam game shortcuts.

![Housekeeper in list view](docs/screenshots/list-light.png)

<details>
<summary>Grid view</summary>

![Housekeeper in grid view](docs/screenshots/grid-light.png)

</details>

## Features

- Browse a searchable list or grid, filter by source, and sort by name, size, or Last Updated.
- Open search with Ctrl+F and Preferences with Ctrl+comma; choose automatic or manual list refresh.
- Inspect versions, file locations, installation scope, and hidden entries.
- Open apps from their details page and see available updates beside their names.
- Customize launcher icons and restore the originals.
- Preview RPM and Flatpak updates and removal where supported, preserving personal data.
- Choose manual or on-entry update checks, daily or weekly, for RPM and Flatpak.
- Move eligible AppImages to Trash and open external managers for web apps and Steam games.

## Current support

Housekeeper manages installed desktop applications. It does not install new apps,
perform system upgrades, or clean application data. Software sizes exclude shared
runtimes and dependencies; unavailable sizes and update dates stay unknown.

Management support varies by source. DEB, Pacman, APK, and Snap currently provide
external management instructions. Direct RPM removal is unavailable on the tested
Fedora backends; Housekeeper provides guidance instead. See
[compatibility](docs/compatibility.md) for supported environments and limitations.

## Requirements

- Python 3.10+ with PyGObject, GTK 4.12+, and libadwaita 1.4+
- A graphical desktop session; GNOME Shell itself is not required
- Optional RPM, PackageKit, and Flatpak integrations for their respective sources

## Install on Fedora

Install a downloaded RPM matching your Fedora release:

```sh
sudo dnf install ./housekeeper-0.1.14-1.fc44.noarch.rpm
```

Release packages do not configure an automatic update repository.
To uninstall Housekeeper, run `sudo dnf remove housekeeper`.

## Build and run

On Fedora:

```sh
sudo dnf install python3-gobject gtk4 libadwaita meson ninja-build glib2-devel gettext
sudo dnf install python3-rpm PackageKit PackageKit-glib flatpak-libs
meson setup build --prefix=/usr
meson compile -C build
./build/housekeeper-dev
```

The second command enables optional integrations. The development launcher rebuilds
changed resources before starting. Use `GSETTINGS_BACKEND=memory` to try it without
saving preferences.

## Development

See [testing and releases](docs/testing.md) for test commands, disposable integration
environments, and packaging. [Architecture](docs/architecture.md) describes the
implementation; [validation](docs/validation.md) records completed checks and known gaps.
Run package-manager integration tests only in their disposable containers.

## Privacy and diagnostics

Housekeeper has no telemetry, account, or background update service. Preferences
and update results are stored locally. Update checks may contact configured repositories.
System operations use the desktop's Polkit authentication dialog when required;
Housekeeper never collects passwords.

Update errors are logged in `~/.local/state/housekeeper/housekeeper.log`
(or under `$XDG_STATE_HOME`). Set `HOUSEKEEPER_DEBUG=1` for additional diagnostics.
Review logs before sharing: they can include application names and file paths.

The interface is currently English-only, with gettext support for future translations.

## License

[MIT](LICENSE). AppStream metadata is CC0-1.0. Screenshots use synthetic application
inventories; third-party icons retain their original licenses.
