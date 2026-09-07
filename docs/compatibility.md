# Compatibility

## Runtime floor

Python 3.10, GTK 4.12, libadwaita 1.4, and PyGObject are required. Both Wayland and
X11 are intended targets. A GNOME Shell process is not required. Core discovery
uses XDG and freedesktop conventions rather than GNOME Shell internals.

| Environment | Inventory | Native package removal |
| --- | --- | --- |
| Fedora Workstation 43 / 44 | Supported | Implemented; tested backends currently require manual guidance |
| Other ordinary RPM distributions | RPM metadata when bindings are available | Manual in v0.1 |
| Ubuntu / Debian | Desktop entries and installed optional integrations | DEB provider planned |
| Arch / openSUSE | Desktop entries and installed optional integrations | Broader native adapters planned |
| OSTree / bootc / NixOS | Available desktop entries and optional integrations | Host changes remain manual |
| Missing optional provider | Other providers remain usable | Affected operation is unavailable |

This table describes intended behavior; the independently recorded checks in
[validation](validation.md) distinguish tested environments from future targets.
An RPM file alone does not make a distribution supported for native removal.

The tested Fedora 43 PackageKit/DNF backend rejects `allow_deps=false`. The tested
Fedora 44 PackageKit/DNF5 backend reports success with an empty removal preview for
the fixture package. Housekeeper refuses execution in both cases and shows a
source-specific command for review. Passing these fallback tests is not evidence
of a successful real RPM removal or graphical Polkit authentication.

## Source-specific details

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

## Expansion policy

First add DEB ownership and validate PackageKit on Ubuntu and Debian. Then add
Arch and openSUSE native adapters. Immutable and declarative environments need their
own host-layer semantics. Capability checks and fixture-based removal tests are
required before enabling an operation on a new platform.

A future Flatpak distribution of Housekeeper needs a separate host-access design
and review. The v0.1 RPM build is not a sandboxed frontend. Older GNOME installations
below the runtime floor require a suitable bundled runtime or a later compatibility
project; supporting GNOME does not imply every historical library version is supported.
