from pathlib import Path

import pytest

from housekeeper.discovery import read_entry
from housekeeper.identity import classify
from housekeeper.models import ManagementError, Outcome
from housekeeper.providers.appimage import AppImageProvider


@pytest.fixture
def image_app(tmp_path, desktop):
    binary = tmp_path / "Example.AppImage"
    binary.write_bytes(b"independent test file, never executed")
    binary.chmod(0o755)
    entry_path = desktop(Exec=str(binary))
    app = classify(read_entry(entry_path, tmp_path, {"GNOME"}))
    return app


def provider(tmp_path, **kwargs):
    return AppImageProvider(home=tmp_path, ownership=lambda _p: [], **kwargs)


def test_only_exact_files_are_planned(image_app, tmp_path):
    config = tmp_path / ".config/example"
    config.mkdir(parents=True)
    plan = provider(tmp_path).prepare(image_app, [image_app])
    assert set(plan.affected) == {image_app.location, str(image_app.entries[0].path)}
    assert str(config) not in plan.affected


def test_package_owned_file_rejected(image_app, tmp_path):
    manager = AppImageProvider(home=tmp_path, ownership=lambda _p: ["owned-package"])
    with pytest.raises(ManagementError, match="software package"):
        manager.prepare(image_app, [image_app])


def test_changed_inode_rejected(image_app, tmp_path):
    manager = provider(tmp_path, trash=lambda p: p.unlink())
    plan = manager.prepare(image_app, [image_app])
    target = Path(image_app.location)
    replacement = target.with_suffix(".replacement")
    replacement.write_bytes(b"changed")
    replacement.replace(target)
    with pytest.raises(ManagementError, match="changed"):
        manager.execute(image_app, plan, lambda *_: None)
    assert target.exists()


def test_symlink_swap_rejected(image_app, tmp_path):
    manager = provider(tmp_path, trash=lambda p: p.unlink())
    plan = manager.prepare(image_app, [image_app])
    target = Path(image_app.location)
    target.unlink()
    target.symlink_to("/usr/bin/true")
    with pytest.raises(ManagementError):
        manager.execute(image_app, plan, lambda *_: None)
    assert target.is_symlink()


def test_trash_failure_does_not_delete_or_remove_launcher(image_app, tmp_path):
    def unavailable(_path):
        raise OSError("Trash unavailable")

    manager = provider(tmp_path, trash=unavailable)
    result = manager.execute(image_app, manager.prepare(image_app, [image_app]), lambda *_: None)
    assert result.outcome == Outcome.FAILED
    assert Path(image_app.location).exists()
    assert image_app.entries[0].path.exists()


def test_partial_success_is_reported(image_app, tmp_path):
    def move(path):
        if path.suffix == ".desktop":
            raise OSError("permission denied")
        path.rename(tmp_path / "trashed-app")

    manager = provider(tmp_path, trash=move)
    result = manager.execute(image_app, manager.prepare(image_app, [image_app]), lambda *_: None)
    assert result.outcome == Outcome.PARTIAL
    assert result.completed == (image_app.location,)
    assert image_app.entries[0].path.exists()


def test_shared_target_not_removable(image_app, tmp_path):
    from dataclasses import replace

    other = replace(image_app, key="different-launcher")
    with pytest.raises(ManagementError, match="share"):
        provider(tmp_path).prepare(image_app, [image_app, other])


def test_missing_ownership_backend_fails_closed(image_app, tmp_path, monkeypatch):
    class Missing:
        available = False

    monkeypatch.setattr("housekeeper.providers.rpm.RpmIndex", Missing)
    with pytest.raises(ManagementError, match="cannot be verified"):
        AppImageProvider(home=tmp_path).prepare(image_app, [image_app])


def test_target_outside_home_rejected(image_app, tmp_path):
    with pytest.raises(ManagementError, match="home directory"):
        provider(tmp_path / "another-home").prepare(image_app, [image_app])
