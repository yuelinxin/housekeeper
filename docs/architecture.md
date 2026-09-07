# Architecture

## Data flow

Desktop discovery reads XDG application roots in precedence order using GLib
KeyFile and GIO. It preserves the effective entry even when hidden or invalid for
the current desktop. A lower-priority entry cannot resurrect a hidden override.
Flatpak additionally supplies configured installation roots and installed app refs.

Command classification recognizes web apps, Steam game entries, and direct AppImage
launches before native package attribution. RPM ownership of a browser executable is
not ownership of the web app it launches. Unknown shell wrappers remain manual.

Providers enrich records; the inventory service merges only proven identical launch
targets within the same installation. RPM package versions are metadata, not stable
UI identity. Flatpak identity includes the installation path and full ref. Web-app
identity includes the browser, profile, explicit data root, and app ID.

The service publishes an initial desktop inventory and then enriched records. The
UI shares a Gio.ListStore, filter, sorter, and selection between both virtualized
views. Records are sorted by name. GTK mutations are dispatched on the main loop; blocking integrations
run in a single worker. No application database is written.

## Internal interfaces

`DesktopEntry` describes a launcher, while `AppRecord` describes an application
installation and its related launchers. `ProviderCapabilities` describes available
operations. `RemovalPlan` is a preview of exact targets with a fingerprint;
`OperationResult` distinguishes success, failure, cancellation, and partial completion.

The provider contract is private. New integrations implement capability detection,
inventory attribution, a preparation step, and execution. They must not call GTK.
The current package and Flatpak integrations are loaded lazily so missing typelibs
do not prevent startup. No third-party plugin ABI or public D-Bus service is exposed.

## Removal boundaries

RPM uses local ownership data and exact installed PackageKit IDs. Simulation must
report only the selected package. Both simulation and execution disable dependency
cascades and automatic dependency cleanup. The target and plan are checked again
before execution. Polkit handles privilege escalation outside the GTK process.
Housekeeper cannot remove itself through its own UI.

Flatpak uses `Flatpak.Transaction`. Preparation resolves the transaction and returns
false from `ready`, before any uninstall. Execution compares the installation,
installed commit, and operation list at the same signal. Related refs and unused
runtime cleanup are disabled for version 0.1. Personal data is preserved.

AppImage removal requires exact regular files inside the user's home, unambiguous
direct launchers, and negative package-ownership evidence. Symlinks and shared
targets remain manual. Device, inode, size, modification time, ownership, and mode
are rechecked. Only GIO Trash is used; there is no permanent-delete fallback.
If the program cannot be moved, its launchers are retained. Partial launcher failure
is reported individually. This is not a transactional filesystem rollback system.

External management opens a known browser management page with only recognized
profile arguments, or the Steam library URI. It never edits browser databases or
reports an external handoff as an uninstall. PWAsForFirefox has explicit instructions
because its extension manager has no stable cross-profile deep link used by v0.1.

## Lifetimes and observation

Directory and Flatpak monitors are active only while the window is open. Events are
debounced, and returning to the active window schedules a refresh. A pending refresh
runs after an operation completes. Closing during an active operation keeps the
window alive and explains that the task must finish or be safely cancelled.

Settings are limited to window geometry, view mode, and hidden-entry visibility.
Missing metadata is reported as unknown; package origin and executable locations
are never inferred from display names. No network metadata refresh is initiated
by the inventory scan.
