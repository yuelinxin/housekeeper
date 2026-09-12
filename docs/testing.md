# Testing and releases

## Local tests

Removal tests request cancellation before client setup, during the RPM handshake,
and inside RPM/Flatpak backend calls; synthetic backends must receive cancellation
without reporting completed removals. DEB tests report both metadata and ownership
query failures as UNAVAILABLE attribution for every launcher of a scan while running
dpkg-query once per index; only the fresh index of a later scan retries. Ownership
tests distinguish all-inapplicable databases from missing or failed queries, including
the default AppImage preview/execution path with a simulated Trash callback.

RPM activation tests use the canonical D-Bus resolver to reject inaccessible,
oversized, malformed, duplicate, or swapped service overrides and enforce exact
filenames in the runtime directory. GTK smoke excludes hidden and uncheckable
launchers from update summaries and checks singular update/unsupported-app text;
plural messages use gettext plural selection.

Inventory regressions require merged records to own copies of entries from every
input, including entries appended to an existing group. A partial callback may
change its snapshot without affecting reused scanner entries or final records.
Hidden-overlay lookup uses exact desktop IDs and leaves provider records unchanged.
RPM removal revalidates ownership once during its fresh execution preview; stale
launcher and non-host-database tests supply valid plans and still stop before any
PackageKit call. GTK smoke checks centered disclosure geometry in both states,
full-width expanded contents, and alias deduplication in the visible summary. The
disclosure reports its expanded state as a tristate integer, never a bool. Batch
checks name every launcher of an installation once, the checked application first.

Scan audit regressions live in `test_scan_regressions.py`, `test_attribution.py`,
`test_flatpak_attribution.py`, `test_launch.py`, and `test_file_ownership.py`.
They cover provider permutations, conflicting owners, changed installation selectors,
current deployment exports, spoofed command names, XDG defaults, environment lookup,
format-identifiable AppImage fixtures, and unavailable versus negative file queries.
Batch/cache tests verify component completion, evidence-bound previews and rejection
of schema 1; cached plans round-trip their sandbox permission and in-use process
disclosures, and a plan missing one is ignored rather than restored as harmless. GTK smoke
checks that conflict records cannot invoke direct operations.

`test_processes.py` builds a synthetic procfs to check that executables and mapped files
are separated, that a replaced binary still running is matched by path, and that a denied
or exited process is skipped instead of counted as idle. It uses absent entries rather
than permission modes, which root would bypass. RPM update tests cover the preview's
in-use lists, that they stay out of the fingerprint, that quitting a program before
updating still executes, and that a program started after the preview stops execution.
Flatpak tests cover instance disclosure without blocking, and ignore other refs. GTK
smoke checks that the card carries only the action, that the paths appear in Details
with a bounded remainder, that a running Flatpak is disclosed without a quit
instruction, and that it keeps the neutral accent while permissions and replaced files
take the warning accent. The disposable RPM container additionally resolves real package file
lists and detects a real running process.
The AppImage transaction fixture remains confined to the disposable container.


`test_rpm_attribution.py` uses temporary files and synthetic installed RPM metadata to
verify launcher/entry-point digests, symlink targets, shared command dependencies,
dependency versions and architectures, PATH shadowing, D-Bus service ownership and
user precedence, and provider revalidation after a launcher changes. Unverified apps
retain their RPM source but cannot enter update batches or direct management. Dependency
comparison cases use the optional RPM Python bindings; other cases run without them.

Update preference regressions cover daily/weekly expiry boundaries, cache source
matching (including older caches), and skipping disabled providers while passing
the complete inventory to enabled providers. GTK smoke exercises manual entry,
manual refresh, deferred-entry cancellation, weekly checks, source switches,
both sources disabled, late results after a source change, and Preferences controls.

GTK smoke checks that single and batch update confirmations start with Details
collapsed, show short summaries, retain complete transaction and authorization
information (including installation and target) in the expander, and omit launcher
aliases from the heading while showing them in the visible summary. Both batch and
details-page confirmations disclose shared installations. The list and dialog use
the same version text for RPM versions, packaging-only revisions, Flatpak commits,
and missing primary changes. The expanded preview is captured separately from the
default compact dialog. Cache tests bound snapshot serialization to once per app
per load, including inventories with many apps and only one pending update.

`test_sorting.py` checks numeric order, unknown and zero values, deterministic ties,
timestamp validation, RPM metadata reuse and Flatpak installation separation. Package
fixtures verify Pacman and Snap dates and keep unsupported dates unknown. GTK smoke
exercises the settings action, both views, filters, selection, inventory refreshes,
and the sort menu at 360 pixels wide. At that width, the external view buttons
must remain mapped and the header's minimum width must fit the window.

