# Changelog

## 0.1.10 — 2026-09-09

- Verify the effective D-Bus service and installation before authorizing Flatpak
  management; service overrides, contents and symlink changes invalidate previews.
- Correct duplicate Flatpak/Other entries for D-Bus applications such as Gapless by
  verifying their installed desktop exports, including icon-only user overrides.
  Keep distinct D-Bus components separate even when their fallback commands match.
- Verify Flatpak launcher targets and installation selection before association and
  management; unrelated labels and guest apps cannot inherit host uninstall actions.
- Resolve independent ownership evidence centrally, expose conflicts, and separate
  visible components from installations and transaction targets. Revalidate all
  direct operations and invalidate older cached previews.
- Honor empty XDG defaults and common env options using the effective launch PATH.
- Identify AppImages by ELF/type markers and query all applicable supported file
  ownership backends before allowing Trash, including on non-RPM systems.

## 0.1.9 — 2026-09-08

- Place search and the main menu on either side of the Housekeeper sidebar title.
  Hide the search field until requested; Ctrl+F opens it, and Escape or the search
  button closes it and clears the filter.
- Move refresh to the left of the application and Updates headers, with sorting
  beside application refresh. Keep the sidebar button first in narrow windows.
- Add Ctrl+comma to open Preferences and a remembered automatic app-list refresh
  switch, enabled by default. Disabling it cancels queued automatic refreshes while
  preserving startup loading, manual refreshes, and refreshes after app operations.
- Display hidden application icons at 50% opacity in both list and grid views.
- Refresh the README screenshots and synchronize application and release metadata.

## 0.1.8 — 2026-09-08

- Remove successfully updated applications from the visible and persisted update
  cache while retaining remaining updates, selections, and the original check time.
  Preserve unfinished applications after partial failures or cancellation, and keep
  unrelated inventory changes subject to a fresh check.
- Recognize RPM applications with shared executables or D-Bus launchers, including
  LibreOffice Calc, Impress and Writer, GNOME Maps, and GNOME Weather, using desktop
  package ownership and verified package relationships.
- Check installed RPM launcher and entry-point contents, symlink targets, direct
  dependencies, and applicable D-Bus services. Revalidate targets before direct
  management; retain source identification with instructions when evidence is
  insufficient, including unsupported systemd-delegated activation.
- Add cache, attribution, and GTK regressions, and simplify the README.

## 0.1.7 — 2026-09-08

- Add update preferences for checks on entry or manual checks only, daily or
  weekly intervals, and independent RPM/Flatpak source switches. Match cached
  reports to the enabled sources and discard results when that selection changes.
- Simplify the header: keep view switching beside sorting, including in narrow
  windows, and keep hidden-entry visibility in Preferences only.
- Add a remembered sort menu for list and grid views: name (default), largest software
  size first, or Last Updated (newest first). Show the selected metric and put unknown
  values last, with alphabetical ties and preserved selection when changing order.
- Reuse inventory metadata for sorting. Last Updated comes from RPM, Pacman and Snap
  metadata or Flatpak's local deployment journal and is also shown in details.
- Match Flatpak history by installation, full ref and current commit, with user-scope
  isolation and no fallback to a stale deployment before an uninstall or newer change.
  Unavailable history leaves the date unknown without interrupting inventory.
- Recompile the development settings schema when its source changes.

## 0.1.6 — 2026-09-07

- Show software size in app details for RPM, DEB, Pacman, APK, Flatpak, Snap and
  AppImage installations. Keep unavailable sizes unknown; omit user-data scanning
  and exclude shared dependencies and runtimes from estimates.
- Identify DEB, Pacman, APK and Snap applications from local installed-package
  metadata and exact desktop ownership. Include Pacman-registered AUR builds,
  custom Pacman roots and Snap desktop exports. Keep update and removal actions
  for these sources in their external package managers.
- Use one overall update-check progress bar based on completed installations.
  Backend phase percentages and unknown estimates no longer reset its progress.
- Keep the progress window size stable with separate fixed-height application
  and status lines, using tooltips for long messages.
