# Changelog

## Unreleased

- Rename the System Packages sidebar category to RPM to distinguish source from
  installation scope.
- Avoid GTK's background SVG icon preloading path, which can crash during startup
  while rendering text in application icons. Preserve icon themes, scaling, and fallbacks.
- Add repeated SVG startup regression tests with generated fixtures.

## 0.1.0 — development

- Add a unified inventory of desktop applications and supported installations.
- Add source filters, list and grid views, search, and adaptive app details.
- Add RPM and Flatpak removal previews and conservative AppImage trash operations.
- Add browser web-app and Steam management guidance.
- Add optional-provider degradation, package fixtures, GTK smoke tests, RPM packaging,
  and draft release automation.

This is a development release. Refer to the validation record before publishing.
