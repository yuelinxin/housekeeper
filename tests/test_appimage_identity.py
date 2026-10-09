"""Read real SquashFS fixtures; neither fixtures nor imported AppImages are run."""

import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from appimage_fixture import image_bytes
from gi.repository import Gio

from housekeeper.appearance import save_icon
from housekeeper.appimage_identity import _read_command, read_appimage_identity
from housekeeper.discovery import read_entry
from housekeeper.installations import Installer, InstallRequest


@pytest.fixture
def appimage(tmp_path):
    if not shutil.which("mksquashfs") or not shutil.which("unsquashfs"):
        pytest.skip("squashfs-tools is optional")

    def make(files=None):
        root = tmp_path / "AppDir"
        root.mkdir(exist_ok=True)
        for name, data in (
            files
            or {
                "org.example.App.desktop": "[Desktop Entry]\nType=Application\nName=Example\nExec=DO-NOT-RUN\nStartupWMClass=ExampleApp\n"
            }
        ).items():
            (root / name).write_text(data)
        filesystem = tmp_path / "filesystem.squashfs"
        subprocess.run(
            ["mksquashfs", str(root), str(filesystem), "-noappend", "-processors", "1", "-quiet"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        header = bytearray(image_bytes())
        struct.pack_into("<Q", header, 40, 64)
        struct.pack_into("<HH", header, 58, 64, 1)
        path = tmp_path / "Example.AppImage"
        path.write_bytes(header + bytes(64) + filesystem.read_bytes())
        return path

    return make


def test_embedded_identity_survives_import_without_importing_commands(
    appimage, tmp_path, monkeypatch
):
    path = appimage()
    identity = read_appimage_identity(path)
    assert identity.desktop_id == "org.example.App.desktop"
    assert identity.wm_class == "ExampleApp"
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system"))
    result = Installer().install(InstallRequest("appimage", str(path)), lambda *_: None)
    info = Gio.DesktopAppInfo.new_from_filename(result.completed[0])
    assert result.completed[0].endswith("/org.example.App.desktop")
    assert info.get_startup_wm_class() == "ExampleApp"
    assert "DO-NOT-RUN" not in info.get_commandline()


def test_existing_desktop_id_collision_falls_back_to_hashed_launcher(
    appimage, tmp_path, monkeypatch, desktop
):
    path = appimage()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system"))
    existing = desktop(filename="org.example.App.desktop", root=tmp_path / "system/applications")
    before = existing.read_bytes()
    result = Installer().install(InstallRequest("appimage", str(path)), lambda *_: None)
    info = Gio.DesktopAppInfo.new_from_filename(result.completed[0])
    assert Path(result.completed[0]).name.startswith("housekeeper-appimage-")
    assert not info.get_startup_wm_class()
    assert existing.read_bytes() == before


def test_newer_build_of_an_imported_appimage_can_be_added(appimage, tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system"))
    Installer().install(InstallRequest("appimage", str(appimage())), lambda *_: None)
    newer = appimage({"version": "1.1"})  # Different contents, same embedded launcher.
    result = Installer().install(InstallRequest("appimage", str(newer)), lambda *_: None)
    assert Path(result.completed[0]).name.startswith("housekeeper-appimage-")
    assert (tmp_path / "data/applications/org.example.App.desktop").exists()


@pytest.mark.parametrize("wm_class", [None, ""])
def test_missing_class_uses_embedded_desktop_id(appimage, wm_class):
    contents = "[Desktop Entry]\nType=Application\nName=Example\n"
    if wm_class is not None:
        contents += f"StartupWMClass={wm_class}\n"
    path = appimage({"org.example.App.desktop": contents})
    identity = read_appimage_identity(path)
    assert identity.desktop_id == "org.example.App.desktop"
    assert identity.wm_class == "org.example.App"


def test_ambiguous_root_launchers_are_not_guessed(appimage):
    contents = "[Desktop Entry]\nType=Application\nName=Example\n"
    assert (
        read_appimage_identity(appimage({"one.desktop": contents, "two.desktop": contents})) is None
    )


def test_missing_reader_and_truncated_images_are_optional(appimage, monkeypatch):
    path = appimage()
    monkeypatch.setattr("housekeeper.appimage_identity.shutil.which", lambda _: None)
    assert read_appimage_identity(path) is None
    path.write_bytes(image_bytes())
    assert read_appimage_identity(path) is None


def test_metadata_reader_bounds_output_and_time(monkeypatch):
    with pytest.raises(ValueError, match="too large"):
        _read_command([sys.executable, "-c", "print('a' * 100000)"])
    monkeypatch.setattr("housekeeper.appimage_identity.TIMEOUT", 0.05)
    with pytest.raises(ValueError, match="timed out"):
        _read_command([sys.executable, "-c", "import time; time.sleep(5)"])


@pytest.mark.parametrize("existing_class", ["", "UserChosen"])
def test_old_import_repairs_only_missing_window_class(
    appimage, tmp_path, monkeypatch, desktop, existing_class
):
    image = appimage()
    image.chmod(0o755)
    root = tmp_path / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(root))
    keys = {"StartupWMClass": existing_class} if existing_class else {}
    path = desktop(
        filename="housekeeper-appimage-old.desktop",
        root=root / "applications",
        Exec=str(image),
        Icon="application-x-executable",
        **{"X-Housekeeper-Created": "true", **keys},
    )
    picture = tmp_path / "icon.svg"
    picture.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" fill="red"/></svg>'
    )
    save_icon(read_entry(path, path.parent), picture)
    info = Gio.DesktopAppInfo.new_from_filename(str(path))
    assert info.get_startup_wm_class() == (existing_class or "ExampleApp")
    save_icon(read_entry(path, path.parent))
    assert Gio.DesktopAppInfo.new_from_filename(str(path)).get_startup_wm_class() == (
        existing_class or "ExampleApp"
    )
