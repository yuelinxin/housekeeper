# Changelog

## 0.2.0 — 2026-09-24

- Add an Install Application dialog for native packages and Flatpak applications.
  Search configured sources by app name or keyword using AppStream metadata,
  review the exact source and installation scope, and select a result to install
  with its dependencies. Reuse installed system Flatpak runtimes when possible.
- Add Software Sources to Preferences. List and enable or disable native and
  Flatpak repositories, import Flatpak sources from a local file or HTTPS URL,
  and review their installation scope and signature-verification setting before
  adding them. Keep update-source preferences on the same page.
- Import local AppImages into ~/AppImages and create desktop launchers without
  executing the imported file. Keep the original download and allow an optional
  name and custom icon. Snap and Steam installation open their external stores.
- Create website launchers for Chrome or Chromium with an optional name and icon.
  Retrieve the website's icon during installation, fall back when it is unavailable,
  and store normalized images locally. Match Chromium's Wayland and X11 window
  identities so the website window can use its own launcher, name and icon.
- Uninstall verified Housekeeper website launchers by moving their desktop files
  to Trash after rechecking ownership and contents. Preserve browser data and icons;
  externally created shortcuts and browser-installed PWAs retain browser management.
- Keep installation and source changes on the existing cancellable management
  worker, discard obsolete search results, and refresh inventory after operations.
  Add unit, GTK and isolated integration coverage for installation, software sources,
  icon handling and website launcher creation and removal.
