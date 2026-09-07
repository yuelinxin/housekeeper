# Changelog

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
