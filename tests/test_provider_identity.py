from dataclasses import replace

import pytest

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
    index.enrich(app)
    assert app.source == Source.OTHER


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


def test_hidden_override_without_flatpak_key_hides_synthetic_apps(entry):
    index = FlatpakIndex.__new__(FlatpakIndex)
    index.apps = [flatpak_app("/system-flatpak"), flatpak_app("/user-flatpak")]
    hidden = entry(
        desktop_id="org.example.App.desktop",
        visible=False,
        reason="Hidden by a desktop entry override",
    )
    index.associate(classify(hidden))
    assert all(not app.visible for app in index.apps)


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
    assert index.associate(classify(desktop)) is beta
    assert first.key != beta.key != other.key
