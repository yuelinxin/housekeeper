# Application discovery and ownership

The September 2026 scan audit was reproduced against `53bcaa1` using temporary
launchers and fake package/transaction backends. An unrelated `X-Flatpak` launcher
could reach a simulated uninstall; changing native provider order changed the
selected source; empty XDG variables omitted default roots; common `env` options
were unrecognized; browser recognition was limited to the documented browsers;
and an ordinary script with an AppImage suffix passed format classification.
No personal application was updated or removed during the audit.

## Data flow and private interfaces

Discovery reads one effective set of desktop IDs in XDG priority order, including
provider-supplied roots. Missing or empty XDG values use specification defaults.
Hidden and malformed overrides cannot be resurrected by a supplementary root.
The optional early UI snapshot has no direct actions and is never mutated later.

`LaunchSpec` preserves command arguments, environment assignments, unsets and
clearing, wrappers, and unsupported-context explanations. Resolution applies the
effective PATH. Parsing never executes the application, evaluates shell code, or
expands shell substitutions. The recognized env wrapper must resolve to the system
command. `env -S`, nested env, shell evaluation, relative PATH contexts, and changes
to installation/activation environment variables remain manual.

The private discovery adapter exposes contexts, roots, capabilities, independent
ownership candidates, and file-ownership queries. `AttributionCandidate` and its
evidence are immutable. Provider order has no role in choosing ownership:

- A launcher owner takes precedence over a command-only owner.
- Multiple installation claims at the same level remain a conflict, even if one
  provider has more complete content verification.
- An unavailable applicable backend retains other evidence but disables direct
  management; it is not a negative ownership answer.
- A browser, interpreter or other host is not the installation of its guest app.

`AppComponent` describes a visible component and its equivalent launchers.
`InstallationInstance` includes the provider and installation context; package
versions and Flatpak commits are mutable state. `ManagementTarget` identifies the
actual package, ref or file. `AppRecord` is the UI projection. Components such as
Writer and Calc stay separate while sharing a transaction target when appropriate.
Independent official Flatpak installation rows do not invent a launcher relation.

Only confirmed equivalent launches in one installation are merged. Update batches
group by installation, execute a shared target once, and report every completed
component key. Ownership states and candidates are available in Technical Details;
previews display the installation context and actual target.

## Flatpak authorization

`X-Flatpak`, path containment, and uniqueness alone do not authorize association.
The command must resolve to the system Flatpak executable and parse as a supported
`flatpak run`. Only options before the ref select an installation, architecture or
branch. Explicit selectors and the user-first rule participate in resolution;
unresolved installations and non-current defaults remain manual.

A simple direct invocation with only desktop field-code arguments can identify an
installation. Custom commands and application arguments additionally require an
equivalent launcher in the current deployment's export. Icon-only overrides retain
their launch relationship. Guest browser/Steam launchers never inherit the host's
uninstall action. D-Bus activation is not inferred from the fallback Exec command.
For D-Bus launchers, ownership requires a matching current deployment export with
the same desktop ID and every desktop key unchanged except the icon and Housekeeper's
icon bookkeeping. This associates official exports and icon-only overrides with
their installation instead of displaying a spurious Other row beside it. Renamed
D-Bus launchers are not equivalent: the desktop ID determines the activation name
([Desktop Entry specification](https://specifications.freedesktop.org/desktop-entry/latest/dbus.html)).
Component signatures include that ID, so distinct D-Bus components sharing an Exec
fallback stay separate. This establishes ownership, not successful D-Bus activation;
no application or service is started during verification.
Wrong labels cannot rename, hide, or confer permissions on the official installation.
Hidden desktop-ID overlays affect visibility separately from ownership.

Every direct update/removal entrypoint rechecks the launcher relationship and exact
installed target. Removal/update plans bind the component key, installation ID,
target ID and launch evidence. The normal transaction fingerprints, commit checks,
dependency restrictions, and supported-platform policy still apply. A shared update
dependency exception cannot waive changed ownership evidence. Cache schema 2
invalidates previous previews rather than migrating their authorization.

These checks detect changes; they do not lock the host package databases or files
against concurrent changes throughout an entire transaction.

## AppImage files

Identification uses a bounded, nonblocking read of a regular file's ELF header and
AppImage type marker. A suffix is neither sufficient nor required. Header detection
does not authenticate the publisher or validate the entire embedded filesystem.
Tests use deliberately non-runnable, format-identifiable binary fixtures.

Trash eligibility separately requires a directly referenced, user-owned regular
file in the user's home, no symbolic-link ambiguity, unchanged launchers and file
snapshots, and no other inventory entries sharing the target. Format and ownership
are checked again before removal. Failure to trash the executable keeps its launchers.

File queries distinguish owned, confirmed unowned, unavailable/error, and not
applicable. RPM and dpkg query exact ownership; Pacman and APK include all package
files and refuse incomplete ownership databases. Flatpak and Snap installation
boundaries and Snap exports remain managed files. All applicable queries must
complete negatively to permit Trash. Queries are cached within a snapshot and
refreshed for execution. Unsupported management systems are not newly implemented.

## Deliberate limits

Direct RPM operations retain the tested Fedora 43/44 and transaction-backend limits.
A custom RPM database cannot target the host PackageKit backend.
DEB, Pacman, APK and Snap continue to use external management. Additional browsers,
general interpreter/systemd validation, Nix/Guix, arbitrary containers, and a full
Desktop Entry Exec parser are separate extensions. No native package inventory
without desktop launchers is added by this change.
