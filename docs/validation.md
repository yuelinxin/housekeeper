# v0.1.16 development validation

## v0.1.16 responsive inventory refresh — September 18, 2026

- Python, Meson, RPM, AppStream and changelog metadata target v0.1.16 / RPM release 1.
  AppStream validation passes with no network access.
- The Fedora 43 and 44 offline containers each pass 538 unit tests, with one real dpkg
  fixture skipped because dpkg-query is unavailable, then build, install, replace and
  remove the RPM. Ruff lint and formatting pass; strict mypy targets are unchanged by
  this release.
- Service tests cover the queueing contract directly: a preview or batch queued behind
  a scan runs against the inventory that scan publishes, a second concurrent management
  operation is still refused, a cancellation requested while queued withdraws the work
  before any provider is contacted, and a queued icon change is refused when the
  completed scan no longer lists the launcher the window validated.
- GTK smoke on Fedora 43 and 44 confirms details, launching, update checks, list
  refreshes and update confirmations all remain available during a background scan,
  that a queued operation shows a usable Cancel, and that an inventory arriving
  mid-operation defers its Updates reconciliation until that operation ends.
  Automatic refresh coalescing, the settle delay, and the successful and failed scan
  cooldowns are exercised against an injected clock rather than real time.
- Both confirmation dialogs were inspected as rendered: the removal dialog stays
  compact with its exact targets folded into Details, and the update dialog's Details
  still carries the transaction list, installation and target.
- The real transaction suite passes on Fedora 44 in the disposable container,
  including a cancelled system Flatpak extra-data download and a real GIO Trash
  removal with application data preserved. Native RPM removal authorization remains
  unverified because the container backend cannot provide a safe preview.
- No personal installed applications were launched, removed, or updated during
  validation. Automatic refresh never reads a package transaction that is still
  writing, so a scan cannot report a half-applied state as the inventory.

## v0.1.15 Flatpak data removal — September 13, 2026

- Python, Meson, RPM, AppStream and changelog metadata target v0.1.15 / RPM release 1.
- The Fedora 44 offline container passes 530 unit tests, with one real dpkg fixture
  skipped because dpkg-query is unavailable. Ruff lint/format and strict mypy pass.
- Real Flatpak fixtures on Fedora 43 and 44 verify default data preservation,
  explicit deletion, and preservation of unrelated data and symlink targets.
  The Fedora 43 fixture also seeds permissions and verifies that default uninstall
  preserves them and explicit deletion resets only the selected application's permissions.
- GTK smoke passes on Fedora 43 and 44, covering the default Keep User Data switch,
  both choices, cancellation, and omission of that control for other providers.
  The rendered confirmation was inspected at its compact width.
- The Ubuntu 24.04 runtime-floor container passes 507 unit tests, with 24 tests
  skipped for absent optional Flatpak/RPM bindings. GTK smoke and five fresh SVG
  icon startups also pass without those providers.
- User-data size scanning is intentionally deferred. Storage still reports only
  software size. No personal installed applications were removed during validation.

## v0.1.14 application details — September 13, 2026

- Python, Meson, RPM version/release, AppStream, changelog, and installation examples
  target v0.1.14 / RPM release 1.
- The Fedora host unit suite passes 512 tests in an isolated D-Bus session; the real
  dpkg fixture skips because dpkg-query is unavailable. Restricted-sandbox SVG decode
  failures disappear when the same tests run in the isolated session.
- GTK smoke passes with synthetic inventory data under Xvfb. It covers Open for all
  source categories, exact desktop-entry routing, launch failures, multiple-launcher
  selection and cancellation, and applications without a launcher. No real apps are
  launched or removed by these UI checks.
- Available-update badges follow installation identity and update-list changes.
  Light/dark screenshots verify the badge beside the application name; narrow
  layouts retain non-overlapping, keyboard-focusable action buttons.
- Ruff lint and formatting pass. Source icon lookup was checked against installed
  copies at multiple sizes and scale factors in Adwaita and hicolor.

## Runtime floor test isolation — September 12, 2026

