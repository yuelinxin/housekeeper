"""Website launcher removal stays confined to the explicitly previewed desktop file."""

import os
from dataclasses import replace
from pathlib import Path

import pytest

from housekeeper.discovery import read_entry
from housekeeper.identity import classify
from housekeeper.installations import Installer, InstallRequest, desktop_exec
from housekeeper.models import (
    Action,
    FileOwnershipResult,
    FileOwnershipState,
    ManagementError,
    OperationCancelled,
    Outcome,
)
from housekeeper.providers.web import WebLauncherProvider
from housekeeper.services import collect
from housekeeper.web_identity import legacy_identifier


@pytest.fixture
def website(tmp_path, monkeypatch):
    monkeypatch.setattr("housekeeper.installations.website_icon", lambda *_: "web-browser")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    browser = tmp_path / "google-chrome"
    browser.write_text("#!/bin/sh\nexit 0\n")
    browser.chmod(0o755)
    monkeypatch.setattr("housekeeper.installations.shutil.which", lambda _name: str(browser))
    monkeypatch.setattr(
        "housekeeper.ownership.FileOwnershipIndex.query",
        lambda *_: FileOwnershipResult(FileOwnershipState.UNOWNED),
    )
    result = Installer().install(
        InstallRequest("web", 'https://example.org/a%20b?q="hi"&next=$x'), lambda *_: None
    )
    launcher = Path(result.completed[0])
    apps, *_ = collect(roots=[launcher.parent], indexes=[])
    assert apps[0].provider == "web-launcher" and apps[0].action == Action.UNINSTALL
    return apps[0], launcher, browser


def test_remove_only_launcher_and_preserve_browser_data_icons_and_other_apps(website, tmp_path):
    app, launcher, browser = website
    markers = [tmp_path / name for name in ("browser-data", "saved-icon.png", "other.desktop")]
    for marker in markers:
        marker.write_text("keep")
    trashed = tmp_path / "trashed-launcher"
    manager = WebLauncherProvider(trash=lambda path: path.rename(trashed))
    plan = manager.prepare(app, [app])
    assert plan.affected == (str(launcher),) and len(plan.files) == 1
    assert "Browser data is kept" in plan.message
    result = manager.execute(app, plan, lambda *_: None)
    assert result.outcome == Outcome.SUCCESS and result.completed == (str(launcher),)
    assert not launcher.exists() and trashed.exists() and browser.exists()
    assert all(marker.read_text() == "keep" for marker in markers)
    assert collect(roots=[launcher.parent], indexes=[])[0] == []


@pytest.mark.parametrize("change", ["content", "inode", "symlink", "marker", "command"])
def test_changed_launchers_never_reach_trash(website, tmp_path, change):
    app, launcher, _browser = website
    moved = []
    manager = WebLauncherProvider(trash=moved.append)
    plan = manager.prepare(app, [app])
    before = launcher.stat()
    data = launcher.read_text()
    if change == "inode":
        replacement = tmp_path / "replacement"
        replacement.write_text(data)
        replacement.replace(launcher)
    elif change == "symlink":
        original = tmp_path / "original"
        launcher.rename(original)
        launcher.symlink_to(original)
    elif change == "marker":
        launcher.write_text(
            data.replace("X-Housekeeper-Created=true", "X-Housekeeper-Created=false")
        )
    elif change == "command":
        launcher.write_text(data.replace("example.org", "changed.org"))
    else:
        # File-content comparison must detect same-size edits even with restored timestamps.
        launcher.write_text(data.replace("Name=example.org", "Name=changed.org"))
        os.utime(launcher, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ManagementError):
        manager.execute(app, plan, lambda *_: None)
    assert not moved and launcher.exists()


