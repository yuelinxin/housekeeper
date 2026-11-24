# Native desktop acceptance

Run these checks in disposable virtual machines with a normal, unprivileged
desktop account. Container checks do not validate Plasma panels, window grouping,
file-manager integration or graphical Polkit dialogs. An `XDG_CURRENT_DESKTOP=KDE`
GTK smoke run tests the application's fallback, not a running KDE desktop.

## Targets and record

| Target | Session | Status |
| --- | --- | --- |
| Ubuntu 24.04.5 + Plasma 5.27 (KVM) | X11 | Installation, Polkit and launcher icons pass; [partial acceptance](plasma-vm-validation.md) |
| Ubuntu 24.04.5 + Plasma 5.27 (KVM) | Wayland | Native startup, inventory and rendering pass; [partial acceptance](plasma-vm-validation.md) |
| Kubuntu 24.04 / Plasma | Wayland | Pending desktop acceptance |
| Kubuntu 24.04 / Plasma | X11 | Pending desktop acceptance |
| Debian 13 / Plasma | Wayland | Pending desktop acceptance |
| Ubuntu 24.04 / GNOME | Wayland | Pending desktop acceptance |
| Arch Linux / Plasma 6 | Wayland | Container build/runtime passes; real session pending |
| Arch Linux / GNOME | Wayland | Container build/runtime passes; real session pending |
| Arch Linux / Hyprland | Wayland | Pending; needs a running Polkit agent such as `hyprpolkitagent` |

Record the commit, package filename and SHA256, distribution version, desktop version,
session type, PackageKit backend/version, Flatpak version, and each result below.
Mark an unavailable integration as not tested, rather than passed. Use a VM
snapshot before any package transaction; do not use the developer's installed apps.

## Install and desktop entry

1. Install the matching distribution's DEB with `sudo apt install ./housekeeper_*.deb`.
   Verify `housekeeper --version`, then open Housekeeper from the application menu.
2. Verify a single menu entry with the correct icon, window title, taskbar grouping,
   and no extra terminal. Pin, close and reopen the application from the panel.
3. Exercise list/grid views, search, keyboard shortcuts, preferences, light/dark
   appearance, scaling and a narrow window. Check readable text and missing icons.
4. Open a known native application from its details. Compare its DEB identity,
   version and declared installed size against `dpkg-query`.
5. Confirm that DEB update/removal and Housekeeper's self-update direct the user to
   the system package manager, without offering an RPM transaction.

## Launcher icons on Plasma

For Arch, first perform a full system upgrade, then install the tested `.pkg.tar.zst`
with `pacman -U`. Compare installed ownership, version, size and date with `pacman -Qi`
and `pacman -Qo`. Native search should show the Pacman source; installation must show
external-manager guidance without starting a PackageKit transaction. A Flatpak
alternative should remain installable. Native updates/removal and local pacman
package installation stay external. Apply the desktop/launcher checks below as
usual; the DEB-specific transaction checks do not apply to Arch.

1. Pick a disposable application with a desktop entry and change its icon. Confirm
   the menu launcher uses the new icon after the menu refreshes.
2. Check pinned and running taskbar icons separately. Record whether they refresh,
   need unpinning/reopening, or keep a separate icon. Panel-wide replacement is
   not currently promised outside GNOME.
3. Restore the original icon and check that an unrelated application is unchanged.
4. If `org.gnome.desktop.interface` is installed, record `gsettings get
   org.gnome.desktop.interface icon-theme` before and after the change. It must
   remain unchanged in Plasma. No GNOME icon theme layer should be created.

## Installation and authorization

1. Search for a small application in the configured distribution repositories.
   Check source, installed status and app metadata. Cancel one installation at the
   authentication dialog and confirm it is not reported as installed.
2. Install that application with the desktop's normal Polkit dialog. Verify dpkg
   state and refresh Housekeeper's inventory. Remove it externally afterward.
3. Open a downloaded, disposable `.deb` through Dolphin/Files and through
   Housekeeper's picker; also drag it onto Install Apps. Confirm the local details
   and any unverified-package prompt before proceeding. Verify both refusal and
   explicit consent paths; do not treat a backend's unsupported role as a pass.
4. Review native software sources. Test toggling only a fixture repository in the
   VM, then restore it. Unsupported backend operations must explain the limitation.
5. With a test Flatpak remote, check user and system installation, update preview,
   update, and uninstall. Confirm authentication cancellation and keeping data by
   default; use only disposable data to check explicit data deletion.

## Local integrations and package lifecycle

1. Import a test AppImage and verify its menu entry, icon, launch and Trash action.
   Supply the runtime/FUSE dependencies it needs separately.
2. If native Chrome/Chromium is installed, create a website launcher, open it, check
   window grouping and remove it. Browser profile data must remain.
3. Install the DEB again; verify preferences and custom launcher icons persist.
4. Remove Housekeeper with `sudo apt remove housekeeper`; its menu entry must
   disappear. Existing user-created launchers and user data must remain.

Report failures with the relevant Housekeeper log excerpt and desktop/backend
versions. Do not mark the platform fully supported until its required checks pass.