`test_flatpak_history.py` verifies Last Updated from structured journal timestamps,
default/user/named installation matching, user isolation, exact refs and commits,
reinstall/removal boundaries, malformed or absent records, and bounded journal
queries. GTK smoke checks the Last Updated menu label using an isolated display.

`test_packages.py` exercises installed Pacman and APK database fixtures, custom
Pacman roots, missing sizes, changed versions, ambiguous desktop ownership, and
provider failures. Snap tests cover active revisions, exclusion of bases and old
revisions, and HTTP transport over a temporary Unix socket (no real snap daemon or
applications are modified). GTK smoke checks DEB, Pacman, APK and Snap source rows
and their details-page management guidance. These fixtures do not replace tests on
actual Arch/Alpine desktops or a live snapd installation.

`test_platforms.py` checks native package labels, `ID_LIKE` inheritance, ID priority
and unknown/missing os-release fallback. GTK smoke checks the second sidebar row
against the actual host, keeps unsupported native categories selected across refresh,
and ensures RPM fixtures retain their source when run on Ubuntu.

`test_appearance.py` covers durable icon images, per-user desktop precedence,
restoring originals, preserving translations/actions/comments, conflicting and
symlinked launchers, and verified RPM/Flatpak attribution. GTK smoke additionally
exercises icon theme notifications, custom icon labels, the Technical Details path,
Appearance's position at the bottom, and the real serialized icon save/refresh/reset
flow using temporary launchers and a simulated file chooser. `appearance-light.png`
shows the new controls. No personal launcher is edited by these tests.

Unit tests exercise ownership, XDG overrides, invalid and hidden launchers, browser
profile identity, conservative removal plans, changed targets, and partial failures.
Run them with the system Python so GObject introspection modules are available.

```sh
PYTHONPATH=src /usr/bin/python3 -m pytest -q
ruff check src tests build-aux
ruff format --check src tests build-aux
mypy
```

Mypy keeps strict checking for the seven modules listed in `pyproject.toml`.
`follow_imports = "silent"` retains type information from imported modules while
limiting diagnostics to the explicit check targets; plain `mypy` uses this policy
both locally and in CI.

`tests/smoke_ui.py` uses synthetic records and does not invoke real uninstall
or update providers. It exercises list/grid switching, search, details, narrow layout, themes,
preferences, and About. The output is captured from Housekeeper's own rendered widget,
not the desktop or another application. Set `HOUSEKEEPER_SCREENSHOT_DIR` to choose
the output directory. The custom D-Bus configuration avoids activating unrelated
desktop services during tests.

`tests/benchmark_startup.py` opens a real, read-only inventory in an isolated session
with in-memory preferences, reports startup time and peak RSS, and closes it. It
does not remove applications. Run it using the same D-Bus wrapper as the smoke test.
Timing starts before GTK imports and excludes process creation. The synthetic smoke
test separately measures filtering with 1,000 records and asserts the matching result
count. It reports elapsed time without a pass/fail latency threshold, since shared
CI runners have variable CPU and rendering costs. Its peak RSS includes rendering
screenshots and is not an idle-memory measurement.

`tests/stress_icons.py --runs 20` starts fresh GTK processes with generated SVG icons
containing text. It checks themed and file icons, missing-icon fallbacks, resizing,
theme refresh, and signal cleanup. It never reads the application inventory. Use the
same D-Bus wrapper as the smoke test, adding `xvfb-run -a` without a real display.
CI runs five repetitions on each tested GTK runtime. See [the startup crash
investigation](startup-crash.md) for the original failure and mitigation.

Update tests also cover candidate identity, dependency and repository changes,
stale PackageKit inventory, update cancellation, Flatpak commit pinning, partial
completion, and task submission failures. GTK smoke verifies the adjacent actions,
stacked narrow layout, keyboard focus, current/cancelled checks, confirmation defaults,
stale callbacks, deferred refresh, and update progress. Its additional screenshots
are `details-narrow.png` and `update-preview.png`.

## Disposable Fedora environments

Local build outputs use the following directories:

- `build/`: the active Meson development build, including `housekeeper-dev`.
- `dist/sources/`: source archives produced by `build-aux/source_archive.py`.
- `dist/copr/`: current RPM and SRPM packages. Upload the `.src.rpm` file to COPR;
  install the `.noarch.rpm` matching your Fedora release. The validated packaging
  revision is currently `0.1.13-2` for Fedora 43 and 44.
- `dist/archive/`: older packages and source archives, retained for reference.
- `work/logs/` and `work/screenshots/`: local diagnostics and current UI captures.
- `work/archive/`: historical UI captures and investigations. `work/containers/`
  and `work/python-tools/` are local tooling stores, not release artifacts.

