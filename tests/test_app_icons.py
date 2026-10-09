"""Desktop/theme integration is source-independent and never changes host settings."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from housekeeper.app_icons import (
    GENERIC,
    IconUpdateBusy,
    _lock,
    has_app_icon,
    refresh_icon_theme,
    save_app_icon,
)
from housekeeper.appearance import can_reset_icon
from housekeeper.discovery import read_entry
from housekeeper.models import AppRecord, ManagementError, Source


class Settings:
    def __init__(self):
        self.theme = "Adwaita"
        self.writable = True
        self.fail = False

    def get_string(self, key):
        assert key == "icon-theme"
        return self.theme

    def set_string(self, key, value):
        assert key == "icon-theme"
        if self.fail:
            return False
        self.theme = value
        return True

    def is_writable(self, key):
        return self.writable


@pytest.fixture
def setup_icons(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(root))
    image = tmp_path / "picture.svg"
    image.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="24"><rect width="32" height="24" fill="red"/></svg>'
    )
    return root, image, Settings()


def app_for(paths, source=Source.OTHER):
    entries = [read_entry(path, path.parent) for path in paths]
    return AppRecord(entries[0].desktop_id, "Example", source=source, entries=entries)


def reread(app, root):
    return replace(
        app,
        entries=[
            read_entry(root / "applications" / e.desktop_id, root / "applications")
            for e in app.entries
        ],
    )


def theme_icons(root, settings):
    return root / "icons" / settings.theme / "scalable/apps"


@pytest.mark.parametrize("source", list(Source))
def test_all_sources_change_all_launchers_named_icons_and_restore(desktop, setup_icons, source):
    root, image, settings = setup_icons
    first = desktop(
        filename="org.example.App.desktop", Icon="example-icon", StartupWMClass="ExampleApp"
    )
    second = desktop(filename="org.example.Editor.desktop", Icon="example-editor")
    app = app_for([first, second], source)
    originals = [p.read_bytes() for p in (first, second)]
    save_app_icon(app, image, [app], settings=settings)
    changed = reread(app, root)
    assert changed.entries[0].icon == changed.entries[1].icon
    icons = theme_icons(root, settings)
    for alias in (
        "org.example.App",
        "org.example.Editor",
        "ExampleApp",
        "example-icon",
        "example-editor",
    ):
        assert (icons / (alias + ".png")).read_bytes() == Path(changed.entries[0].icon).read_bytes()
    assert "Inherits=Adwaita" in (icons.parent.parent / "index.theme").read_text()
    assert [p.read_bytes() for p in (first, second)] == originals
    old_theme = icons.parent.parent
    save_app_icon(changed, None, [changed], settings=settings)
    assert settings.theme == "Adwaita" and not old_theme.exists()
    assert not list((root / "applications").glob("*.desktop"))
    assert [p.read_bytes() for p in (first, second)] == originals


def test_chrome_panel_alias_and_inherited_icons_resolve_in_real_gtk(desktop, setup_icons):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    root, image, settings = setup_icons
    path = desktop(
        filename="chrome-app-Default.desktop", Icon="chrome-app-Default", StartupWMClass="crx_app"
    )
    app = app_for([path], Source.WEB)
    save_app_icon(app, image, [app], settings=settings)
    theme = Gtk.IconTheme.new()
    theme.set_search_path([str(root / "icons"), "/usr/share/icons", "/usr/share/pixmaps"])
    theme.set_theme_name(settings.theme)
    # The extension requests this name directly, ignoring the desktop Icon field.
    for size in (16, 32, 64, 128):
        icon = theme.lookup_icon(
            "chrome-app-Default", None, size, 1, Gtk.TextDirection.NONE, Gtk.IconLookupFlags(0)
        )
        assert Path(icon.get_file().get_path()).resolve() == Path(reread(app, root).entries[0].icon)
    symbolic = theme.lookup_icon(
        "chrome-app-Default",
        None,
        16,
        1,
        Gtk.TextDirection.NONE,
        Gtk.IconLookupFlags.FORCE_SYMBOLIC,
    )
    assert Path(symbolic.get_file().get_path()).resolve() == Path(reread(app, root).entries[0].icon)
    assert theme.has_icon("folder")  # Unrelated icons still come from the user's theme.


def test_shared_and_generic_names_do_not_replace_other_apps(desktop, setup_icons):
    root, image, settings = setup_icons
    first = app_for([desktop(filename="first.desktop", Icon="shared-icon")])
    save_app_icon(first, image, [first], settings=settings)
    first = reread(first, root)
    second = app_for([desktop(filename="second.desktop", Icon="shared-icon")])
    save_app_icon(second, image, [first, second], settings=settings)
    icons = theme_icons(root, settings)
    assert not (icons / "shared-icon.png").exists()
    assert (icons / "first.png").exists() and (icons / "second.png").exists()
    assert all(not (icons / (name + ".png")).exists() for name in GENERIC)


def test_changing_theme_and_resetting_one_app_preserves_the_other(desktop, setup_icons):
    root, image, settings = setup_icons
    first = app_for([desktop(filename="first.desktop", Icon="first")])
    second = app_for([desktop(filename="second.desktop", Icon="second")])
    save_app_icon(first, image, [first, second], settings=settings)
    first = reread(first, root)
    settings.theme = "NewTheme"
    save_app_icon(second, image, [first, second], settings=settings)
    second = reread(second, root)
    save_app_icon(first, None, [first, second], settings=settings)
    icons = theme_icons(root, settings)
    assert (icons / "second.png").exists() and not (icons / "first.png").exists()
    assert "Inherits=NewTheme" in (icons.parent.parent / "index.theme").read_text()
    save_app_icon(second, None, [second], settings=settings)
    assert settings.theme == "NewTheme"


def test_a_failed_theme_update_rolls_back_every_launcher_and_registry(desktop, setup_icons):
    root, image, settings = setup_icons
    path = desktop(root=root / "applications", Icon="original")
    other = desktop(filename="other.desktop", Icon="other")
    app = app_for([path, other])
    before = path.read_bytes()
    settings.fail = True
    with pytest.raises(ManagementError, match="could not be updated"):
        save_app_icon(app, image, [app], settings=settings)
    assert path.read_bytes() == before
    assert not (root / "applications/other.desktop").exists()
    assert not (root / "housekeeper/app-icons.json").exists()
    assert settings.theme == "Adwaita"
    assert not list((root / "icons").iterdir())


def test_stale_second_launcher_does_not_leave_first_changed(desktop, setup_icons):
    root, image, settings = setup_icons
    paths = [
        desktop(filename=f"{name}.desktop", root=root / "applications", Icon=name)
        for name in ("first", "second")
    ]
    app = app_for(paths)
    before = paths[0].read_bytes()
    paths[1].write_text(paths[1].read_text().replace("Icon=second", "Icon=changed"))
    with pytest.raises(ManagementError, match="icon has changed"):
        save_app_icon(app, image, [app], settings=settings)
    assert paths[0].read_bytes() == before and "Icon=changed" in paths[1].read_text()
    assert settings.theme == "Adwaita"


def test_repeated_edits_use_new_generation_and_keep_first_original(desktop, setup_icons):
    root, image, settings = setup_icons
    app = app_for([desktop(Icon="original")])
    save_app_icon(app, image, [app], settings=settings)
    app = reread(app, root)
    old = theme_icons(root, settings).parent.parent
    image.write_text(image.read_text().replace("red", "blue"))
    save_app_icon(app, image, [app], settings=settings)
    app = reread(app, root)
    assert not old.exists()
    assert (theme_icons(root, settings) / "original.png").read_bytes() == Path(
        app.entries[0].icon
    ).read_bytes()
    save_app_icon(app, None, [app], settings=settings)
    assert settings.theme == "Adwaita"


@pytest.mark.parametrize("locked", [True, False])
def test_unavailable_theme_setting_still_changes_launchers(
    desktop, setup_icons, monkeypatch, locked
):
    root, image, settings = setup_icons
    path = desktop(Icon="original")
    app = app_for([path])
    settings.writable = False
    # Locked by an administrator, or the desktop schema is not installed.
    monkeypatch.setattr("housekeeper.app_icons._settings", lambda: None)
    injected = settings if locked else None
    save_app_icon(app, image, [app], settings=injected)
    changed = reread(app, root)
    assert changed.entries[0].icon.endswith(".png") and can_reset_icon(changed.entries[0])
    assert settings.theme == "Adwaita"
    assert not (root / "housekeeper/app-icons.json").exists() and not (root / "icons").exists()
    save_app_icon(changed, None, [changed], settings=injected)
    assert not (root / "applications/example.desktop").exists()


def test_corrupt_state_leaves_launchers_unchanged(desktop, setup_icons):
    root, image, settings = setup_icons
    path = desktop(Icon="original")
    app = app_for([path])
    state = root / "housekeeper/app-icons.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"version": 9}))
    with pytest.raises(ManagementError, match="could not be read"):
        save_app_icon(app, image, [app], settings=settings)
    assert not (root / "applications").exists()


def test_theme_switch_rebases_overrides_and_reset_restores_new_base(desktop, setup_icons):
    root, image, settings = setup_icons
    app = app_for([desktop(Icon="original")])
    save_app_icon(app, image, [app], settings=settings)
    app = reread(app, root)
    settings.theme = "Papirus"
    refresh_icon_theme(settings)
    icons = theme_icons(root, settings)
    assert "Inherits=Papirus" in (icons.parent.parent / "index.theme").read_text()
    assert (icons / "original.png").is_file()
    theme = settings.theme
    refresh_icon_theme(settings)
    assert settings.theme == theme
    save_app_icon(app, None, [app], settings=settings)
    assert settings.theme == "Papirus"


def test_theme_rebase_failure_preserves_registry_and_users_selection(desktop, setup_icons):
    root, image, settings = setup_icons
    app = app_for([desktop(Icon="original")])
    save_app_icon(app, image, [app], settings=settings)
    state = root / "housekeeper/app-icons.json"
    before = state.read_bytes()
    settings.theme = "NewTheme"
    settings.fail = True
    with pytest.raises(ManagementError, match="could not be updated"):
        refresh_icon_theme(settings)
    assert state.read_bytes() == before and settings.theme == "NewTheme"


def test_concurrent_update_cannot_observe_or_replace_partial_state(desktop, setup_icons):
    root, image, settings = setup_icons
    app = app_for([desktop(Icon="original")])
    with _lock(), pytest.raises(IconUpdateBusy):
        save_app_icon(app, image, [app], settings=settings)
    assert not (root / "applications").exists()


def test_same_icon_save_is_idempotent(desktop, setup_icons):
    root, image, settings = setup_icons
    app = app_for([desktop(Icon="original")])
    save_app_icon(app, image, [app], settings=settings)
    app = reread(app, root)
    theme = settings.theme
    save_app_icon(app, image, [app], settings=settings)
    assert settings.theme == theme
    assert len(list((root / "icons").iterdir())) == 1


def test_rollback_preserves_a_concurrent_launcher_edit(desktop, setup_icons, monkeypatch):
    root, image, settings = setup_icons
    path = desktop(root=root / "applications", Icon="original")
    app = app_for([path])

    def fail(_state, _current):
        path.write_text(path.read_text() + "Comment=Edited elsewhere\n")
        raise OSError("Theme write failed")

    monkeypatch.setattr("housekeeper.app_icons._make_theme", fail)
    with pytest.raises(OSError, match="Theme write failed"):
        save_app_icon(app, image, [app], settings=settings)
    assert "Comment=Edited elsewhere" in path.read_text()


def test_unrelated_launcher_removed_since_the_scan_does_not_block_saving(desktop, setup_icons):
    root, image, settings = setup_icons
    app = app_for([desktop(Icon="original")])
    removed = desktop(filename="gone.desktop", Icon="gone")
    other = app_for([removed])
    removed.unlink()
    save_app_icon(app, image, [app, other], settings=settings)
    assert (theme_icons(root, settings) / "original.png").exists()


def test_uninstalled_app_releases_its_names(desktop, setup_icons, tmp_path, monkeypatch):
    root, image, settings = setup_icons
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system"))
    path = desktop(filename="org.foo.Foo.desktop", root=root / "applications", Icon="foo")
    app = app_for([path])
    save_app_icon(app, image, [app], settings=settings)
    assert has_app_icon(app)
    path.unlink()  # Removed through any provider.
    refresh_icon_theme(settings)
    assert settings.theme == "Adwaita" and not has_app_icon(app)
    assert json.loads((root / "housekeeper/app-icons.json").read_text())["apps"] == {}
    assert not list((root / "icons").iterdir())


def test_saved_names_stay_resettable_after_the_launcher_override_is_lost(
    desktop, setup_icons, tmp_path
):
    root, image, settings = setup_icons
    source = desktop(filename="example.desktop", root=tmp_path / "system", Icon="original")
    app = app_for([source])
    save_app_icon(app, image, [app], settings=settings)
    (root / "applications/example.desktop").unlink()  # Deleted outside Housekeeper.
    assert not can_reset_icon(app.entries[0]) and has_app_icon(app)
    save_app_icon(app, None, [app], settings=settings)
    assert settings.theme == "Adwaita" and not has_app_icon(app)
