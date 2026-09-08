# Compatibility

## Runtime floor

Python 3.10, GTK 4.12, libadwaita 1.4, and PyGObject are required. Both Wayland and
X11 are intended targets. A GNOME Shell process is not required. Core discovery
uses XDG and freedesktop conventions rather than GNOME Shell internals.

| Environment | Inventory | Native package removal |
| --- | --- | --- |
| Fedora Workstation 43 / 44 | Supported | Implemented; tested backends currently require manual guidance |
| Other ordinary RPM distributions | RPM metadata when bindings are available | Manual in v0.1 |
| Ubuntu / Debian | DEB ownership, versions and software sizes through local dpkg-query; optional integrations | External system package manager |
| Arch / Manjaro | Pacman ownership, versions and software sizes; optional integrations | External system package manager |
| Alpine | APK ownership, versions and software sizes; optional integrations | External system package manager |
| openSUSE | RPM metadata when bindings are available; optional integrations | External system package manager |
| OSTree / bootc / NixOS | Available desktop entries and optional integrations | Host changes remain manual |
| Missing optional provider | Other providers remain usable | Affected operation is unavailable |

This table describes intended behavior; the independently recorded checks in
[validation](validation.md) distinguish tested environments from future targets.
An RPM file alone does not make a distribution supported for native removal.
The sidebar's second category is named for the host's native package family,
even when that provider is not yet implemented. Unsupported categories explain
the limitation and do not relabel unknown or RPM-owned applications as DEB,
Pacman, or another native format. Only verified inventory populates source filters.

The tested Fedora 43 PackageKit/DNF backend rejects `allow_deps=false`. The tested
Fedora 44 PackageKit/DNF5 backend reports success with an empty removal preview for
the fixture package. Housekeeper refuses execution in both cases and shows a
source-specific command for review. Passing these fallback tests is not evidence
of a successful real RPM removal or graphical Polkit authentication.

## Source-specific details

- Pacman reads installed ALPM `desc`/`files` records, including AUR builds installed
  through Pacman. `pacman-conf` supplies custom database and installation roots.
- APK reads `/lib/apk/db/installed`; downloaded repository indexes and package archive
  sizes are not used. Broken package records are excluded.
- Snap reads active app revisions through snapd's local `/v2/snaps` endpoint. The
  daemon's desktop-file paths establish ownership. The reported snap size excludes
  data, base snaps, shared runtimes, and inactive revisions. Exported desktop entries
  are discovered even when the Snap desktop directory is missing from XDG_DATA_DIRS.
- Nix, Guix, Portage, XBPS and eopkg do not yet have software-size adapters. Their
  native category labels do not imply package attribution or size support.
- DEB identifies installed owners of desktop files, including verified icon overrides.
  Software size uses the package's declared installed size; unowned launchers and
  ambiguous or diverted files remain unknown. No APT metadata refresh is needed.
- Flatpak enumerates user, system, and named system installations. Different branches
  and installation scopes remain distinct. Runtime refs are not listed as apps.
- Chrome and Chromium web apps retain the profile and explicit data directory from
  their entry. Other Chromium derivatives and sandboxed browser wrappers are manual
  unless their launch structure can be identified without ambiguity.
- PWAsForFirefox entries are identified by their `firefoxpwa site launch` command.
- AppImage trash currently depends on available RPM ownership evidence. On systems
  without that evidence, file locations remain available for manual review.
- An entry supplied by an unsupported manager is still shown. Removing a shortcut
  alone is never presented as uninstalling the application.
- Hidden and auxiliary entries are opt-in. Explicit hidden overrides continue to
  suppress matching synthetic Flatpak entries.

## Application updates

RPM update capability is checked independently of removal capability. Fedora 43
and 44 ordinary systems use PackageKit to refresh metadata and simulate an exact
application update with its dependencies. Unsupported roles, incomplete previews,
stale installed metadata, extra removals, or downgrades produce management guidance.
The RPM database is checked independently of PackageKit's cached installed inventory.
Immutable/declarative systems and other distributions retain external native management.
Both Fedora backends pass signed local application/dependency upgrades and denied
authorization fixtures; these successful update tests do not change the removal
limitations documented above.

Flatpak update previews require libflatpak 1.9.1 or newer. User, system, and named
installations retain their own paths, refs, branches, and origins. Real local update
fixtures currently exercise user scope; system and custom installation authorization
still require desktop acceptance checks. Missing optional bindings do not prevent
startup or source-specific instructions.

Single-app and selected/all-app batches use the same provider restrictions. Only
explicitly requested application updates are supported. Dependencies and
extensions can be installed or updated after preview; new source configuration,
application migration, runtime cleanup, release upgrades, and automatic restart are
outside this feature. An application with no configured update source cannot be
updated from its package file or release website automatically. AppImage, web apps,
Steam games, and Housekeeper itself provide update instructions.

## Expansion policy

First add DEB ownership and validate PackageKit on Ubuntu and Debian. Then add
Arch and openSUSE native adapters. Immutable and declarative environments need their
own host-layer semantics. Capability checks and fixture-based removal tests are
required before enabling an operation on a new platform.

A future Flatpak distribution of Housekeeper needs a separate host-access design
and review. The v0.1 RPM build is not a sandboxed frontend. Older GNOME installations
below the runtime floor require a suitable bundled runtime or a later compatibility
project; supporting GNOME does not imply every historical library version is supported.

System Flatpak updates use the standard system-helper path: Flatpak rejects explicit
commit requests from an unprivileged client before normal update authorization.
Housekeeper verifies every resolved commit against the preview before deployment;
changed targets still require another preview. User installations retain explicit
commit pinning. Polkit handles system authorization with the desktop agent and its
configured password/fingerprint methods; system policy may authorize without prompting.
