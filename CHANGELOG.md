# Changelog

## 0.3.1 — 2026-10-12

- Update Housekeeper from within Housekeeper. Update checks on the Updates page
  also look for a new Housekeeper package, using the same check as the other apps.
  An available update appears below a divider at the bottom of the sidebar instead
  of in the list; choosing it opens the usual update confirmation, installs the
  update and restarts Housekeeper into the new version.
- Detect how the running copy was installed. RPM installations on supported Fedora
  releases update in the app; Flatpak, Snap, DEB, Pacman, APK, source checkouts and
  unidentified installations are not updated in the app, and Details on the Updates
  page explains how to update them, with a command to copy.
- Leave Housekeeper out of the Updates list and its count of apps that need their
  own updater, and keep Housekeeper's package out of other apps' update
  transactions.
- Refresh the README screenshots.
