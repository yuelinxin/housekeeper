# Plasma virtual-machine validation — November 15, 2026

This is a real KVM guest with SDDM, Plasma, KWin, a session bus, KDE portals and
the KDE Polkit agent. It extends the container checks with desktop interaction.
It is Ubuntu with Plasma packages installed, not a stock Kubuntu image, and is
partial acceptance rather than certification of every item in
[the desktop checklist](desktop-acceptance.md).

## Reproducible environment

| Component | Tested configuration |
| --- | --- |
| Guest | Ubuntu 24.04.5 LTS, amd64, kernel 6.8.0-147-generic |
| Desktop | plasma-workspace 5.27.12; KWin X11/Wayland 5.27.11; SDDM 0.20.0 |
| GTK / libadwaita | GTK 4.14.5 / libadwaita 1.5.0 |
| PackageKit | 1.2.8-2ubuntu1.5, APT backend |
| KDE Polkit / portal | 5.27.11 / xdg-desktop-portal-kde 5.27.11 |
| Flatpak | 1.14.6 installed; transactions not tested here |
| Virtual hardware | KVM/q35, host CPU, 4 vCPUs, 4 GiB RAM, 28 GiB qcow2 overlay, OVMF, virtio VGA at 1440×900 |
| Networking | QEMU user networking; SSH forwarded only on 127.0.0.1:22224 |
| Console | QEMU VNC Unix socket, noVNC 1.7.0 and websockify on 127.0.0.1:6084 |
| Account | Disposable `hk-test`, normal desktop user with administrator authorization |

