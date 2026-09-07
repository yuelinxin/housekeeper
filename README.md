# Housekeeper

A lightweight GTK 4 application for understanding where your desktop applications
come from, where their files live, and how to remove them correctly.

Housekeeper combines desktop entries with supported installation providers. It
recognizes RPM packages, Flatpak applications, independent AppImages, Chrome and
Chromium web apps, PWAsForFirefox, and Steam game shortcuts.

![Housekeeper in list view](docs/screenshots/list-light.png)

## Features

- Search applications by name, package identity, or file location.
- Switch between a list and an icon grid, with source filters and adaptive navigation.
- Inspect versions, installation scopes, desktop entries, and executable paths.
- Include hidden and auxiliary entries, with explanations of their visibility.
- Preview RPM and Flatpak removal, preserving personal application data.
- Move precisely identified, user-owned AppImage files and launchers to Trash.
- Open the appropriate browser or Steam manager for externally managed applications.

Housekeeper does not install or update software, clean application data, or list
every command-line package. It never scans the entire disk looking for executables.

## Requirements

- Python 3.10 or newer, with PyGObject
- GTK 4.12 or newer
- libadwaita 1.4 or newer
- A graphical desktop session; GNOME Shell itself is not required

RPM ownership uses the optional Python RPM bindings. Direct RPM removal additionally
requires PackageKit, its introspection bindings, a compatible backend, and Polkit.
Flatpak support uses the optional libflatpak introspection bindings. Missing providers
leave the rest of the inventory usable.

The initial system-package removal target is Fedora Workstation 43 and 44.
See [compatibility](docs/compatibility.md) for other environments and limitations.

**Current native-removal limitation:** the tested Fedora PackageKit backends cannot
provide the required dependency-safe removal plan: Fedora 43 rejects the no-cascade
request, and Fedora 44 returns an empty preview. Housekeeper retains the application
and gives terminal management guidance. Direct RPM removal must not be advertised
as verified on these backends. Flatpak and AppImage follow independent paths.

## Build and run on Fedora

```sh
sudo dnf install python3-gobject gtk4 libadwaita meson ninja-build glib2-devel gettext
sudo dnf install python3-rpm PackageKit PackageKit-glib flatpak-libs
meson setup build --prefix=/usr
meson compile -C build
./build/housekeeper-dev
```

The second installation command enables optional integrations. Build tools are not
needed when installing a release RPM. The launchers always use `/usr/bin/python3`,
including when a Conda environment is active, and work from any current directory.

The development launcher uses build-tree resources and schemas. To test without
saving preferences, launch it with `GSETTINGS_BACKEND=memory`.

To install a downloaded release package:

```sh
sudo dnf install ./housekeeper-0.1.0-1.fc44.noarch.rpm
```

Install the build matching your Fedora release. GitHub release packages do not
configure an automatic update repository. To remove Housekeeper itself, use
`sudo dnf remove housekeeper`.

## Development and validation

```sh
sudo dnf install python3-pytest
PYTHONPATH=src /usr/bin/python3 -m pytest
meson test -C build --print-errorlogs
ruff check src tests build-aux
ruff format --check src tests build-aux
mypy
```

Install Ruff and mypy in a development environment; they are not runtime dependencies.
GUI smoke tests use synthetic applications and perform no removal:

```sh
GTK_A11Y=none GIO_USE_VFS=local \
  dbus-run-session --config-file=tests/session.conf -- \
  /usr/bin/python3 tests/smoke_ui.py
```

For CI without a display, put `xvfb-run -a` before `dbus-run-session`. This smoke test
checks layout and interaction; it does not replace a real screen-reader test.

See [testing and releases](docs/testing.md) for disposable package-manager tests,
RPM builds, and the release checklist. Never run integration fixtures on a personal
system; their harness explicitly requires a disposable container.

## Privacy and behavior

Housekeeper keeps its inventory in memory and preferences in GSettings. It has no
telemetry, account, background service, or automatic network scan. Package managers
and explicitly opened external managers may use their own network connections.

`HOUSEKEEPER_DEBUG=1` enables diagnostic logging. Review logs before sharing them:
application names, paths, and package-manager errors can identify installed software.

All maintained repository prose, comments, and interface text are English.
The gettext structure is ready for future translations; version 0.1 is English-only.

## License

MIT; see [LICENSE](LICENSE). AppStream metadata is CC0-1.0. Application screenshots
show synthetic inventory data; third-party application icons retain their original licenses.
