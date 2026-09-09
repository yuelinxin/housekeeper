"""Independent reproductions from the application discovery audit."""

from pathlib import Path

import pytest

from housekeeper.discovery import application_roots, read_entry
from housekeeper.identity import classify, unwrap_env
from housekeeper.models import Action, AppRecord, Source
from housekeeper.providers.flatpak import FlatpakIndex


def test_empty_xdg_uses_defaults():
    assert application_roots({"XDG_DATA_HOME": "", "XDG_DATA_DIRS": ""}, Path("/example")) == [
        Path("/example/.local/share/applications"),
        Path("/usr/local/share/applications"),
        Path("/usr/share/applications"),
    ]


@pytest.mark.parametrize("prefix", [("env", "--"), ("env", "-u", "FOO")])
def test_env_option_forms(prefix):
    assert unwrap_env((*prefix, "/usr/bin/true")) == ("/usr/bin/true",)


def test_script_with_appimage_suffix_is_not_an_appimage(desktop, tmp_path):
    binary = tmp_path / "ordinary.AppImage"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    path = desktop(Exec=str(binary))
    assert classify(read_entry(path, tmp_path)).source != Source.APPIMAGE


@pytest.mark.parametrize("binary", ["brave-browser", "microsoft-edge"])
def test_unimplemented_browser_stays_external(entry, binary):
    app = classify(entry((binary, "--app-id=" + "a" * 32)))
    assert app.source == Source.OTHER
    assert app.action != Action.UNINSTALL


@pytest.mark.parametrize(
    "argv",
    [
        ("/usr/bin/true",),
        ("flatpak", "run", "org.example.Different"),
        ("flatpak", "run", "org.example.App", "--app-id=guest"),
    ],
)
def test_flatpak_tag_does_not_authorize_unrelated_launcher(entry, argv):
    installed = AppRecord(
        "installed",
        "Actual application",
        source=Source.FLATPAK,
        provider="flatpak",
        identity="app/org.example.App/x86_64/stable",
        action=Action.UNINSTALL,
        metadata={"installation": "/example/flatpak", "app_id": "org.example.App"},
    )
    index = FlatpakIndex.__new__(FlatpakIndex)
    index.apps = [installed]
    linked = index.associate(classify(entry(argv, flatpak_id="org.example.App")))
    assert linked is None or linked.action != Action.UNINSTALL
    assert installed.name == "Actual application"
    assert not installed.entries
