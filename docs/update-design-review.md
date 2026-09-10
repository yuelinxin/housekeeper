# Desktop application update review

Reviewed 2026-09-09 against the GNOME Software main-branch source and the installed
LibreOffice launchers. Upstream links below follow main and can change.

## Findings

Housekeeper previously called `prepare_update` for every eligible installation.
Each Flatpak check resolved a new transaction. RPM shared its source refresh, but
still called PackageKit `get_updates` for every application. The cost therefore
grew with installed applications, even when most had no update.

GNOME Software's [Flatpak update collection](https://github.com/GNOME/gnome-software/blob/main/plugins/flatpak/gs-flatpak.c)
uses `gs_flatpak_add_updates` and `flatpak_installation_list_installed_refs_for_update`
per installation. Its `get_real_app_for_update` can present an extension's update
under its parent application's name. Its [PackageKit plugin](https://github.com/GNOME/gnome-software/blob/main/plugins/packagekit/gs-plugin-packagekit.c)
uses `pk_client_get_updates_async` to collect updates for an update-list request.
The plugin also includes background preparation of updates. Thus comparing the
time to open Software with a fresh Housekeeper check is not a controlled benchmark.

The [libflatpak API](https://docs.flatpak.org/en/latest/libflatpak-api-reference.html#flatpak-installation-list-installed-refs-for-update)
can return apps needing only missing related refs or runtimes. App-kind filtering
alone does not prove that the application itself has a newer commit. A resolved
transaction must include a change to that application's full ref and commit.

The installed `/usr/share/applications/libreoffice-xsltfilter.desktop` declares
`NoDisplay=true`, matching [LibreOffice's auxiliary launcher](https://github.com/LibreOffice/core/blob/master/sysui/desktop/menus/xsltfilter.desktop).
Housekeeper already preserved this visibility information in inventory, but the
update checker ignored it. The Fedora start-center launcher is also hidden; Writer,
Calc and Impress provide the visible desktop applications. Package names and display
names are not used as a blacklist for identifying auxiliary components.

## Implemented behavior

- Query candidates once per Flatpak installation and once per RPM check; only
  matching desktop candidates receive complete individual transaction previews.
- Honor inventory visibility on the Updates page. Auxiliary and hidden entries
  remain available through the inventory's existing controls and source manager.
- Require an application's own commit or package version to change before listing
  an update. Shared runtimes and extensions alone do not create application rows.
- Preserve all required dependencies in the confirmed transaction. A hidden row
  does not disable a dependency update needed by Writer, Calc or another app.
- Apply the same row rules when loading older cached results. Keep source failures,
  cancellation, exact ownership checks and execution-time validation separate from
  a successful empty result.

For N applications, K candidates and I Flatpak installations, discovery changes
from N per-app checks to I Flatpak queries or one PackageKit query, followed by K
full previews. This particularly benefits mostly current inventories. An inventory
where every application needs an update still pays for individual previews. These
are needed by the current confirmation and dependency-validation contract. This
change does not claim measured parity with GNOME Software or add background network
work. System-wide maintenance remains the responsibility of the system manager.
