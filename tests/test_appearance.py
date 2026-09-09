from dataclasses import replace
from pathlib import Path

import pytest
from gi.repository import GLib

from housekeeper.appearance import (
    can_reset_icon,
    launcher_theme,
    save_icon,
    verified_icon_source,
)
from housekeeper.attribution import attribute
from housekeeper.discovery import read_entry, scan_entries
from housekeeper.identity import classify
from housekeeper.models import AppRecord, ManagementError, Source
from housekeeper.providers.flatpak import FlatpakIndex
from housekeeper.providers.rpm import RpmIndex


@pytest.fixture
def icon_home(tmp_path, monkeypatch):
    root = tmp_path / "user-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(root))
    return root


@pytest.fixture
def picture(tmp_path):
    path = tmp_path / "picture.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="24">'
        '<rect width="32" height="24" fill="red"/></svg>'
    )
    return path


def test_system_override_preserves_launcher_and_survives_image_removal(desktop, icon_home, picture):
    source = desktop(
        root=icon_home.parent / "system/applications",
        Icon="old-icon",
        **{"Name[zh_CN]": "示例", "DBusActivatable": "true"},
    )
    source.write_text(
        source.read_text() + "# Keep actions\n[Desktop Action New]\nExec=true --new\nName=New\n"
    )
    before = source.read_bytes()
    entry = read_entry(source, source.parent)
    target = save_icon(entry, picture)
    assert target == icon_home / "applications" / entry.desktop_id
    assert source.read_bytes() == before
    changed = read_entry(target, target.parent)
    assert changed.command == entry.command and changed.dbus_activatable
    assert "Name[zh_CN]=示例" in target.read_text()
    assert "# Keep actions" in target.read_text() and "Exec=true --new" in target.read_text()
    assert verified_icon_source(target) == source
    picture.unlink()
    assert Path(changed.icon).read_bytes().startswith(b"\x89PNG")
    entries, warnings = scan_entries([target.parent, source.parent])
    assert not warnings and len(entries) == 1 and entries[0].icon == changed.icon
    assert can_reset_icon(changed)
    assert save_icon(changed) == source
    assert not target.exists() and source.read_bytes() == before


def test_user_icon_reset_preserves_other_edits_and_first_icon(desktop, icon_home, picture):
    source = desktop(root=icon_home / "applications", Icon="original")
    source.chmod(0o700)
    entry = read_entry(source, source.parent)
    save_icon(entry, picture)
    source.write_text(source.read_text().replace("Name=Example", "Name=Renamed"))
    changed = read_entry(source, source.parent)
    save_icon(changed, picture)
    save_icon(read_entry(source, source.parent))
    restored = read_entry(source, source.parent)
    assert restored.icon == "original" and restored.name == "Renamed"
    assert source.stat().st_mode & 0o777 == 0o700
    assert not can_reset_icon(restored)
    assert "X-Housekeeper-" not in source.read_text()


def test_reset_without_original_icon_and_nested_user_launcher(desktop, icon_home, picture):
    root = icon_home / "applications"
    source = desktop(root=root, filename="nested/example.desktop")
    assert save_icon(read_entry(source, root), picture) == source
    save_icon(read_entry(source, root))
    assert "\nIcon=" not in source.read_text()
    assert not (root / "nested-example.desktop").exists()


@pytest.mark.parametrize("edit", ["Comment=My note", "# My note"])
def test_reset_keeps_unrelated_override_edits(desktop, icon_home, picture, edit):
    source = desktop(Icon="original")
    target = save_icon(read_entry(source, source.parent), picture)
    target.write_text(target.read_text() + edit + "\n")
    save_icon(read_entry(target, target.parent))
    assert edit in target.read_text()
    assert read_entry(target, target.parent).icon == "original"


def test_reset_uses_updated_source_icon(desktop, icon_home, picture):
    source = desktop(Icon="original")
    target = save_icon(read_entry(source, source.parent), picture)
    source.write_text(source.read_text().replace("Icon=original", "Icon=updated"))
    save_icon(read_entry(target, target.parent))
    assert not target.exists()
    assert read_entry(source, source.parent).icon == "updated"


def test_invalid_image_and_conflicting_override_do_not_change_launcher(desktop, icon_home, picture):
    source = desktop(Icon="original")
    entry = read_entry(source, source.parent)
    original = source.read_bytes()
    picture.write_text("not an image")
    with pytest.raises(GLib.Error):
        save_icon(entry, picture)
    target = icon_home / "applications" / entry.desktop_id
    assert not target.exists() and source.read_bytes() == original
    target.parent.mkdir(parents=True)
    target.write_text("unrelated override")
    with pytest.raises(ManagementError, match="changed"):
        save_icon(entry, picture)
    assert target.read_text() == "unrelated override"


def test_symlink_invalid_id_and_stale_icon_are_rejected(desktop, icon_home, picture):
    root = icon_home / "applications"
    source = desktop(root=root, Icon="original")
    entry = read_entry(source, root)
    with pytest.raises(ManagementError, match="invalid"):
        save_icon(replace(entry, desktop_id="../escape.desktop"), picture)
    with pytest.raises(ManagementError, match="changed"):
        save_icon(replace(entry, icon="stale"), picture)
    link = root / "link.desktop"
    link.symlink_to(source)
    with pytest.raises(ManagementError, match="symbolic"):
        save_icon(read_entry(link, root), picture)
    assert read_entry(source, root).icon == "original"


def test_icon_override_keeps_rpm_attribution_but_changed_command_does_not(
    desktop, icon_home, picture
):
    source = desktop(Exec="/usr/bin/python3 /usr/share/example/main.py", Icon="original")
    target = save_icon(read_entry(source, source.parent), picture)
    index = RpmIndex.__new__(RpmIndex)
    index.ts = object()
    index.owners = lambda path: (
        [{"name": "example", "version": "1", "arch": "x86_64"}] if Path(path) == source else []
    )
    app = classify(read_entry(target, target.parent))
    app = attribute(app, (index,))
    assert app.source == Source.RPM
    target.write_text(target.read_text().replace("main.py", "unrelated.py"))
    app = classify(read_entry(target, target.parent))
    app = attribute(app, (index,))
    assert app.source == Source.OTHER


def test_icon_override_keeps_flatpak_installation_identity(desktop, icon_home, picture, tmp_path):
    system = tmp_path / "system-flatpak"
    source = desktop(
        root=system / "exports/share/applications",
        Exec="flatpak run --system org.example.App",
        **{"X-Flatpak": "org.example.App"},
    )
    target = save_icon(read_entry(source, source.parent), picture)
    index = FlatpakIndex.__new__(FlatpakIndex)
    index.apps = [
        AppRecord(
            str(path),
            "Example",
            scope="System" if path == system else "User",
            identity="app/org.example.App/x86_64/stable",
            metadata={"app_id": "org.example.App", "installation": str(path)},
        )
        for path in (system, tmp_path / "user-flatpak")
    ]
    assert index.associate(classify(read_entry(target, target.parent))).key == index.apps[0].key


def test_explicit_launcher_theme(entry):
    assert launcher_theme(entry(("env", "GTK_THEME=Yaru:dark", "example"))) == "Yaru:dark"
    assert launcher_theme(entry(("sh", "-c", "GTK_THEME=Yaru example"))) == ""
    assert launcher_theme(entry(("example", "GTK_THEME=Yaru"))) == ""