@pytest.mark.parametrize("change", ["foreign", "pwa", "unmarked", "renamed"])
def test_external_and_unverified_launchers_keep_external_management(website, tmp_path, change):
    _app, launcher, _browser = website
    if change == "foreign":
        directory = tmp_path / "other-applications"
        directory.mkdir()
        launcher = launcher.rename(directory / launcher.name)
    elif change == "renamed":
        launcher = launcher.rename(launcher.parent / "unrelated.desktop")
    else:
        data = launcher.read_text()
        if change == "unmarked":
            data = data.replace("X-Housekeeper-Created=true", "X-Housekeeper-Created=false")
        else:
            data = data.replace("--app=", "--app-id=")
        launcher.write_text(data)
    app = classify(read_entry(launcher, launcher.parent))
    with pytest.raises(ManagementError):
        WebLauncherProvider().prepare(app, [app])
    collected = collect(roots=[launcher.parent], indexes=[])[0][0]
    assert collected.action not in {Action.UNINSTALL, Action.TRASH}


@pytest.mark.parametrize("state", [FileOwnershipState.OWNED, FileOwnershipState.ERROR])
def test_package_ownership_is_checked_again_before_removal(website, state):
    app, launcher, _browser = website
    answer = FileOwnershipResult(FileOwnershipState.UNOWNED)
    moved = []
    manager = WebLauncherProvider(ownership=lambda _: answer, trash=moved.append)
    plan = manager.prepare(app, [app])
    answer = FileOwnershipResult(state)
    with pytest.raises(ManagementError):
        manager.execute(app, plan, lambda *_: None)
    assert not moved and launcher.exists()


def test_tampered_plan_does_not_remove_another_file(website):
    app, launcher, browser = website
    moved = []
    manager = WebLauncherProvider(trash=moved.append)
    plan = manager.prepare(app, [app])
    with pytest.raises(ManagementError):
        manager.execute(app, replace(plan, target=str(browser)), lambda *_: None)
    assert not moved and launcher.exists() and browser.exists()


def test_cancellation_and_trash_failure_keep_launcher(website):
    app, launcher, _browser = website

    def unavailable(_path):
        raise OSError("Trash unavailable")

    manager = WebLauncherProvider(trash=unavailable)
    plan = manager.prepare(app, [app])
    result = manager.execute(app, plan, lambda *_: None)
    assert result.outcome == Outcome.FAILED and not result.completed and launcher.exists()
    manager.request_cancel()
    with pytest.raises(OperationCancelled):
        manager.execute(app, plan, lambda *_: None)
    assert launcher.exists()


def test_legacy_launchers_remain_uninstallable(website):
    from gi.repository import GLib

    _app, launcher, browser = website
    url = 'https://example.org/a%20b?q="hi"&next=$x'
    data = GLib.KeyFile()
    data.load_from_file(str(launcher), GLib.KeyFileFlags.NONE)
    data.set_string("Desktop Entry", "Exec", desktop_exec((str(browser), "--app=" + url)))
    data.remove_key("Desktop Entry", "StartupWMClass")
    launcher.write_text(data.to_data()[0])
    launcher = launcher.rename(
        launcher.parent / (legacy_identifier(str(browser), url) + ".desktop")
    )
    app = collect(roots=[launcher.parent], indexes=[])[0][0]
    assert app.action == Action.UNINSTALL
    assert WebLauncherProvider().prepare(app, [app]).target == str(launcher)


def test_symlinked_data_home_still_allows_removal(website, tmp_path, monkeypatch):
    _app, launcher, _browser = website
    # Many systems link ~/.local/share to another disk; only the launcher itself matters.
    (tmp_path / "data").rename(tmp_path / "real-data")
    (tmp_path / "data").symlink_to(tmp_path / "real-data")
    apps, *_ = collect(roots=[launcher.parent], indexes=[])
    assert apps[0].provider == "web-launcher" and apps[0].action == Action.UNINSTALL
    trashed = []
    manager = WebLauncherProvider(trash=trashed.append)
    plan = manager.prepare(apps[0], apps)
    assert manager.execute(apps[0], plan, lambda *_: None).outcome == Outcome.SUCCESS
    assert trashed == [launcher]
