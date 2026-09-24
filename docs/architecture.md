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
are selected with the same bounded parser and precedence rules used for Flatpak
activation, then resolved again after RPM file verification to detect changes.
Unreadable higher-priority directories cannot be skipped. Services
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

The service publishes an independent initial desktop snapshot without direct actions,
then resolved application records. Merging copies each retained entry, including
entries appended to an existing group, so the initial callback needs no second full
copy. Hidden installation overlays are matched using a desktop-ID lookup. The
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

## Software sources

Preferences has separate General and Software Sources pages. Repository reads begin
only when the sources page opens. `RepositoryManager` enumerates native repositories
through PackageKit and remotes in every configured Flatpak installation; one unavailable
backend does not hide the others. The Software Sources page starts with Update Sources
switches, which control the application types checked on Updates independently of
the repositories listed below. General retains the update-check mode and interval.
The native repository list initially shows enabled sources. A final Show Disabled Sources
row reveals disabled sources below them and can collapse them again; the expanded
state survives refreshes within the same preferences window.

Enable/disable operations re-read the selected repository before writing and retain
the native repository ID or exact Flatpak installation path and remote name. Flatpak
changes modify only the disabled flag. Native repository creation and deletion remain
in the distribution's software manager. Backend authorization errors appear in the page,
which rereads actual state after a failed write instead of trusting the optimistic switch.

