# Architecture

## Data flow

Desktop discovery reads XDG application roots in precedence order using GLib
KeyFile and GIO. It preserves the effective entry even when hidden or invalid for
the current desktop. A lower-priority entry cannot resurrect a hidden override.
Flatpak additionally supplies configured installation roots and installed app refs.

Command classification recognizes web apps, Steam game entries, and direct AppImage
launches before native package attribution. RPM ownership of a browser executable is
not ownership of the web app it launches. Unknown shell wrappers remain manual.

A uniquely RPM-owned desktop file identifies its application package even when its
command belongs to a shared package, as with LibreOffice components and GNOME apps
launched through `gapplication`. Multiple desktop owners remain ambiguous. Executable
ownership is a fallback only for unowned direct launchers without guest-app arguments;
generic interpreters and launch helpers cannot identify their guest applications.

RPM source attribution is separate from permission to manage the application.
`RpmAttribution` compares launcher and entry-point file digests and symlink targets
with installed RPM headers. Shared commands require a matching installed RPM dependency;
an interpreter also requires an application-owned script argument. D-Bus activation
requires a verified service with a matching name, owned by the app or a direct
dependency, whose entry point belongs to the application itself. Higher-priority
session service files, including user overrides, participate in this check. Services
delegating to systemd are currently display-only because unit overrides are not verified.
Unknown digests, modified files, unresolved targets, and unowned launchers can retain
their RPM source but receive management instructions rather than direct actions.
Providers repeat the evidence check before previews and execution; cached attribution
never bypasses it. These local checks do not lock files or prove arbitrary wrapper
script behavior, and do not replace the existing PackageKit transaction checks.

Providers return independent immutable ownership candidates. The inventory service
resolves launcher/command evidence and conflicts before projecting UI records and
merging only confirmed identical launch targets within the same installation. RPM package versions are metadata, not stable
UI identity. Flatpak identity includes the installation path and full ref. Web-app
identity includes the browser, profile, explicit data root, and app ID.

