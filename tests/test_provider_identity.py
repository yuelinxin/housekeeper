from dataclasses import replace

import pytest

from housekeeper.attribution import attribute
from housekeeper.identity import classify, digest
from housekeeper.models import AppRecord, Source
from housekeeper.providers.flatpak import FlatpakIndex
from housekeeper.providers.rpm import RpmIndex


@pytest.mark.parametrize(
    "argv",
    [
        ("/usr/bin/sh", "-c", "/home/example/app"),
        ("/usr/bin/python3", "/home/example/app.py"),
        ("/usr/bin/flatpak", "run", "org.example.App"),
        ("/usr/bin/gnome-terminal", "--", "/home/example/app"),
        ("/usr/bin/gapplication", "launch", "org.example.App"),
        ("/usr/bin/gapplication",),
    ],
)
def test_user_launcher_does_not_attribute_guest_to_host_package(entry, argv):
    app = classify(entry(argv))
    index = RpmIndex.__new__(RpmIndex)
    index.ts = object()
    index.owners = lambda path: (
        []
        if str(path).endswith(".desktop")
        else [{"name": "host-package", "version": "1-1", "arch": "x86_64"}]
    )
    app = attribute(app, (index,))
    assert app.source == Source.OTHER


@pytest.mark.parametrize(
    "package_name,argv,binary_package",
    [
        ("libreoffice-calc", ("/usr/bin/libreoffice", "--calc", "%U"), "libreoffice-core"),
        ("libreoffice-impress", ("/usr/bin/libreoffice", "--impress", "%U"), "libreoffice-core"),
        ("libreoffice-writer", ("/usr/bin/libreoffice", "--writer", "%U"), "libreoffice-core"),
        ("gnome-maps", ("/usr/bin/gapplication", "launch", "org.gnome.Maps", "%U"), "glib2"),
        ("gnome-weather", ("/usr/bin/gapplication", "launch", "org.gnome.Weather"), "glib2"),
    ],
)
def test_rpm_desktop_owner_identifies_app_using_shared_binary(
    entry, package_name, argv, binary_package
):
    package = {"name": package_name, "version": "2-1", "arch": "x86_64", "size": "1234"}
    app = classify(entry(argv))
    index = RpmIndex.__new__(RpmIndex)
    index.ts = object()
    index.owners = lambda path: (
        [package]
        if path == app.entries[0].path
        else [
            {"name": binary_package, "version": "1-1", "arch": arch} for arch in ("x86_64", "i686")
        ]
    )
    app = attribute(app, (index,))
    assert app.source == Source.RPM and app.provider == "rpm"
    assert app.metadata["name"] == package_name
    assert app.identity == package_name + "-2-1.x86_64"
    assert app.version == "2-1" and app.software_size == 1234


def test_ambiguous_rpm_desktop_owner_does_not_fall_back_to_binary(entry):
    app = classify(entry())
    packages = [{"name": name, "version": "1-1", "arch": "x86_64"} for name in ("first", "second")]
    index = RpmIndex.__new__(RpmIndex)
    index.ts = object()
    index.owners = lambda path: packages if path == app.entries[0].path else packages[:1]
    app = attribute(app, (index,))
    assert app.source == Source.OTHER


@pytest.mark.parametrize("ambiguous", [False, True])
def test_unowned_direct_rpm_launcher_requires_unambiguous_binary(entry, ambiguous):
    desktop = entry(("/usr/bin/example", "%U"), resolved_executable="/usr/libexec/example")
    app = classify(desktop)
    package = {"name": "example", "version": "1-1", "arch": "x86_64"}

    def owners(path):
        if path == desktop.path:
            return []
        if ambiguous and str(path) == desktop.resolved_executable:
            return [{**package, "name": "different"}]
        return [package]

    index = RpmIndex.__new__(RpmIndex)
    index.ts = object()
    index.owners = owners
    app = attribute(app, (index,))
    assert app.source == (Source.OTHER if ambiguous else Source.RPM)


@pytest.mark.parametrize(
    "argv",
    [
        ("/usr/bin/chromium", "--app=https://example.org"),
        ("/usr/bin/chromium", "--app-id=unrecognized-id"),
        ("/usr/bin/flatpak", "run", "org.chromium.Chromium", "--app-id=" + "a" * 32),
    ],
)
def test_browser_guest_with_nonstandard_launcher_never_uninstalls_browser(entry, argv):
    app = classify(entry(argv, flatpak_id="org.chromium.Chromium"))
    assert app.source == Source.WEB
    assert app.provider == "browser-wrapper"


def flatpak_app(installation, branch="stable"):
    identity = "app/org.example.App/x86_64/" + branch
    return AppRecord(
        digest(installation, identity),
        "Example",
        source=Source.FLATPAK,
        provider="flatpak",
        identity=identity,
        metadata={"installation": installation, "app_id": "org.example.App"},
    )


@pytest.mark.parametrize(
    "argv",
    [
        ("flatpak", "run", "com.valvesoftware.Steam", "steam://rungameid/123"),
        ("flatpak", "run", "--command=firefoxpwa", "org.mozilla.firefox", "site", "launch", "SITE"),
    ],
)
def test_sandboxed_external_app_never_maps_to_host_flatpak(entry, argv):
    app = classify(entry(argv, flatpak_id="org.example.Host"))
    assert app.provider == "external-wrapper"
    assert app.source in {Source.STEAM, Source.WEB}


def test_hidden_override_cannot_authorize_or_mutate_installations(entry):
    index = FlatpakIndex.__new__(FlatpakIndex)
    index.apps = [flatpak_app("/system-flatpak"), flatpak_app("/user-flatpak")]
    hidden = entry(
        desktop_id="org.example.App.desktop",
        visible=False,
        reason="Hidden by a desktop entry override",
    )
    index.associate(classify(hidden))
    assert all(app.visible and not app.entries for app in index.apps)


def test_flatpak_installations_and_branches_are_not_conflated(entry, tmp_path):
    index = FlatpakIndex.__new__(FlatpakIndex)
    first = flatpak_app(str(tmp_path / "system"))
    beta = flatpak_app(str(tmp_path / "system"), "beta")
    other = flatpak_app(str(tmp_path / "user"))
    index.apps = [first, beta, other]
    desktop = entry(
        ("flatpak", "run", "--branch=beta", "org.example.App"), flatpak_id="org.example.App"
    )
    desktop = replace(
        desktop, path=tmp_path / "system/exports/share/applications/org.example.App.desktop"
    )
    assert index.associate(classify(desktop)).key == beta.key
    assert not beta.entries
    assert first.key != beta.key != other.key
