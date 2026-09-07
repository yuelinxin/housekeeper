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

Update capability is independent of the existing removal action. `UpdateAction`
selects an explicit check or source-specific instructions. `UpdateCheckResult`
distinguishes an available plan from a successful check with no changes; failures
and acknowledged cancellation are separate exceptions. `UpdatePlan` contains exact
`UpdateChange` targets, current versions or commits, sources, and a fingerprint.
Providers implement `prepare_update` and `execute_update` without depending on GTK.

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

## Update boundaries

Only an explicit check refreshes network metadata. PackageKit checks first refresh
configured sources, resolve the exact name and architecture, and compare installed
versions with a fresh local RPM database. Update simulation must positively report
the selected target and all dependency operations. New dependencies and upgrades
are allowed; downgrades, reinstalls, extra removals, and Housekeeper self-updates
are rejected. Old versions replaced by their own upgrades are not extra removals.
Execution repeats the preview and fingerprints dependency versions and repository
configuration. PackageKit's trusted-package flag and Polkit remain in effect.
Preview and execution are separate transactions, not an atomic lock or rollback.

Flatpak updates retain the installation path, full ref, and origin. Dependencies
and related extensions participate in the preview, while unused-runtime removal
and pruning are disabled. `ready-pre-auth` captures the resolved transaction and
aborts before deployment. Only the deliberate Flatpak abort is swallowed; network
and authentication failures are never interpreted as "up to date". Execution pins the selected commit for user installations. System installations use
the normal system-helper update path because explicit commits require a root process.
Both compare the full plan and exact target commits at `ready` before deployment, including
dependency commits and remote configuration. New remotes, ambiguous source choices,
account login, and application migration require external management. Cancellation is
requested cooperatively through the transaction's `GCancellable`,
including extra-data downloads. The UI stays open until the backend returns and
installed commits are verified; completed components are reported as partial results.

The window keeps both actions disabled through preparation, confirmation, and
execution. Callback tokens prevent completed operations from updating a later task.
The single service worker serializes checks, scans, and mutations and restores state
even when provider creation or task submission fails. Results are verified against
installed versions or commits; partial changes are reported without claiming rollback.
Every execution outcome refreshes inventory. No automatic restart is performed.

## Updates page and batches

The Updates navigation row stays below the scrollable source list. Its first activation
requests a check after inventory scanning finishes; the page refresh action requests
subsequent checks. Filesystem/focus refreshes only read inventory and invalidate changed
rows. They never initiate a background update check. Selection and source filters are
independent. The page uses GTK 4/libadwaita widgets and wraps actions at narrow widths.

`UpdateBatch` runs as one task on the existing executor. It groups RPM name/architecture
and Flatpak installation/full-ref identities, retaining all application names. RPM
metadata refresh is shared within a check. Provider failures are collected separately
from successful no-update results; cancellation retains partial check results. A locked
cancellation controller follows provider changes without opening gaps between tasks.

Execution re-previews each selected installation and stops at the first error. A preceding
successful transaction may have already completed a shared dependency. Only those exact
confirmed changes may disappear from a later plan; the target, current application state,
remaining operations, and source-configuration digest must still match. Providers then
perform their own final validation. Unrelated external changes require a new check and
confirmation. This is a series of transactions, without a batch-wide lock or rollback.
