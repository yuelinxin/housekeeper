# Changelog

## 0.3.4 — 2026-10-25

- Explain how to install an RPM file that PackageKit's dnf5 backend refuses as
  unauthenticated, instead of showing the backend's raw error. That backend refuses
  every local package file unless Allow Unverified Package is on.
- Add an Allow Unverified Packages preference, under Installing Apps, that starts the
  Allow Unverified Package switch on for every package file (off by default). Each
  install still asks for administrator authorization.
- Add a Move Installed Files to Trash preference, under Installing Apps (off by
  default). After a package, Flatpak or AppImage file installs successfully, the file
  you opened goes to the Trash, unless it changed during installation.