DEB attribution batches local `dpkg-query` package metadata and desktop-file ownership
queries. Only an unambiguous installed owner of the exact desktop path (or a verified
icon override's original path) is used. Unowned launchers, diverted files, and browser
or Steam guest apps are not attributed to host packages. Name and architecture form
stable package identity; updates and removal remain in the external system manager.

The service publishes an immutable initial desktop snapshot without direct actions,
then resolved application records. The
UI shares a Gio.ListStore, filter, sorter, and selection between both virtualized
views. Name sorting uses the current locale and is the default; a remembered setting
also selects largest software size or Last Updated, newest first. Numeric ties use
name and stable identity, with unknown values last. Sorting reuses the installed
metadata gathered during inventory, without per-row queries or network access.
GTK mutations are dispatched on the main loop; blocking integrations
run in a single worker. No application database is written.

## Internal interfaces

`DesktopEntry` and `LaunchSpec` describe a launcher and its environment.
`AppComponent`, `InstallationInstance`, and `ManagementTarget` separate presentation,
installation identity, and actual operation targets; `AppRecord` is their UI projection.
`AttributionResult` preserves candidates, verification states and conflicts. See
[scan attribution](scan-attribution.md) for selection and revalidation rules. `ProviderCapabilities` describes available
operations. `RemovalPlan` is a preview of exact targets with a fingerprint;
`OperationResult` distinguishes success, failure, cancellation, and partial completion.

Update capability is independent of the existing removal action. `UpdateAction`
selects an explicit check or source-specific instructions. `UpdateCheckResult`
distinguishes an available plan from a successful check with no changes; failures
and acknowledged cancellation are separate exceptions. `UpdatePlan` contains exact
`UpdateChange` targets, current versions or commits, sources, and a fingerprint.
Providers implement `prepare_update` and `execute_update` without depending on GTK.
For inventory-wide checks, `discover_updates` returns candidate application keys
for one provider context. Candidate discovery is not an executable plan.

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

Settings cover window geometry, view mode, sort order, hidden-entry visibility,
and update-check mode, interval and participating sources.
Missing metadata is reported as unknown; package origin and executable locations
are never inferred from display names. No network metadata refresh is initiated
by the inventory scan.

## Storage

The details page measures software size on the serialized worker when opened. RPM
reports the installed package's size, DEB uses the installed package's `Installed-Size`
(KiB converted to bytes), Flatpak reports the matching installed ref's size, and
AppImage reports its file length. Package versions and architectures or Flatpak
commits must still match the inventory. Shared dependencies and runtimes are excluded.
Missing metadata remains unknown. These are estimates, not reclaimable disk space.
No user data directories are scanned. Late results cannot update a different details page.

Inventory records retain software-size estimates for sorting, while the details-page
measurement still revalidates the installed version. RPM `INSTALLTIME`, Pacman
`%INSTALLDATE%`, and Snap `install-date` supply the time of the currently installed
package, which may change after an upgrade or reinstall. The interface calls this
Last Updated. DEB, APK, AppImage, web apps and Steam entries currently have unknown
dates. File timestamps and build/commit timestamps are never used as substitutes.

Flatpak dates come from the same local journal message ID as `flatpak history`.
One read-only `journalctl` query per inventory reads up to 5,000 recent deployment
and removal records, with a three-second timeout and an 8 MiB parsing limit.
Structured microsecond timestamps retain the year omitted by the history command's
formatted output. Installation names map to configured paths; user installations
also require the current user's journal UID. The newest relevant event must be an
install/update deployment matching the full ref and current commit. A newer removal,
different commit or invalid date blocks fallback to older deployments. Ambiguous
installation names and unavailable history leave dates unknown. No journal access
permissions are changed. The saved sort value `installed` remains for compatibility
with existing preferences; the data field is `updated_at`.

Pacman uses the installed ALPM database's [`%SIZE%`](https://man.archlinux.org/man/alpm-db-desc.5.en#%25SIZE%25)
field in bytes and the `%FILES%` section to establish exact desktop ownership.
`pacman-conf` resolves custom database and root directories. APK uses the installed
database's `I:` size and `F:`/`R:` file ownership records. Both indexes are loaded once
per inventory, include local builds, and keep missing sizes unknown. Snap reads only
snapd's local `/v2/snaps` GET endpoint and uses active application revisions,
`installed-size`, and the supplied `desktop-file` paths. Socket timeouts and bounded
responses limit unavailable backends; one unavailable provider does not hide others.
All three adapters verify the package identity, installation and version again when
measuring size; Snap also verifies revision. They provide external update and removal
guidance. Nix, Guix, Portage, XBPS and eopkg remain unsupported for attribution and size.

## Appearance and launcher icons

Appearance sits last on the details page and watches GTK icon theme changes only
while the page is mapped. It shows the theme used for icon lookup, or identifies
custom icons and application image files. Global GTK and cursor settings are omitted.
Only direct
`env GTK_THEME=...` launcher assignments are reported as explicit overrides.
The displayed icon path uses the same GTK lookup and fallback as the inventory
and is placed inside Technical Details, including for apps without desktop entries.

Icon edits run on the serialized worker. Images under 10 MB are decoded and saved
as PNGs of at most 512 pixels per side under `$XDG_DATA_HOME/housekeeper/icons`.
Content-addressed image files are retained for reuse. Launchers follow the
[desktop entry specification](https://specifications.freedesktop.org/desktop-entry/latest-single/):
system entries receive a per-user override with the same desktop ID; existing
user entries keep their path. Atomic replacement preserves translations, desktop
actions and other fields. Symlinked user launchers and conflicting overrides are
rejected. Each launcher of a merged record is edited separately.

Private launcher keys retain the original icon and source path. Reset removes an
unchanged generated override to reveal the current source, or restores only its
icon if other fields were edited. Source attribution compares every non-icon key
against the referenced original before RPM or Flatpak can use its path; a source
marker alone is never ownership evidence. Changed source commands or other fields
can make attribution unavailable until the launcher override is reconciled.

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

Flatpak updates retain the installation path, full ref, and origin. Only a changed
commit for the selected application produces an available update; runtime-only,
extension-only, and same-commit repair operations are not application updates. Dependencies
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

Individual and batch update confirmations share one compact presentation. Single
updates show the primary application name and version change; batches show a short,
scrollable application list. RPM epoch/release details are omitted from the summary
only when the upstream versions differ; revision-only updates retain full versions.
A collapsed Details expander contains the full operation list and version strings,
dependency/source information, installation context, launcher aliases, download
estimate and authorization guidance. Both dialogs still pass the original plans
to execution, with Cancel as the default response.

The Updates navigation row stays below the scrollable source list. In the default
on-entry mode, activation requests a check after inventory scanning finishes when no
saved result exists or the last successful check is at least one day old. Preferences
can extend this interval to one week, or select manual-only checks. Manual mode also
cancels a pending entry check waiting for inventory. The page refresh action can request
checks regardless of the selected interval or mode. Filesystem/focus refreshes only read inventory and invalidate changed
rows. They never initiate a background update check. Selection and source filters are
independent. The page uses GTK 4/libadwaita widgets and wraps actions at narrow widths.
Action buttons keep their natural width at the bottom right, with selection status
at the bottom left. This footer stays visible while the application list scrolls. The header
shows the update count and last check time; Details contains separate-updater guidance
and check errors, and the timestamp tooltip explains the selected check policy.

RPM and Flatpak source switches apply to Updates-page checks. Disabled providers
are skipped before grouping and do not increase the unsupported-app count; providers
still receive the full inventory for ownership and dependency validation. Individual
checks in app details and application discovery are unchanged. Changing sources clears
results and their timestamp, requests cancellation of an active page check, and rejects
its late completion using a revision counter. No settings change starts a network check.
With both sources disabled, the page explains how to enable a source and disables refresh.

`UpdateCache` saves successful reports, including empty reports, in the XDG cache
directory using a bounded JSON file and atomic replacement. Failures and cancelled
checks leave the previous successful file intact. Cache schema and application versions
must match; malformed files are ignored. The checked-provider set must also match;
older caches without this field are treated as checks of both providers. Restoring a report requires an unchanged local
app snapshot for each retained item. New or changed inventory shows a refresh reminder
without network access. The last successful check time remains visible, and navigation
preserves checkbox selection. Expiry is evaluated on page entry, including when an
entry request waits for the initial inventory. Cache loading, focus refreshes, and
leaving the page before that inventory completes do not initiate checks. No background
timer runs; failed or cancelled checks do not renew the chosen cache lifetime.
Successful updates remove completed installations from the visible and saved report,
preserving remaining selections, check errors, and the original check time. Batch results
identify fully completed apps by key rather than display name or completed dependency names.
The following inventory refresh reconciles those installations with the saved snapshot;
unrelated inventory changes still show a refresh reminder. Failed or cancelled updates
keep unfinished rows, and removals still invalidate stored previews.
Cached plans still undergo normal provider validation
before execution; a saved preview never authorizes an update on its own.

`UpdateBatch` runs as one task on the existing executor. Hidden inventory records,
including NoDisplay auxiliary launchers such as LibreOffice XSLT filters, do not
become top-level update rows or count as unsupported apps. Visible components such
as Writer, Calc and Impress remain separate desktop applications. Hidden components
remain in the full inventory and can still participate in required dependency plans.
The Updates-page scope is independent of the inventory's Show Hidden preference.

Checks group RPM name/architecture and Flatpak installation/full-ref identities,
retaining the visible application names. Flatpak calls
`list_installed_refs_for_update` once per configured installation represented in
the check, then intersects app-kind refs with the inventory. That API may also
return an app needing only a related-ref repair, so the full preview must still
prove its own commit changes. PackageKit refreshes metadata and calls `get_updates`
once, matching package name and architecture to desktop records. Its candidate
snapshot is used only for that check's previews; individual checks and execution
query again. Only candidates undergo per-application ownership revalidation and
full dependency simulation. A failed discovery is not retried for every app in
that context; other contexts continue independently. Provider failures are collected separately
from successful no-update results. Cancellation returns any partial check results to
the caller, but the page preserves its previous list, selection, status, and check time
instead of presenting that incomplete snapshot. A brief toast acknowledges cancellation.
Checks use one determinate progress bar based on completed installation groups.
Backend phase percentages and unknown estimates cannot reset it or switch it to
pulse mode. Failed checks count as attempted groups; cancellation never reports
full completion. The status area reserves separate single lines for the application
and backend status, and ellipsizes longer messages
with a full tooltip so source changes do not resize the progress window.
During update checks, Cancel stays enabled independently of the current backend's
ability to interrupt immediately. A click requests cooperative cancellation and
disables the button once; a non-interruptible read can finish before cancellation
is acknowledged. Update execution and removal still follow backend cancellation
capabilities. Later progress callbacks cannot re-enable a requested cancellation.
A locked
cancellation controller follows provider changes without opening gaps between tasks.

Cached rows must satisfy the same visibility and application-change rules, including
older caches containing runtime-only plans. Filtering does not renew the timestamp.
The page describes desktop application updates, not overall system update status.
See [the GNOME Software comparison](update-design-review.md) for source references
and the performance tradeoff of retaining full previews for actual candidates.

Execution re-previews each selected installation and stops at the first error. A preceding
successful transaction may have already completed a shared dependency. Only those exact
confirmed changes may disappear from a later plan; the target, current application state,
remaining operations, and source-configuration digest must still match. Providers then
perform their own final validation. Unrelated external changes require a new check and
confirmation. This is a series of transactions, without a batch-wide lock or rollback.
