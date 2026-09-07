# Testing and releases

## Local tests

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
providers. It exercises list/grid switching, search, details, narrow layout, themes,
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

## Disposable Fedora environments

The container image contains build and test dependencies, not personal applications.
The source tree is mounted read-only; RPM installation modifies only the disposable
container. Integration scripts require a container marker and an explicit test flag.

```sh
podman build --build-arg FEDORA_VERSION=44 -t housekeeper-test:44 -f tests/Containerfile .
python3 build-aux/source_archive.py dist/housekeeper-0.1.0.tar.gz
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
