# Changelog

## 0.3.6 — 2026-11-10

- Support Fedora 45. Checking for updates, and updating, installing and removing
  system packages, were turned off there because RPM management only allowed Fedora
  43 and 44.
- Notice software source changes between an update preview and the update on Fedora
  45, which keeps its repositories in /usr/share/dnf5/repos.d, and in dnf5 repository
  overrides and configuration drop-ins.