Flatpak imports accept a bounded local `.flatpakrepo` file or HTTPS link. A preview
records its bytes, destination, name, URL and signature-verification state; confirmation
uses those same bytes and rejects an existing name. Imports enable GPG verification
unless the file itself sets `GPGVerify=false`, matching the saved OSTree default even
though libflatpak's uncommitted remote object reports both an unset and a false flag as
false; the review then states that verification is disabled. The source's embedded key
is imported by libflatpak. Downloads follow redirects only to HTTPS. See the
[Flatpak repository format](https://docs.flatpak.org/en/latest/flatpak-command-reference.html#flatpak-flatpakrepo).

Repository work runs on the serialized management worker. Changes cancel obsolete
installer searches and clear their catalogues on the search worker, after any in-flight
search finishes. Successful writes refresh inventory and invalidate update previews;
failures also invalidate previews because a backend may have already saved its change.
The preferences window stays open during a write, and late read callbacks are ignored
after it closes.

## Adding applications

Every inventory page opens the same Install Application dialog with a type selector,
initially set to the host's native package type regardless of the current page.
Inventory refresh lives in the sidebar menu; the Updates page
retains its own refresh action. Package searches debounce for 400 ms on a separate
single worker, cancel obsolete requests, and discard callbacks after input changes
or window closure. Results retain the exact PackageKit ID or Flatpak installation,
remote and full ref. At most 12 candidates are shown. A query may be a phrase: runs
of whitespace collapse to single spaces, and each word may contain letters, digits and
`. + : -`. AppStream searches and the Snap Store receive the whole phrase; searches that
can only match package names or app IDs use its longest word. A phrase counts as naming
an app only when every word appears in the app's name.

Native search first matches desktop applications in the distribution's AppStream
catalogue (`LOAD_OS_CATALOG` only; loaded once on the search worker and dropped with
the remote catalogues), so app names, summaries and keywords match while libraries,
plugins and `-devel` packages never appear. Apps whose name contains the query come
first, then AppStream's relevance order; rows show the catalogue name and icon. Their
package names are resolved to exact available PackageKit IDs. When the catalogue is
missing or nothing it matches is installable, as for many third-party repositories,
search falls back to PackageKit name search with the GUI filter (packages with
`application()` provides, i.e. a desktop file), ordered by exact name, prefix,
substring and simple spelling similarity, with at most one shorter-prefix retry of
at least three characters. Both paths drop installed names through an explicit
`INSTALLED` resolve: the Fedora 44 DNF5 backend's `NOT_INSTALLED` filter still returns
an installed build that a repository also carries.

Flatpak search reads each enabled remote's own AppStream catalogue, the
`appstream.xml.gz` Flatpak keeps in the remote's AppStream directory, and never
downloads one; Flatpak refreshes it when the remote is updated. A parsed catalogue
is reused until its resolved directory, size or modification time changes. Desktop
applications whose Flatpak bundle is an uninstalled app ref of that remote match by
name, summary and keywords, named apps first and then relevance across all remotes;
addons and runtimes are excluded. Without any catalogue match, for example after a
typo, app IDs are ranked as before. Either way, the shown rows use the catalogue's
name, summary and cached icon (from the catalogue's `icons/<size>` directory, plain
file names only) where the remote lists the app, and keep the remote, scope and
branch in the subtitle. A remote's enumerated catalogue is reused for two minutes, so
successive keystrokes re-rank it instead of re-reading every remote summary.
Selecting a result is required before installation.

PackageKit installs the selected native package ID and its dependencies with
ONLY_TRUSTED and desktop Polkit interaction. It only operates on the host's package
family; OSTree and bootc hosts retain external management. Flatpak searches
configured user and system remotes, excludes installed refs, runtimes and foreign
architectures, and installs into the selected source's installation. It permits
dependencies from configured remotes but does not add new remotes automatically.
Transactions include the default system installations as dependency sources, so
user applications can reuse installed system runtimes without duplicating them.
Snap uses snapd's read-only search endpoint and hands the selected name to the
Snap Store; a host without snapd reports the missing service rather than a socket error. Steam opens its library for installation and shortcut creation.

Web integration creates a per-user desktop file using the selected Chrome or
Chromium executable with `--app=<http(s) URL>`. This is a website launcher, not an
entry in the browser's installed-PWA database. Names default to the hostname.
The launch command selects the Default browser profile so its window identity is
stable. The desktop filename matches Chromium's Wayland app ID, while
`StartupWMClass` matches its X11 instance. These derive from the canonical URL's
host and path following Chromium's
[Linux window identity implementation](https://chromium.googlesource.com/chromium/src/+/main/chrome/browser/ui/views/frame/browser_native_widget_aura_linux.cc).
This lets GNOME associate the window with the website's launcher, name and icon even
when a normal browser window already exists. URLs differing only in query, fragment,
port or scheme share Chromium's window identity; an existing launcher is never
overwritten. This desktop integration does not register an installed browser PWA.
Exec arguments escape both desktop field codes and KeyFile syntax; no shell is used.
Installation fetches the page's declared icon or touch icon, preferring larger sizes,
then tries `/favicon.ico`. Relative links use the final page URL and its first base
URL. Requests accept only HTTP(S), validate redirects, use short socket timeouts and
an eight-second lookup budget checked between requests and reads. Page reads stop
at 512 KiB; icons over 2 MiB are rejected. No browser cookies or external favicon
service are used. Decoded icons are normalized to a local PNG, and lookup failures
fall back to `web-browser` without blocking installation. Cancellation stops the
installation. Inventory scans never fetch website icons.
Inventory offers direct Uninstall for verified Housekeeper website launchers. The
creation marker alone is insufficient: the file must be owned by the current user,
unowned by a package, directly inside the user's applications directory, and match
the generated Exec format and window-identity filename (or the legacy browser/URL
filename hash). Symlinks and shared
launchers are rejected. Removal previews the exact desktop file and rechecks its
ownership, identity and contents before moving it to Trash. Browser data and icons
are kept. Other website shortcuts and installed PWAs retain external management.

AppImage integration follows [Gear Lever's](https://github.com/mijorus/gearlever)
file-selection and managed-directory approach with only an optional name edit.
The importer copies a regular, non-symlink file to `~/AppImages`, checks the copied
ELF/AppImage marker, sets mode 0755, and publishes a launcher. It never executes
the AppImage to extract metadata. Content hashes identify copies, existing files
are never overwritten, and a failed launcher write removes only the new copy.
The original download remains intact. Web App and AppImage forms offer an optional
desktop icon with a preview and a reset button. Images use the same 10 MB input limit
and normalized PNG storage as Appearance; launchers keep a private copy independent
of the selected file. Web Apps default to the saved website icon; AppImages use the
standard `application-x-executable` theme icon, also shown in the installation form.
Custom icons take precedence and Appearance can restore these defaults later.
Invalid custom images prevent launcher creation, and AppImage rollback
also removes the new application copy if saving its icon fails.

Installations reuse the serialized management worker, progress, cancellation,
close protection and post-operation inventory refresh. External store/Steam
handoffs are not reported as completed installations. Package-manager cancellation
does not promise rollback of dependencies already installed; Flatpak reports
completed operations separately if a later operation fails.
PackageKit's search snapshot can briefly lag a completed transaction even with the
not-installed filter; inventory refresh still reads the native package database.

## Removal boundaries

RPM uses local ownership data and exact installed PackageKit IDs. Simulation must
report only the selected package. Both simulation and execution disable dependency
cascades and automatic dependency cleanup. The target and plan are checked again
before execution. Polkit handles privilege escalation outside the GTK process.
Housekeeper cannot remove itself through its own UI.

Flatpak uses `Flatpak.Transaction`. Preparation resolves the transaction and returns
false from `ready`, before any uninstall. Execution compares the installation,
installed commit, and operation list at the same signal. Related refs and unused
runtime cleanup are disabled. The confirmation defaults to keeping personal data.
An explicit choice to delete data is carried in the immutable removal plan and
applies only after successful uninstall. Flatpak shares the current user's
`~/.var/app/<app-id>` across installations and branches; the dialog discloses this.
libflatpak has no delete-data transaction option, and the CLI cannot delete a
specified app's data after its ref is uninstalled. Housekeeper therefore removes
only that app ID's data directory using directory descriptors without following
symlinks, then calls `flatpak permission-reset` for that ID, matching the CLI's
`--delete-data` behavior. Cleanup failures report a partial result with the
application uninstall recorded as complete. Files outside that directory remain.

References: [Flatpak uninstall implementation](https://github.com/flatpak/flatpak/blob/main/app/flatpak-builtins-uninstall.c),
[Flatpak data directories](https://docs.flatpak.org/en/latest/conventions.html).

AppImage removal requires exact regular files inside the user's home, unambiguous
direct launchers, and negative package-ownership evidence (or explicit absence of
all supported package databases). Missing or failed applicable backends still block
management. Symlinks and shared
targets remain manual. Device, inode, size, modification time, ownership, and mode
are rechecked. Only GIO Trash is used; there is no permanent-delete fallback.
If the program cannot be moved, its launchers are retained. Partial launcher failure
is reported individually. This is not a transactional filesystem rollback system.

External management opens the selected Chrome/Chromium web app with its validated
app ID and recognized profile arguments, or the Steam library URI. The web app's
own menu provides the uninstall action. It never edits browser databases or
reports an external handoff as an uninstall. PWAsForFirefox has explicit instructions
because its extension manager has no stable cross-profile deep link used by v0.1.

The details page's Open action launches the selected desktop entry through GIO,
preserving browser profiles, environment settings, terminal requirements, and D-Bus
activation. Applications with multiple launchers present a choice. Available updates
appear as an update-symbol badge after the application name, matched by installation identity
and refreshed with the Updates page's results. Update actions remain on the Updates page.

## Lifetimes and observation

Directory and Flatpak monitors are active only while the window is open. Events and
window activation coalesce into a single automatic request 30 seconds later, which
waits for a five-second quiet period so a package transaction is never read halfway
through, and then for a five-minute cooldown after the last successful scan. A failed
scan takes a one-minute cooldown instead, so a momentarily unavailable backend is
retried rather than left for five minutes. Manual refresh bypasses both and cancels a
pending automatic request. A pending refresh runs after an operation completes. Closing
during an active operation keeps the window alive and explains that the task must
finish or be safely cancelled.

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

Package updates are applied to the running system, so the preview also reports which
of the transaction's installed paths a process currently executes or maps. Only paths
are compared, through procfs; process memory, command lines and environments are never
read, and a denied or exited process is skipped rather than reported as idle. These
lists stay out of the plan fingerprint because they change with ordinary desktop
activity. Execution instead compares them asymmetrically: quitting a program before
updating is the advised response to the warning and must not invalidate consent, while
a program started after the preview stops the update, because the user approved a
transaction that did not name it. Housekeeper does not stage offline updates; a single
`/system-update` slot cannot be shared with the system's own updater, and a transaction
applied at the next boot cannot be verified against the RPM database while the user
is present. See [the GNOME Software comparison](update-design-review.md).

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
requested cooperatively through the transaction's `GCancellable`, and a running instance
is reported from `FlatpakInstance` without ever blocking the update,
including extra-data downloads. The UI stays open until the backend returns and
installed commits are verified. Completion requires every planned component to sit at
its planned commit and remote, which a repair operation already satisfies; only
components whose commit actually changed are reported as changed. A transaction that
stops early reports partial results.

The preview also compares the selected application's sandbox context — `shared`,
`sockets`, `devices`, `features`, `filesystems`, `persistent` and bus policies — between
the installed and resolved metadata, matching what `flatpak` reports before applying an
update. Only widened access is listed; a withdrawn or negated entry is not a new
permission. The difference is part of the plan fingerprint, and unreadable metadata
refuses the update rather than implying that nothing changed.

The window keeps both actions disabled through preparation, confirmation, and
execution. Callback tokens prevent completed operations from updating a later task.
The single service worker serializes checks, scans, and mutations and restores state
even when provider creation or task submission fails. A background scan blocks nothing
the user asks for: details, launching, update checks and management requests all remain
available, and a management request submitted during a scan queues on that worker and
runs against the inventory the scan published rather than the one it replaced. Only a
second concurrent management operation is refused. While a request is queued its task
dialog says it is waiting for the inventory and keeps Cancel enabled, because a
cancellation asked for before the operation starts withdraws it instead of starting it.
An inventory that arrives mid-operation does not prune the Updates list or call it
stale; that reconciliation is replayed against the operation's own result once it
ends. Results are verified against installed versions or commits; partial changes are
reported without claiming rollback. Every execution outcome refreshes inventory. No
automatic restart is performed.

## Updates page and batches

Individual and batch update confirmations share one compact presentation. Single
updates show the primary application name and version change; batches show a short,
scrollable application list. RPM epoch/release details are omitted from the summary
only when the upstream versions differ; revision-only updates retain full versions.
Other eligible launchers of the same installation appear once in the visible summary.
An update that widens a Flatpak sandbox names the added access in the visible summary
and in a card above Details, never only inside the collapsed disclosure. A second card
carries one line per application about programs the update would replace while they run:
what to do, not which files. The paths themselves stay in Details, bounded so that a
transaction touching a core library cannot fill the dialog. A running Flatpak keeps its
own deployment until it exits, so it is disclosed as a version the user must reopen to
see, not as a hazard: that card keeps the neutral accent, while widened sandbox access
and any file replaced under a running process take a warning accent. Both tint the
libadwaita semantic colors rather than fixed hues, so light, dark and a custom accent
preference stay legible. A centered
disclosure button toggles full-width Details containing the operation
list and version strings, dependency/source information, installation/target context, download
estimate and authorization guidance, with the operation list counting its entries and
naming any newly installed package before listing them. The removal confirmation uses
the same disclosure for the exact targets of `RemovalPlan.affected` — files to be moved
to Trash, or the application entries a package removal takes with it — and for its
installation/target context, keeping the visible dialog to the consequence and the
Flatpak user-data switch. Both dialogs still pass the original plans
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
