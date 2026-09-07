# v0.1.3 development validation

Updated on September 7, 2026. These are local development checks, not a claim that
the complete Fedora Workstation release acceptance matrix has been signed off.
No personal application was used as an update or removal target. Earlier dated
sections below preserve the results and limitations known at each stage.

## v0.1.3 packaging validation — September 7, 2026

- Application, Meson, RPM spec, and AppStream versions agree on 0.1.3. AppStream
  validates offline, and source archive names and prefixes use the application version.
- Fedora 43 and 44 offline containers pass clean builds, Meson tests, synthetic GTK
  smoke, and five fresh SVG icon startups. RPM and SRPM builds, package installation,
  same-version replacement, version output, and removal pass on both releases.
- The conservative Ubuntu 24.04 runtime passes 118 unit tests with one optional
  RPM-binding skip, clean builds, GTK smoke, and five SVG icon startups.
- Ruff lint/format, mypy, shell syntax, and whitespace checks pass. The exact minimum
  runtime and graphical authentication-agent checks remain open as described below.

## Flatpak download cancellation — September 7, 2026

The update provider previously marked every execution progress callback non-cancellable,
including extra-data downloads. It now exposes cooperative cancellation through the
transaction's `GCancellable`. The UI sends one request, keeps its cancelling indicator
through late progress callbacks, and waits for installed-state verification before closing.

- 119 unit tests, clean Meson tests, GTK smoke, Ruff and mypy pass.
- The offline Fedora 44 system Flatpak fixture cancels an actual slow localhost extra-data
  transfer before the payload finishes. The app commit and personal data remain unchanged;
  already completed components are retained and reported as partial results.
- The existing system authorization, stale commit, RPM update, Flatpak batch and removal
  regression fixtures still pass with the earlier documented removal limitations.

## System Flatpak authorization fix — September 7, 2026

A reported WeChat failure exposed a system-installation gap in the original user-scope
fixtures. Explicit commit requests fail for unprivileged system clients before normal
update authorization. System updates now resolve through the normal helper path and
compare the complete confirmed plan at `ready`; user installations keep commit pinning.

- Fedora 43 and 44 offline containers reproduce the exact specific-commit root-permission
  error. Signed system Flatpak fixtures then verify successful helper-mediated updates,
  unchanged app commits after Polkit denial or stale-plan rejection, and retained data.
- The system Python suite passes 117 tests; clean Meson tests, GTK confirmation smoke,
  Ruff and mypy pass. Authorization notices, waiting-for-auth progress, scoped commit
  arguments and rotating diagnostics are covered.
- These system-installation tests extend the earlier user-only results below. They use
  disposable allow/deny rules; actual graphical password/fingerprint prompts and named
  custom installations remain desktop acceptance work. No personal WeChat was updated.

## Updates page and batch validation — September 7, 2026

- The system Python suite now passes 111 tests. Ruff lint/format, mypy, and diff
  whitespace checks pass. Added cases cover installation grouping, cancelled and
  incomplete checks, service exclusivity, submission failure, shared dependencies,
  and rejection of unapproved changes between batch transactions.
- Clean Meson builds/tests and synthetic GTK smoke pass on Fedora 44 and the
  Ubuntu 24.04 GTK baseline (110 unit tests plus one optional RPM-binding skip).
  Smoke verifies the pinned bottom Updates row, selected/all update confirmation,
  cancellation, partial results, navigation, checkbox focus, and narrow layout.
  Screenshots use synthetic apps; smoke never performs a real update.
- Offline Fedora 43 and 44 integration suites pass real signed two-app RPM batches
  sharing a dependency and real two-app Flatpak batches sharing a runtime. Installed
  RPM versions and Flatpak commits are verified after execution. The existing
  update authorization, stale-preview rejection, data preservation, and removal
  regression fixtures continue to pass with their documented backend limitations.
- Batch execution is sequential and can stop after partial completion. There is no
  batch-wide rollback. Actual graphical Polkit prompts and system/custom Flatpak
  installations still require the desktop acceptance checks below.

## Update feature validation — September 7, 2026

The application update feature was checked separately from the original removal
release. No personal application was updated or removed.

- The expanded Fedora unit suite passes 99 tests. Ruff lint/formatting and strict
  mypy checks pass. Update cases include exact package identity, dependency/source
  changes, stale PackageKit inventory, cancellation, partial completion, and service
  lifecycle failures. Without optional provider bindings, the Ubuntu suite passes
  98 tests and skips only the real RPM version-comparison test.
- Fedora 43 and 44 disposable containers both update a signed local application RPM
  and its dependency from version 1 to version 2, verify the installed versions,
  report no remaining update, and reject a separately denied authorization request.
  These are real **update** results; the existing native **removal** preview
  limitations below still apply.
- Local Flatpak fixtures update both an application and its runtime, verify target
  commits and stable inventory identity, preserve an application-data marker, and
  reject a stale preview after another runtime commit is published. User scope is
  verified; system/custom installation authorization remains a desktop acceptance task.
- Clean Meson builds and synthetic GTK smoke cover adjacent update/removal buttons,
  narrow stacked layout, confirmation and cancellation, stale callbacks, deferred
  refresh, and operation-window lifetime. Fedora 43/44 and the conservative Ubuntu
  24.04 environment without optional provider bindings pass these checks.
- Fedora 43/44 clean source archives build RPM and SRPM packages successfully. The
  packaging harness passes installation, same-version replacement, launcher checks,
  and removal, as well as GTK smoke and the SVG startup regression checks.
- Screenshot additions use synthetic inventory: application details, narrow details,
  and the update confirmation dialog. The interface does not check for updates while
  scanning inventory or merely opening a detail page.

The disconnected DNF5 fixture needs PackageKit's test daemon to preserve
`GIO_USE_NETWORK_MONITOR=base`; otherwise DNF5 refuses even uncached local `file://`
package payloads in cache-only mode. The harness uses `--keep-environment` for this
purpose. External networking remains disabled, and production daemon settings are
untouched. See the testing guide for the harness details.

Graphical Polkit dialogs, screen-reader interaction, the exact minimum runtime
combination, and real system/custom Flatpak update authorization are not claimed
by these container checks. PackageKit preview and execution are separate transactions;
the feature verifies the plan again but does not provide an atomic cross-transaction
lock or rollback of partially completed updates.

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