Before building a new release, move the previous contents of `dist/copr/` into a
versioned directory under `dist/archive/`. If present, regenerate `SHA256SUMS`
after replacing packages: `(cd dist/copr && sha256sum *.rpm > SHA256SUMS)`.
CI uses `artifacts/`, `release-files/`, and `release-assets/` as temporary staging
directories; local package builds should use `dist/copr/` instead.

The container image contains build and test dependencies, not personal applications.
The source tree is mounted read-only; RPM installation modifies only the disposable
container. Integration scripts require a container marker and an explicit test flag.

```sh
podman build --build-arg FEDORA_VERSION=44 -t housekeeper-test:44 -f tests/Containerfile .
python3 build-aux/source_archive.py
mkdir -p dist/copr
podman run --rm --network none \
  -v "$PWD:/source:ro" -v "$PWD/dist/copr:/artifacts" \
  housekeeper-test:44 bash /source/build-aux/container_check.sh
podman run --rm --network none -v "$PWD:/source:ro" -w /source \
  -e HOUSEKEEPER_DISPOSABLE_TEST=1 \
  housekeeper-test:44 python3 tests/integration_host.py
```

On SELinux hosts, use `--security-opt label=disable` for these disposable test
containers to read the bind mount without relabeling the development checkout.
Repeat with Fedora 43. The packaging check builds RPM/SRPM artifacts, installs the
RPM, replaces that installed version, checks the command from another directory,
and removes Housekeeper from the container.

The real transaction harness starts an isolated system D-Bus and Polkit, builds two
fixture RPMs with a reverse dependency, and runs the provider as an unprivileged
test user. When the backend supports safe previews, it verifies dependency rejection,
an allowed removal, and denied authorization. Otherwise it verifies that the fixture
remains installed and prints an explicit limitation; it does not claim successful
removal or authorization coverage. Its Polkit rule exists only in the disposable container. A local
Flatpak repository supplies a tiny test app, so removal needs no downloaded runtime.
The harness verifies that application data survives uninstall. A temporary AppImage
and launcher exercise the real GIO Trash implementation and preservation of unrelated
application data. See [the validation record](validation.md) for observed results.

The update fixtures create a temporary signing key and a local RPM repository with
two application/dependency versions. They verify preview without installation, a
real signed update, a current-version check, and authorization denial. Update and
removal fallback results are recorded separately. Flatpak fixtures export a tiny
runtime and several application commits, then verify updating both, data preservation,
and rejection when dependencies change after preview. No fixture application is run.

PackageKit is started with `--keep-environment` and `GIO_USE_NETWORK_MONITOR=base`
only inside the disposable harness. This lets DNF5 read uncached `file://` payloads
instead of forcing all-cache mode when netlink sees no network interface. Containers
still run with `--network none`; no external network is available and no production
daemon configuration is changed.

## Manual desktop checks

Before a public release, use Fedora Workstation virtual machines to check actual
GNOME Polkit dialogs (accept, deny, and dismiss), Orca, keyboard-only navigation,
fractional scaling, and an external browser/Steam management handoff. Container
authorization tests verify policy behavior but do not substitute for the graphical
authentication-agent dialog. Exercise real Wayland and X11 sessions separately.

Inspect a mixed inventory and compare effective visible desktop IDs with GIO. A
logical row can represent multiple identical launchers, so raw row counts need not
match the launcher's entry count. Unknown metadata must remain visibly unknown.

## Release process

Keep the version consistent in the Python package, Meson, RPM spec, AppStream
release metadata, source archive script, and workflows. Update CHANGELOG and the
validation record. Review generated screenshots for synthetic data only.

The tag workflow builds and validates both Fedora versions and creates a **draft**
GitHub Release with source, RPM, SRPM, and SHA-256 checksums. It does not publish
automatically. Tags and repository changes must be pushed deliberately; merely
building the app locally does not create a release.

Review installation instructions, platform claims, known limitations, and attached
files before publishing. Publishing a stable GitHub Release triggers
`.github/workflows/copr.yml`, which downloads and verifies one release SRPM and
uploads it to `yuelinxin/housekeeper` in COPR. COPR builds all chroots enabled in
that project. The Actions job waits for the result and fails if COPR reports a
failed build. Pre-releases are excluded. Housekeeper has no application self-updater;
users who enable the COPR repository receive packages through normal DNF updates.

### One-time GitHub and COPR setup

1. Sign in as a COPR project owner or builder and open
   <https://copr.fedorainfracloud.org/api/>. Copy the entire generated configuration
   block, including `[copr-cli]`, `login`, `username`, `token`, and `copr_url`.
