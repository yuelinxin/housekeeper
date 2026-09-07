# Changelog

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