The base is the [official Ubuntu Noble cloud image](https://cloud-images.ubuntu.com/noble/current/),
checked against its published SHA256SUMS. The image used has SHA256
`04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94`.
The moving download URL alone does not reproduce this image.

Cloud-init installs `kde-plasma-desktop`, `plasma-workspace-wayland`, `sddm`,
`polkit-kde-agent-1`, `xdg-desktop-portal-kde`, `kde-config-gtk-style`,
`gsettings-desktop-schemas`, PackageKit and its Python GI binding, Flatpak and its
GI binding, AppStream and its GI binding, and the built Housekeeper DEB. Refresh
APT metadata after installing the desktop integrations. SDDM autologin uses
`plasma.desktop` for X11 or `plasmawayland.desktop` for Wayland. The VM has its own
disk and generated SSH key/password; no host home or source checkout is mounted.
Provisioning uses guest sudo; graphical PackageKit operations still require the
normal KDE password prompt. No permissive Polkit test rule is installed.

The server image initially left Ethernet unmanaged by NetworkManager, so
PackageKit reported offline despite APT networking working. Configure Netplan to
use `NetworkManager` for the guest Ethernet device and override the cloud image's
`unmanaged-devices` setting before testing installation. Verify `nmcli general
status` reports `connected / full`. This was a guest setup correction; PackageKit
network checks were not bypassed. A powered-off disk/firmware baseline was saved
before the first package transaction; it predates this network correction.

The tested source is the working tree based on commit
`877b5932767c2c9f6ebc3a69817166b6b33156fb`, including the unreleased DEB packaging
changes and subsequent non-application desktop-entry fix. The final tested files:

| Artifact | SHA256 |
| --- | --- |
| `housekeeper-0.3.6.tar.gz` | `dcc89c3dca6d1b7948cb2773492ab1e1e584144935b872d0abd1b8cd1410dd4f` |
| `housekeeper_0.3.6-1+ubuntu24.04_all.deb` | `dd703a0d5f0b56b7225a5bfdfa97bd3214c8d2de95083732349a24bdf213f70f` |

These hashes identify the test build, not a published release. Documentation
written after the run is not part of that source archive.

## Results

| Check | X11 | Wayland |
| --- | --- | --- |
| Real active local Plasma session | Pass (`loginctl`) | Pass (`loginctl`, KWin Wayland) |
| Launch from KDE search, correct app icon and one running panel item | Pass | Pass |
| Inventory and native DEB attribution | Pass; 21 visible apps after installing KCalc | Pass; same inventory |
| List rendering | Pass | Pass |
| Grid rendering | Not exercised | Pass |
| Search, details, native version/size | Pass; KCalc 4:23.08.5-0ubuntu4, 2991 KiB | Not separately exercised |
| Launch KCalc from details | Pass | Not exercised |
| Repository installation with KDE Polkit cancellation | Pass; KCalc remained absent | Not exercised |
| Repository installation after KDE password authorization | Pass; dpkg confirmed KCalc installed | Not exercised |
| KDE portal file picker and custom SVG icon | Pass | Not exercised |
| KDE search launcher icon refresh and restoration | Pass, without restarting Plasma | Not exercised |
| GNOME theme setting unchanged, no GNOME theme layer | Pass; `breeze` before/after | Not separately exercised |
| Reinstall fixed Housekeeper DEB and reopen | Pass | Fixed package started successfully |

Housekeeper's Wayland process connected directly to `/run/user/1000/wayland-0`:
the client socket and KWin peer inode pair were matched with `ss -xnp`. This is
stronger evidence than changing `XDG_CURRENT_DESKTOP` or running GTK under Xvfb.

The initial X11 inventory displayed a warning for `org.kde.kded5.desktop`.
That file intentionally declares `Type=Service`. Discovery now skips
non-application entries without treating them as unreadable applications, while
preserving desktop-ID precedence and reporting malformed files. The warning
disappeared after installing the rebuilt DEB. Regression tests cover services,
links, and a non-application override masking a lower-priority application.

Both Ubuntu 24.04 and Debian 13 rebuilt packages pass 776 unit tests (six RPM-only
tests skipped), both desktop-identity GTK smoke runs, five SVG startup runs, and
install/replacement/purge checks after the fix. Ruff and strict mypy pass.

The successful KCalc transaction ran before the discovery-only fix was installed.
The rebuilt package was used for the warning recheck, icon change/restoration and
Wayland checks. Authentication cancellation currently reports PackageKit's raw
failure message; it does not falsely report a successful install. The VM's
software graphics produced an EGL/DRI3 acceleration warning under X11 but rendered
successfully. Remote keyboard input required explicit Shift handling for symbols;
this was a console-input issue, not an authentication bypass.

## Follow-up: missing and inconsistent UI icons

The actual Breeze theme lacked `package-x-generic-symbolic`,
`application-x-addon-symbolic`, `applications-games-symbolic`,
`web-browser-symbolic` and `software-update-available-symbolic`. GTK resolved
these requests to its missing-image placeholder. Other action icons resolved to
Breeze artwork, explaining the visible difference from GNOME.

All 35 application-owned symbolic UI icons now have private
`housekeeper-…-symbolic` GResource aliases, including six directional variants.
The Adwaita artwork subset is bundled with source, attribution and license texts.
GTK still recolors the symbols for light/dark appearance and selection. Inventory
application icons and toolkit-provided window decorations keep their normal
desktop behavior; the desktop's icon theme is not overridden.

On the same real Plasma Wayland guest, the installed package resolves all 35 names
to symbolic SVGs inside Housekeeper's GResource. The DEB and Updates sidebar icons,
toolbar and preference icons were visually checked, and the theme remained
`breeze`. The automated GTK smoke also checks resource origin, symbolic rendering,
16/32-pixel lookup, LTR/RTL variants, Adwaita/Breeze/Breeze Dark and an empty theme
without filesystem search paths; its existing light/dark UI checks still run.

The GUI-tested DEB SHA256 is
`146f30801842dddfd62f2ac639c7053443361e514d3ad08ab5a8f6944368688c`;
a copy is kept at `work/kde-vm/evidence/housekeeper-ui-icons-tested.deb`.
Evidence includes `breeze-ui-icons-before.txt`, `breeze-ui-icons-after.txt`,
`ui-icons-before.jpg` and `ui-icons-after-light.jpg` in the same directory.
`ui-icons-smoke-dark.png` records the automated GTK dark-mode fixture, whose
symbol recoloring was also visually checked; it is not a real desktop inventory.
The VM was shut down after this follow-up. Its obsolete
`systemd-networkd-wait-online` service was also disabled because NetworkManager
now manages the guest Ethernet link.

## Remaining acceptance

Stock Kubuntu and Debian Plasma images, Plasma 6, GPU acceleration, fractional
scaling, mixed-DPI displays, accessibility and full keyboard navigation remain
untested. So do pin/unpin and running-window custom icons, Wayland installation
authorization and icon changes, local DEB opening/drag-and-drop, repository toggles,
Flatpak transactions, AppImages, browser launchers and uninstall/data preservation
in this desktop VM. Native DEB update/removal remains external.

## Local continuation

The ignored `work/kde-vm/` directory holds the disk, firmware, provisioning data,
private test credentials, control scripts, build logs and `evidence/` screenshots
and text records. Do not commit its credentials or disks. From the repository:

```sh
work/kde-vm/start.sh
# Only if the existing console bridge is not running:
work/kde-vm/console.sh
# When finished:
work/kde-vm/stop.sh
```

Open [the local console](http://127.0.0.1:6084/vnc.html?autoconnect=true&resize=scale).
The guest is currently shut down and configured to start in Wayland. Restarting it may require
clicking Connect in noVNC. The local scripts depend on this machine's QEMU/OVMF
installation and `.tools/bin/websockify`; they are not a cross-host VM installer.
The guest password is in `work/kde-vm/test-password`, and `ssh-config` contains
the connection settings. Stopping the VM releases its 4 GiB RAM allocation.
