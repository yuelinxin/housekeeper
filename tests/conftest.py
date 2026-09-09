import os
import shutil
from pathlib import Path

import pytest

from housekeeper.models import DesktopEntry


@pytest.fixture
def desktop(tmp_path):
    def make(name="Example", filename="example.desktop", root=None, **keys):
        root = tmp_path if root is None else root
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = {"Type": "Application", "Name": name, "Exec": "/usr/bin/true", **keys}
        path.write_text(
            "[Desktop Entry]\n" + "\n".join(f"{k}={v}" for k, v in fields.items()) + "\n"
        )
        return path

    return make


@pytest.fixture
def entry(tmp_path):
    def make(argv=("/usr/bin/true",), desktop_id="example.desktop", **kwargs):
        if argv and Path(argv[0]).name == "flatpak":
            kwargs.setdefault(
                "resolved_executable", str(Path(shutil.which(argv[0]) or argv[0]).resolve())
            )
        return DesktopEntry(
            desktop_id,
            tmp_path / desktop_id,
            "Example",
            argv=argv,
            executable=argv[0] if argv else "",
            **kwargs,
        )

    return make


@pytest.fixture
def flatpak_command(tmp_path, monkeypatch):
    from housekeeper.providers import flatpak_attribution

    directory = tmp_path / "manager"
    directory.mkdir()
    binary = directory / "flatpak"
    binary.write_text("#!/bin/sh\nexit 99\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ.get("PATH", os.defpath))
    monkeypatch.setattr(flatpak_attribution, "MANAGER_SEARCH_PATH", str(directory))
    return binary
