# v0.1 development validation

Recorded on September 6, 2026. These are local development checks, not a claim that
the complete Fedora Workstation release acceptance matrix has been signed off.
No personal application was used as a removal target.

## Automated checks

| Check | Result |
| --- | --- |
| Unit suite | 52 tests pass, including XDG overrides, identities, transaction boundaries, and partial Trash failures |
| Ruff lint and formatting | Pass |
| Strict mypy checks | Pass for core models, provider protocol, and identity logic |
| Desktop entry and AppStream validation | Pass without network access |
| Fedora 43 and 44 clean source builds | Pass in disposable containers |
| Fedora 43 and 44 GTK interaction smoke | Pass with Xvfb and an independent D-Bus session |
| Fedora 43 and 44 RPM/SRPM packaging | Build, installation, same-version replacement, command lookup from another directory, and removal pass |
| Ubuntu 24.04 without RPM/PackageKit/libflatpak bindings | Build, unit tests, and GTK interaction smoke pass on Python 3.12.3, GTK 4.14.5, and libadwaita 1.5.0 |
| Fedora 44 native desktop | Synthetic GTK smoke and read-only real inventory startup pass |

The Ubuntu runtime checks older GTK/libadwaita APIs, but does not by itself prove the
exact Python 3.10, GTK 4.12, and libadwaita 1.4 minimum combination. The app requires
that API floor; a dedicated minimum-version environment remains a release check.
Package replacement is not a two-version upgrade test: no earlier release exists.
GitHub workflows are prepared; these results come from local runs of their scripts.

## Real operations in disposable environments

Both Fedora test environments use an unprivileged fixture user, their own system
D-Bus, and their own Polkit policy. The source checkout is mounted read-only.

- A locally exported Flatpak app is previewed and uninstalled; its application-data
  marker remains. No runtime is downloaded and the fixture application is not run.
- Real GIO Trash moves an exact temporary AppImage and desktop entry; an unrelated
  configuration marker remains. The corresponding files exist in the user's Trash.
- Native RPM preview falls back safely and the fixture remains installed. The Fedora
  43 DNF backend rejects `allow_deps=false`. The Fedora 44 DNF5 backend reports an empty
  simulated operation list. Housekeeper refuses both previews.

The RPM execution path and changed-target/cascade/error boundaries have mock-based
tests. **Successful real RPM removal and its authorization flow are not verified on
these backends.** Do not describe the fallback integration test as an uninstall test
that succeeded. Real Flatpak tests cover user scope; system/custom-installation
transactions still need independent acceptance tests.

Container Flatpak triggers may report that bubblewrap cannot mount `/proc`. This
does not execute the fixture app. Installation, preview, ref removal, and data
preservation are checked explicitly rather than inferred from log messages.

## Inventory and performance observations

The reference desktop uses Fedora 44, Python 3.14.7, GTK 4.22.4, and libadwaita 1.9.3.
Its current effective GIO launcher list has 78 visible desktop IDs. All 78 appear in
Housekeeper, with no missing or extra visible IDs. The inventory contains 162 logical
records including hidden entries, and reported no provider warnings. No application
names or personal paths are stored in test fixtures or screenshots.

| Measurement | Observed value |
| --- | --- |
| First frame, including Python GTK imports | 0.441 seconds |
| Fully enriched inventory ready | 0.564 seconds |
| Standalone metadata scan | 0.479 seconds |
| Search over 1,000 synthetic records | 75.96 milliseconds |
| Real inventory startup peak RSS | 252.4 MiB |
| Synthetic UI smoke peak RSS | 176.2 MiB |

These are individual warm development measurements, not statistical guarantees.
Startup timing excludes process creation. Peak RSS includes GTK, icons, and graphics
resources and is not idle private memory. The synthetic run includes screenshots,
several windows, and 1,000 records. Record comparable measurements on the same runtime
before interpreting future changes as regressions. No periodic full-disk scan exists.

## Remaining public-release checks

- Fedora Workstation 43/44 virtual machines: real authentication-agent dialogs,
  keyboard-only usage, Orca, fractional scaling, and both Wayland and X11 sessions.
- Actual Chrome/Chromium profiles, the PWAsForFirefox extension, and Steam handoff
  with explicit manual verification that the intended manager/profile opens.
- System and custom-installation Flatpak removal with real authorization.
- A supported PackageKit backend that supplies and enforces a single-package plan,
  before advertising direct RPM removal as validated.
- The exact minimum runtime combination, genuine upgrade from an earlier package
  once one exists, and review of draft release attachments before public publishing.