- GitHub Actions run 34676319325 passed the Fedora 43/44 and static jobs but
  failed 20 Flatpak update unit tests on Ubuntu 24.04. The transaction fixture
  still used the real Flatpak loader for running-instance discovery.
- The fixture now provides an empty instance list through its fake Flatpak
  backend. The dedicated running-instance tests continue to override that list.
- The complete Ubuntu 24.04 baseline check passes without Flatpak: 507 unit tests
  pass and six RPM-binding tests skip as expected. GTK smoke and five fresh SVG
  icon startups also pass. Production code and runtime-floor dependencies are unchanged.

## v0.1.13-2 COPR packaging — September 12, 2026

- COPR build 10978836 failed during the Meson GTK import check. Its dependency
  list omitted `gobject-introspection`; removing that package in a disposable
  Fedora 44 container reproduced the missing `cairo-1.0` typelib error.
- The spec now requires `gobject-introspection` for building and running the app,
  plus `flatpak-libs` and `python3-rpm` for the package tests. The first minimal
  build exposed 20 Flatpak test failures without these test dependencies.
- Complete RPM builds, the unit suite, desktop-file validation, and AppStream
  validation pass on Fedora 43 and 44 containers with only declared build
  dependencies and RPM build tooling installed, with weak dependencies disabled.
  Builds ran as an unprivileged user with networking disabled. The unchanged
  0.1.13 source archive was packaged using the revised external spec.
- RPM and SRPM artifacts are in `dist/copr/` with release `2`. These are local
  verification results; the corrected package still needs a new COPR submission.

## v0.1.13 update disclosure — September 11, 2026

- Python, Meson, the RPM spec and changelog, the newest AppStream release, changelog
  heading and README installation example agree on 0.1.13.
- The offline Fedora 44 unit suite passes 503 tests, skipping one real dpkg fixture
  because dpkg-query is absent. Ruff lint and formatting and the configured strict
  mypy check pass.
- Fedora 44 GTK smoke passes. `check_size_sorting` and the screenshot helper are flaky
  on a live compositor rather than under Xvfb; five consecutive runs passed after the
  competing capture processes stopped. This flake predates these changes and reproduces
  on an unmodified tree.
- The Flatpak sandbox comparison was checked against a real pending update: Housekeeper
  reported `sockets: x11`, matching what `flatpak update` prints for the same commit.
  Process detection was checked against real running applications and cost 117 ms across
  a 27,777-path transaction.
- Real RPM update execution was not run on a personal system. Its integration coverage
  lives in the disposable container and was not rerun for this release. No RPM package
  build, hosted CI run, or release was triggered.

# v0.1.12 development validation

Updated on September 11, 2026. These are local development checks, not a claim that
the complete Fedora Workstation release acceptance matrix has been signed off.
No personal application was used as an update or removal target. Earlier dated
sections below preserve the results and limitations known at each stage.

## v0.1.12 final review — September 11, 2026

- Python, Meson, the RPM spec and changelog, the newest AppStream release,
  changelog heading and README installation example agree on 0.1.12. The source
  archive uses `housekeeper-0.1.12/`; release workflows derive the Python version.
- Meson compilation, offline AppStream validation and desktop-file validation pass.
  The development launcher reports `Housekeeper 0.1.12`.
- Ruff lint and formatting, the configured seven-module strict mypy check with a
  fresh analysis, and whitespace checks pass.
- The offline Fedora 44 unit suite passes 483 tests, skipping one real dpkg fixture
  because dpkg-query is absent. Ubuntu passes 478 tests, including that isolated
  dpkg fixture, and skips six tests requiring unavailable RPM Python bindings.
- Ubuntu GTK smoke passes in 32.37 seconds; its 1,000-record filtering measurement
  is 76.62 ms. It covers completed-update cache boundaries and restoration,
  shared-launcher disclosure, version text, plural forms and centered Details.
- Fedora 44 GTK smoke passes in 32.35 seconds with filtering at 62.91 ms.
  The final collapsed and expanded dialog screenshots were visually checked;
  the disclosure arrow and label remain centered in both states.
