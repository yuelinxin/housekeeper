# Testing and releases

## Local tests

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
test separately measures filtering with 1,000 records; its peak RSS includes rendering
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

The container image contains build and test dependencies, not personal applications.
The source tree is mounted read-only; RPM installation modifies only the disposable
container. Integration scripts require a container marker and an explicit test flag.

```sh
podman build --build-arg FEDORA_VERSION=44 -t housekeeper-test:44 -f tests/Containerfile .
python3 build-aux/source_archive.py
mkdir -p dist/packages
podman run --rm --network none \
  -v "$PWD:/source:ro" -v "$PWD/dist/packages:/artifacts" \
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
files before publishing. GitHub releases are the initial distribution channel;
there is no application self-updater or configured COPR repository.

## Updates page and batch regression checks

`test_batch_updates.py` covers duplicate installation grouping, separate installations,
incomplete checks, cancellation, shared dependencies, changed sources or targets, and
stopping a batch after partial completion. Service tests cover batch task exclusivity
and cancellation before submission completes. Synthetic GTK smoke exercises the bottom
sidebar row, per-app checkboxes, Update Selected/Update All confirmation, narrow layout,
partial results, and navigation back to the inventory without executing package updates.

The offline Flatpak integration fixture publishes two applications sharing a runtime.
Both previews include that runtime; the batch must update it once, reconcile the second
preview, and verify both applications' exact target commits and retained personal data.

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