2. In the GitHub repository, open **Settings → Secrets and variables → Actions →
   New repository secret**. Name it `COPR_CONFIG` and paste that configuration.
   Keep it out of source files and release attachments. Refresh the secret when
   the COPR API credentials expire or are rotated.
3. Commit and push the workflow and helper with the other packaging fixes to the
   default branch before creating the next release tag. GitHub Actions must be
   enabled for the repository.

The normal sequence is: push a version tag, wait for **Draft release** to finish,
review the draft and its RPM/SRPM/checksum attachments, then click **Publish release**.
**Publish to COPR** starts automatically. It uses GitHub's token to download release
assets before uploading the SRPM, so it also works with a private GitHub repository.
The uploaded source and resulting packages are distributed through the COPR project.

For an existing release or a missed event, open **Actions → Publish to COPR → Run
workflow** on the default branch and enter its tag (for example `v0.1.13`). The
release must already be published and contain the validated SRPMs and `SHA256SUMS`.
A release containing only GitHub's automatic source ZIP/tarball will fail with an
explanation. Keep just one RPM revision per release; replace superseded RPM assets
and regenerate checksums before submitting a packaging revision.

Manual dispatch submits a new COPR build, so check for an existing successful or
running build before retrying. The job waits up to 90 minutes; if it times out,
check COPR before resubmitting because the remote build can continue. If publishing
is later automated using `GITHUB_TOKEN`, explicitly dispatch the COPR workflow:
GitHub does not start a second workflow for a release event generated by that token.

References: [COPR user documentation](https://docs.copr.fedorainfracloud.org/user_documentation.html),
[GitHub release events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#release),
[triggering workflows](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).

## Updates page and batch regression checks

Cache and GTK smoke regressions cover removing successfully updated installations from
the remaining list after both batch and details-page updates, including unchanged
inventory records. They check preserved selection, count, original
check time, cache restoration, partial completion, cancellation/failure, duplicate
launcher identities, incomplete check details, and the final up-to-date empty state.
After the last listed update completes, the up-to-date state survives inventory
changes limited to the completed installations and cache restoration. Unrelated
changes still mark the report stale, including pending rows pruned before completion,
new installations during an update, and details-page updates after an empty report.
The live page and restored cache must agree even when an unrelated change is observed
before the completion callback.

`test_batch_updates.py` covers duplicate installation grouping, separate installations,
incomplete checks, cancellation, shared dependencies, changed sources or targets, and
stopping a batch after partial completion. Service tests cover batch task exclusivity
and cancellation before submission completes. Synthetic GTK smoke exercises the bottom
sidebar row, per-app checkboxes, Update Selected/Update All confirmation, narrow layout,
partial results, and navigation back to the inventory without executing package updates.

The offline Flatpak integration fixture publishes two applications sharing a runtime.
Both previews include that runtime; the batch must update it once, reconcile the second
preview, and verify both applications' exact target commits and retained personal data.
It then publishes only a newer runtime and verifies an empty application update list.

Update discovery regressions check one Flatpak query per installation, one RPM
refresh/update query for 101 desktop records, previews only for matching candidates,
exact architecture/branch matching, hidden NoDisplay entries, retained dependencies,
same-commit repairs, legacy cache filtering, failed sources and cancellation. Query
counts verify eliminated repeated work; they are not elapsed-time benchmarks.

The signed RPM fixture also installs two version-1 applications with a shared strict
versioned dependency, then checks and updates both in a batch. Version queries must
show version 2 for both applications and the dependency. A package already updated
as part of the preceding transaction is not executed again.

## System Flatpak authorization regression

`integration_flatpak_system_update.py` builds a signed local system repository and
runs the client as an unprivileged user. It reproduces the explicit-commit root
permission error, verifies that policy denial and a stale target cannot deploy,
and checks that the normal system-helper update reaches the confirmed commit while
preserving personal data. The harness starts the helper directly because container
D-Bus service activation cannot use its normal setuid launcher. Without `/dev/fuse`,
the fixture sets `FLATPAK_REVOKEFS_FUSE=/usr/bin/false` to exercise Flatpak's signed
child-repository fallback. Production helper and FUSE configuration remain unchanged.
Graphical password/fingerprint prompts still require a desktop authentication agent;
the fixture exercises Polkit allow/deny rules rather than collecting credentials.

The system Flatpak fixture also serves a deliberately slow extra-data payload over
localhost in the network-isolated container. After transfer begins, it requests
cancellation, checks that the payload was only partially transferred, and verifies
the app commit and personal data remain unchanged. The client runs in a worker with
a GLib main loop, matching the desktop. Unit tests cover cancellation before any
component changes and after a runtime completes; GTK smoke checks single-request
cancellation, its pending indicator and immunity to late progress callbacks.