- Keep Cancel enabled throughout update checks and disable it once after a click.
  Defer cancellation when a backend phase cannot stop immediately, and prevent
  later progress callbacks from re-enabling the button.
- Add storage, package-attribution, cancellation and GTK layout regressions, and
  synchronize application, build, package and AppStream metadata for v0.1.6.

## 0.1.5 — 2026-09-07

- Name the second sidebar category from the distribution's native package family
  using os-release, including DEB on Ubuntu/Debian and RPM on Fedora/openSUSE.
  Keep unsupported-provider guidance separate from verified application sources.
- Add an Appearance group at the bottom of app details with the icon theme or
  custom image and explicit launcher GTK theme overrides when available. Show
  resolved icon files in Technical Details.
- Change launcher icons from local images and restore the original icon. Save
  images durably, preserve other launcher fields, and keep verified RPM/Flatpak
  attribution when creating a per-user icon override.
- Explain GNOME's app-grid refresh limitation in Appearance and in a GNOME Tip
  attached to the save/restore notification, with logout/login guidance when needed.
- Move list and grid spacing inside scrolling content so scrollbars and overshoot
  effects reach the page edges while preserving content insets.
- Refresh README screenshots and synchronize application, build and package metadata.

## 0.1.4 — 2026-09-07

- Cache successful update checks across page navigation and application restarts,
  including empty results. Show the last check time and recheck on page entry after
  24 hours, with manual refresh available anytime. Invalidate affected entries when
  installed applications change.
- Keep the previous update list and selection when a check is cancelled, including
  when providers have returned partial results. Preserve the cached check time.
- Simplify Cancel Operation to Cancel and omit the intermediate cancellation-request notice.
- Keep Updates actions compact at wide window sizes and shorten the header to an
  update count and check time. Move updater guidance into Details and cache timing
  guidance into the timestamp tooltip.
- Place update actions at the bottom right and selection status at the bottom left,
  in a footer that remains visible while scrolling the update list. Remove separators
  above the footer and the Updates sidebar row.
- Remove the rectangular hover background around detail-page pill buttons while
  preserving their native hover, pressed, and keyboard-focus styling.
- Draw grid selection, hover, and pressed backgrounds on the tile alone, give list
  rows subtle rounded corners, and share the same state colors across both views.

## 0.1.3 — 2026-09-07

- Add RPM and Flatpak update checks beside the removal action, with current/target
  versions, dependency previews, explicit confirmation, progress, and verified results.
- Add Updates at the bottom of the sidebar, with app selection, Update All, shared
  dependency handling, and partial-completion reporting. Checks run on explicit user action.
- Support system Flatpak updates through Polkit while retaining exact plan checks.
  Explain system password/fingerprint authentication when authorization is required.
- Enable cooperative cancellation during Flatpak updates and extra-data downloads;
  keep the window open until the backend stops and installed state is verified.
- Provide source-specific update instructions for externally managed applications
  and unsupported operations. Keep update capability independent of removal support.
- Rebuild GTK resources before starting the development launcher and record failures
  in rotating local diagnostic logs.
- Remove ellipses from interface text and keep update/removal controls usable at narrow widths.
- Add signed RPM and Flatpak update fixtures, system authorization and download-cancellation
  regressions, service lifecycle tests, adaptive GTK coverage, and updated screenshots.
- Synchronize application, package, and AppStream versions; derive archive names from
  the application version and backfill the 0.1.2 release notes.

## 0.1.2 — 2026-09-07

- Fix startup crashes caused by GTK background SVG icon loading while preserving
  icon themes, scaling, and fallback icons.
- Rename the System Packages sidebar category to RPM to distinguish installation
  source from System/User scope.
- Add repeated SVG startup regression tests and update screenshots and testing guidance.

## 0.1.0 — development

- Add a unified inventory of desktop applications and supported installations.
- Add source filters, list and grid views, search, and adaptive app details.
- Add RPM and Flatpak removal previews and conservative AppImage trash operations.
- Add browser web-app and Steam management guidance.
- Add optional-provider degradation, package fixtures, GTK smoke tests, RPM packaging,
  and draft release automation.

This is a development release. Refer to the validation record before publishing.