- These are local checks of the release working tree. Real RPM/Flatpak transaction
  integration and RPM package builds were not rerun for this final review, and no
  hosted CI run or release was triggered.

## CI gate repair — September 9, 2026

Read the last five Validate runs from GitHub Actions. Every failed job in these
runs stopped at the default mypy check or the 100 ms filtering assertion:

| Run | Static check | Ubuntu filtering | Fedora 43 filtering | Fedora 44 filtering |
|---|---|---|---|---|
| [v0.1.7](https://github.com/yuelinxin/housekeeper/actions/runs/34215612224) | Pass | 95.31 ms | 127.66 ms | 143.56 ms |
| [v0.1.8](https://github.com/yuelinxin/housekeeper/actions/runs/34291686467) | Pass | 69.34 ms | 121.59 ms | 151.48 ms |
| [v0.1.9](https://github.com/yuelinxin/housekeeper/actions/runs/34312941979) | Pass | 105.60 ms | 109.13 ms | 151.97 ms |
| [v0.1.10](https://github.com/yuelinxin/housekeeper/actions/runs/34407967156) | 348 errors | 109.89 ms | 127.80 ms | 126.74 ms |
| [v0.1.11](https://github.com/yuelinxin/housekeeper/actions/runs/34420095532) | 325 errors | 110.50 ms | 129.70 ms | 90.19 ms |

Times at or above 100 ms failed the old assertion. The configured seven-module
strict check now uses `follow_imports = "silent"`, retaining imported type
information while scoping diagnostics to those modules. Plain `mypy` passes;
a temporary shadow-file probe returning a string from an `int` function still
fails with `return-value`, confirming the checked modules remain enforced.

Filtering still asserts the expected result count and reports elapsed time and
RSS, but has no machine-dependent latency gate. Local offline Fedora 43 and Ubuntu
GTK runs pass (47.91 ms and 43.79 ms respectively), along with Ruff and whitespace
checks. These validate the working-tree repair; no new hosted CI run was triggered.

## v0.1.11 metadata validation — September 9, 2026

- Python, Meson, RPM spec, the newest AppStream release, changelog, validation
  heading and README installation example agree on 0.1.11. Source archives and
  release workflows derive their version from the Python package.
- Meson compilation, offline AppStream validation and desktop-file validation pass;
  the development launcher reports `Housekeeper 0.1.11`.
- All 30 update-cache tests pass after the version change. Ruff lint, formatting
  and whitespace checks pass. Feature validation below covers update discovery,
  dependency filtering and the compact confirmation dialogs.

## Compact update confirmation — September 9, 2026

- Fedora 44 synthetic GTK smoke passes with both update entrypoints using the
  compact confirmation, Details initially collapsed, exact operations and
  authorization guidance retained, launcher aliases excluded from headings, and
  existing cancellation/execution flows intact.
- Inspected rendered Cursor examples in collapsed, expanded, batch and narrow
  layouts. The RPM version summary omits release/epoch clutter only when the
  upstream version changes; a synthetic revision-only update retains full versions.
- Ruff lint, formatting and whitespace checks pass. Screenshot fixtures and tests
  do not invoke real update providers. Log:
  `/tmp/housekeeper-update-dialog-smoke.log`; screenshots:
  `/tmp/housekeeper-update-dialog-shots/`.

## Desktop update discovery and filtering — September 9, 2026

- Fedora 44 disposable unit suite: 460 passed, 1 skipped (`dpkg-query` absent).
  The host sandbox blocked eight unrelated image-decoder tests; all passed inside
  the container. Update, batch, cache and service tests passed on the host as well.
- Query-count fixtures verify one PackageKit refresh and one update query for 101
  desktop records, and one Flatpak discovery per installation. Only matching
  candidates receive previews. These are call counts, not elapsed-time benchmarks.
- The offline Fedora 44 integration suite passes signed RPM application/dependency
  updates, a shared-dependency batch, Flatpak user/system updates, stale-plan and
  authorization rejection, and cancellation of a real extra-data download. The
  extended fixture publishes a newer shared runtime without changing either app;
  neither app appears in the update list or receives an available individual plan.
  Native removal authorization retains the existing unsupported-preview limitation.
- Synthetic GTK smoke passes, including update cache restoration and batch completion.
  Its RPM fixtures now use exact package name/architecture identities.
- Ruff lint, formatting and whitespace checks pass. The unchanged host update cache
  was inspected read-only: ten previous rows become eight, excluding the hidden
  LibreOffice start center and XSLT filters while preserving Writer, Calc and Impress.
- The seven configured core modules pass mypy with `--follow-imports=silent`.
  Default mypy also follows existing untyped provider/inventory modules and is not
  clean: the unmodified HEAD produces 348 errors. New API annotations reduce this
  to 325, with no additional diagnostic kinds/counts per file relative to HEAD.
  The comparison used mypy 2.3.1 in a temporary compatible Python environment;
  no type-check configuration was relaxed in the repository.
- Logs: `/tmp/housekeeper-update-integration-44.log` and
  `/tmp/housekeeper-update-smoke-44.log`. No host applications were updated,
  no host cache was rewritten, and no release was built or published.

## v0.1.10 metadata validation — September 9, 2026

- Python, Meson, RPM spec, the newest AppStream release, changelog and README
  installation example agree on 0.1.10; release metadata is dated September 9.
- Meson compilation, offline AppStream validation and desktop-file validation pass.
  The development launcher reports `Housekeeper 0.1.10`.
- All 28 update-cache tests pass after the version change; whitespace checks pass.
  The feature validation below remains applicable. No package artifacts or remote
  release were published as part of this version metadata update.

## Effective D-Bus service review — September 9, 2026

The P1 review was reproduced: a higher-priority user service using `flatpak run
--system` did not prevent attribution to the user installation selected by the
desktop fallback. Four independent service mutations (target, contents, new
override, symlink destination) also passed the old revalidation. All five new
regressions failed before the fix.

The fix resolves the service with standard session precedence, compares its full
installation/ref and entry point with the installed export, and binds both service
snapshots into the shared authorization evidence. Tests also cover runtime filename
rules, alternate service filenames, duplicate claims, malformed/unsupported files,
missing targets, wrappers, systemd delegation, branch/architecture and named
installation selectors, and both removal/update transaction entrypoints.

- Host full suite: 444 passed, 1 skipped (dpkg-query absent). Ubuntu 24.04 full
  suite: 439 passed, 6 skipped (optional RPM bindings absent).
- Strict mypy now includes the new service resolver (seven modules); Ruff checks
  and whitespace validation pass.
- Fedora 44's disposable integration suite passes. Its Flatpak fixture uses a
  session bus with standard service directories, verifies that a newly effective
  service invalidates an existing removal preview, rejects a system-scope override,
  then successfully previews/uninstalls the normal user fixture. Application data
  remains intact; no fixture application service is activated.
- A read-only host rescan still gives one verified Flatpak record each for Gapless,
  Backups, Decoder, Switcheroo and Varia. Personal applications were not changed.
- Native RPM removal retains the previously documented unsupported-backend limit.
  Detailed logs: `/tmp/housekeeper-dbus-service-integration.log` and
  `/tmp/housekeeper-dbus-service-ubuntu.log`.

## Duplicate D-Bus Flatpak entries — September 9, 2026

The previous scan changes rejected every `DBusActivatable=true` launcher before
checking its deployment export. A real Flatpak installation then appeared alongside
its unassociated Other launcher. The read-only host scan reproduced this for
Gapless, Backups, Decoder, Switcheroo and Varia. After the fix each has one Flatpak
component, and all five pass fresh ownership revalidation. The remaining two
Document Viewer entries belong to distinct installed packages, Papers and Evince.

- Five new assertions failed against the old implementation before the fix. Coverage
  now checks official export symlinks, icon-only overrides, changed desktop IDs,
  activation flags, commands, labels, desktop actions, working directories, missing
  exports, update/removal revalidation, and distinct D-Bus component identities.
- Host unit tests: 415 passed, 1 skipped (dpkg-query absent). Ubuntu 24.04 disposable
  container: 410 passed, 6 skipped (optional RPM bindings absent).
- Ruff lint/format, configured strict mypy and whitespace checks pass.
- Fedora 44's disposable integration suite passes. Its real Flatpak fixture now
  exports a D-Bus desktop entry and service; the scanner returns one component,
  accepts its icon-only copy, rejects a changed/tag-only copy, and successfully
  previews/uninstalls the fixture while preserving its application data. No fixture
  application or D-Bus application service is activated during verification.
- The existing RPM removal preview limitation remains unchanged and is not counted
  as a successful uninstall. Logs are at `/tmp/housekeeper-dbus-integration.log`
  and `/tmp/housekeeper-dbus-ubuntu.log`. Personal applications were only read.

## Scan attribution audit fixes — September 8, 2026

The audit regressions were reproduced before implementation. The final source was
validated in disposable Fedora 43, Fedora 44 and Ubuntu 24.04 containers, with the
checkout mounted read-only. No personal applications were transaction targets.
See [the implemented ownership rules](scan-attribution.md).

| Environment | Unit tests | Build and GTK smoke |
| --- | --- | --- |
| Fedora 43 | 403 passed, 1 skipped (dpkg-query absent) | Passed |
| Fedora 44 | 403 passed, 1 skipped (dpkg-query absent) | Passed |
| Ubuntu 24.04 | 398 passed, 6 skipped (optional RPM bindings absent) | Passed, including real dpkg attribution |

- Ruff lint, formatting and whitespace checks pass. Strict mypy covers six modules:
  models, identity, launch parsing, attribution, Flatpak launch attribution and
  AppImage header identification.
- GTK smoke verifies component browsing, conflict records with management/update
  instructions, blocked direct callbacks, narrow layouts, update previews and
  refresh/cache flows. Smoke took approximately 31.6 seconds on each runtime.
  The 1,000-record filter took 39.88 ms (Fedora 43), 56.46 ms (Fedora 44) and
  49.19 ms (Ubuntu), with about 182 MiB peak RSS. Existing deprecation/rendering
  warnings remain non-fatal; these synthetic measurements are not idle app memory.
- Both Fedora transaction suites pass signed RPM updates and no-update checks,
  denied authorization, and real two-application updates with shared dependencies.
- Both suites pass association of a real Flatpak-exported desktop entry, verification
  of an equivalent user copy, and rejection after its command changes or it contains
  only an unrelated X-Flatpak tag. No fixture launch command is executed.
- Real user Flatpak preview/uninstall, application/runtime updates, shared-runtime
  batches, changed dependency rejection and retained user data pass. System Flatpak
  tests pass helper authorization, denied/stale-plan rejection, exact commits and
  cooperative cancellation during a real extra-data download.
- Real GIO Trash moves only the temporary format-identifiable AppImage and its
  launcher while retaining unrelated application data.
- Native RPM removal remains unavailable on these tested backends. Fedora 43 rejects
  `allow_deps=False`; Fedora 44 does not provide a safe single-package simulation.
  The tests confirm refusal and preservation of the fixture package. Native removal
  authorization is **not verified** by these runs.
- Package artifacts were not released and the application version is unchanged.
  Detailed run logs and screenshots were written to
  `/tmp/housekeeper-validation/{43,44,ubuntu}/` on the development host.

## v0.1.9 validation — September 8, 2026

- Application, Meson, RPM spec, latest AppStream release, changelog and README
  installation example agree on 0.1.9. Meson compilation, offline AppStream and
  desktop-file validation pass; the desktop file retains its optional category
  hint. The development launcher reports `Housekeeper 0.1.9`.
- Source archive generation passes; the archive prefix uses 0.1.9 and includes
  the refreshed README screenshots.
- The system-Python unit suite passes 330 tests with one skip because dpkg-query
  is unavailable. Ruff lint/format, configured mypy checks and whitespace checks pass.
- The full synthetic GTK smoke suite passes on an isolated Xvfb display in
  31.61 seconds. The 1,000-record filter takes 59.13 ms with 189.4 MiB peak RSS.
  Earlier desktop-session runs were interrupted by selection and rendering
  assertions; the isolated run avoids interference from desktop input.
- Focused GTK checks verify search reveal/clear and focus, header order at normal
  and 360-pixel widths, refresh actions on both pages, default-on automatic refresh,
  cancellation of queued automatic requests without losing manual requests,
  startup loading with automatic refresh disabled, and hidden-icon opacity in both
  views including restoration when records change. GTK parses the Preferences
  accelerator as Ctrl+comma.
- README list and grid screenshots are recaptured from the current UI using
  synthetic application records. No personal application is updated or removed.
- RPM/SRPM packaging, real package-manager transactions and the full Fedora 43/44
  desktop acceptance matrix are not repeated for this UI release preparation.

## v0.1.8 metadata validation — September 8, 2026

- Application, Meson, RPM spec, latest AppStream release, changelog and README
  installation example agree on 0.1.8; release metadata is dated September 8.
- Meson compilation and offline AppStream validation pass. Desktop-file validation
  passes with the existing optional Settings-category hint. The development launcher
  reports `Housekeeper 0.1.8` without opening a window.
- The full local unit suite passes again after the version change: 330 passed and
  one optional dpkg-query skip. Ruff lint/format, configured mypy checks (the model
  and identity modules), and whitespace checks pass.

## v0.1.8 behavior validation — September 8, 2026

- Local unit tests pass: 330 passed, one skipped because dpkg-query is unavailable.
  Cache regressions cover single and batch completion, partial failures,
  cancellation, inventory reconciliation, restart persistence, and incomplete
  reports. RPM fixtures cover file contents, symlinks, shared dependencies,
  D-Bus activation, ambiguous ownership, and changed targets.
- Isolated GTK smoke passes with synthetic records, including retained update
  lists and instructions-only RPM actions. The 1,000-record search completed in
  approximately 51 ms with 189 MiB peak RSS; the smoke run took approximately
  32 seconds. No real update or removal provider was invoked.
- A read-only host check verifies the RPM attribution of LibreOffice Calc,
  Impress and Writer, GNOME Maps, and GNOME Weather. Among visible system RPM
  launchers, 50 verify successfully; GNOME Software retains its RPM source with
  instructions because systemd-delegated activation is not supported by the verifier.
- These checks do not replace the full Fedora 43/44 release acceptance matrix.
  No new RPM/SRPM build or real package-manager transaction was performed for this
  release preparation. Broader discovery and provider generalization is deferred.

## v0.1.7 metadata validation — September 8, 2026

- Application, Meson, RPM spec, latest AppStream release, changelog and README
  installation example agree on 0.1.7; release metadata is dated September 8.
- Meson compilation, offline AppStream validation and desktop-file validation pass.
  The development launcher reports `Housekeeper 0.1.7` without opening a window.
- All 23 update-cache tests pass with the new application version. Ruff lint/format
  and whitespace checks pass. RPM packages and release attachments were not built
  as part of this metadata update.

## v0.1.7 update preferences validation

- The offline Fedora 44 container passes 289 unit tests, with one optional
  dpkg-query skip. Coverage includes weekly expiry, checked-provider cache matching,
  legacy cache compatibility, and provider selection through the service and batch.
- Isolated GTK smoke passes manual-only entry and explicit refresh, deferred-entry
  cancellation, weekly expiry, persisted preference controls, source switching,
  both sources disabled, and rejection of late results after a source change.
- Ruff lint/format, configured mypy, Meson build and whitespace checks pass.
  No real application was checked for updates or modified by these tests.

## v0.1.7 Flatpak Last Updated validation

- The offline Fedora 44 container passes 278 unit tests, with one optional
  dpkg-query skip. GTK smoke passes in an isolated Xvfb display, including the
  Last Updated label and existing sorting, filtering and selection checks.
- Journal fixtures cover default/user/named installations, another user's history,
  exact refs and commits, removal/reinstall boundaries, invalid timestamps, missing
  commands, timeouts and bounded output. No journal permissions were changed.
- A read-only host inventory matches current deployment dates for 15 of 19 Flatpak
  apps and retains dates for 123 RPM apps. Four Flatpak dates remain unknown. The
  full inventory took approximately 0.38 seconds and reported no provider warnings.
- Ruff lint/format, configured mypy, Meson build and whitespace checks pass. The
  final Last Updated sort popup was visually reviewed.

## Initial sorting validation — September 7, 2026

- A fresh build in the existing offline Fedora 44 container passes 262 unit tests;
  one test skips because dpkg-query is unavailable. Sorting coverage includes numeric
  ties, unknown values, package timestamp parsing, and inventory metric attribution.
- GTK smoke passes in an isolated Xvfb display, covering both views, remembered
  settings, selection after a rendered frame, filters, refreshes and a narrow sort
  popup. Synthetic screenshots were visually reviewed. The 1,000-record search
  check completed in 58 ms with approximately 180 MiB peak RSS.
- Ruff lint/format, configured mypy checks, and whitespace checks pass. A read-only
  host inventory obtained sizes and installation times for 123 RPM apps, sizes for
  19 Flatpak apps and one AppImage, with no provider warnings.
- Installation dates remain unknown outside RPM, Pacman and Snap. These checks do
  not extend the existing update/removal or platform acceptance guarantees.

## v0.1.6 release validation — September 7, 2026

- Application, Meson, RPM spec, latest AppStream release, README installation
  example and source archive agree on 0.1.6. The source archive includes the new
  storage and DEB, Pacman/APK, and Snap adapters.
- Fedora 44 offline container: a fresh Meson build and its unit-test target pass;
  offline AppStream and desktop metadata validation pass, and the development
  launcher reports `Housekeeper 0.1.6`.
- The full suite passes 240 tests with one optional dpkg-query skip. GTK smoke
  passes with source-filter and storage checks, stable progress-window dimensions,
  and no Cancel sensitivity changes during backend phase transitions. Clicking
  Cancel disables it exactly once; late callbacks do not re-enable it.
- Storage and attribution use synthetic Pacman/APK databases and a temporary Snap
  Unix socket. The real dpkg-query fixture and GTK checks passed on Ubuntu during
  DEB integration. Actual Arch/Alpine desktops and live snapd remain unverified.
- Ruff lint/format, mypy, whitespace and version-consistency checks pass. The new
  progress window was visually reviewed with synthetic messages.
- This commit does not repeat the full Fedora 43/44 RPM installation/removal and
  real update-transaction release matrix. No personal application was installed,
  updated or removed during these checks.

## v0.1.5 release validation — September 7, 2026

- Application, Meson, RPM spec, latest AppStream release and README installation
  example agree on 0.1.5. Source archives derive their version from the application.
- Clean source builds, unit tests, GTK smoke and five fresh SVG startup checks
  pass in Fedora 43 and 44 offline containers. RPM/SRPM builds, offline AppStream
  and desktop metadata validation, installation, same-version replacement and
  removal all pass; installed launchers report `Housekeeper 0.1.5`.
- Ubuntu 24.04 baseline: clean build, 167 tests with one optional RPM-binding skip,
  GTK smoke and five fresh SVG startup checks pass. Existing GTK allocation/toast
  width warnings remain in synthetic narrow transitions.
- Ruff lint/format, mypy, whitespace checks and README image links pass. Screenshots
  were regenerated at desktop and narrow sizes using synthetic records and read-only
  system icons. List, grid, app details, Appearance and Updates were visually reviewed.
- Removed the standalone README updating guide. This release includes launcher icon
  customization/restoration, GNOME refresh guidance, per-distribution source labels
  and scrolling-content padding. DEB and other new labels do not imply new providers.
- No personal application was installed, updated, removed or used as a screenshot
  fixture during release validation. Package operations used disposable containers.

## Native source labels — September 7, 2026

- Fedora 44: 168 unit tests and GTK smoke pass. Ubuntu baseline: 167 tests pass,
  one optional RPM-binding test is skipped, and GTK smoke passes.
- The second sidebar row is RPM on Fedora and DEB on Ubuntu. Unsupported native
  categories explain their status and remain selected across inventory refresh.
  Synthetic RPM records retain RPM labels and filtering on both environments.
- Ruff lint/format, mypy and whitespace checks pass. Existing baseline GTK
  allocation/toast width warnings remain in synthetic narrow transitions.

## Appearance changes — September 7, 2026

- Fedora 44: 149 unit tests pass; clean Meson build and GTK smoke pass, including
  temporary launcher image selection, serialized save, inventory refresh and reset.
- Conservative Ubuntu/GTK baseline: 147 tests pass with one optional RPM-binding
  skip before the final comment-preservation regression was added. The clean build
  and extended GTK smoke pass. Existing allocation/toast width warnings remain in
  synthetic narrow transitions; the details page was visually inspected.
- Ruff lint/format, mypy and whitespace checks pass. Theme changes update the
  visible rows. The synthetic appearance screenshot is saved in `docs/screenshots`.
- Appearance now contains the icon theme or custom image, plus explicit launcher
  GTK overrides, and sits at the bottom. Icon paths are in Technical Details.
  Global GTK/cursor settings and inferred runtime styles are omitted. Icon changes affect desktop launchers;
  GNOME Shell refresh and icons of already-running windows still need manual desktop
  verification. No personal launcher was changed during validation.

## v0.1.4 release validation — September 7, 2026

- Application, Meson, RPM, AppStream, and source archive versions agree on 0.1.4.
  Offline AppStream validation, Ruff lint/format, mypy, and whitespace checks pass.
- Fedora 43 and 44 offline containers pass clean source builds, Meson tests, GTK
  smoke, and repeated SVG startup checks. RPM and SRPM builds, installation,
  same-version replacement, version output, and removal pass on both releases.
- The system Python suite passes 136 tests. The conservative Ubuntu runtime passes
  135 tests with one optional RPM-binding skip, and its clean build and GTK smoke pass.
- GTK coverage includes cache expiry, cancellation retaining previous results,
  the adaptive footer, and list/grid and pill-button hover in both color schemes.
  Grid selection plus hover/pressed states are exercised explicitly so the outer
  highlight cannot be missed by testing only unselected cards.
- No personal application was updated or removed. This release changes caching
  and presentation; existing real-transaction validation and its limitations below
  still apply.

## Update cache validation — September 7, 2026

- Updates header layout passes GTK smoke on the conservative Ubuntu runtime at
  normal, 1200-pixel wide, and 360-pixel narrow window sizes. Buttons remain compact
  at the bottom right and stack at narrow sizes, with selection status at the bottom
  left. Footer bounds are checked against the list and window edges; check metadata
  and Details remain visible.
  Updated screenshots use synthetic records.
- The system Python suite passes 136 tests, including cache round trips, empty
  results, changed installation paths/versions, incomplete checks, malformed files,
  invalidation, unavailable cache storage, and the exact 24-hour expiry boundary.
- Clean Meson tests and synthetic GTK smoke pass on Fedora 44 and the conservative
  Ubuntu runtime. GTK checks verify repeated navigation preserves selection without
  another check, new pages restore saved results, and the refresh button checks again.
  The Ubuntu smoke also verifies empty-result restoration and suppression of a queued
  first check when saved results exist. Smoke uses a temporary isolated cache.
- Fedora 44 GTK smoke also covers expired-cache entry, deferral during inventory
  scans, unchanged inventory without automatic checks, expired empty results after
  restart, and leaving the page before a deferred check starts. Successful checks
  renew the 24-hour TTL; revisiting within that interval reuses the results.
- Cancellation regression smoke clicks the real Cancel button with both empty and
  partial provider results. The previous rows, checkbox selection, status, cached
  file, and successful-check time remain unchanged; the progress window closes and
  actions become available again. A prior successful empty result is also retained.
- Ruff lint/format, mypy, and whitespace checks pass. These tests use synthetic
  update reports and do not update any installed application.

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
transaction's `GCancellable`. The UI sends one request, keeps further clicks disabled
through late progress callbacks, and waits for installed-state verification before closing.
The Cancel label stays unchanged and no intermediate cancellation-request notice is shown.

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
