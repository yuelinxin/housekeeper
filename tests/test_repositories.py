"""Repository identity, authorization failures and reviewed imports; no host configuration changes."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
from urllib.request import Request

import pytest
from gi.repository import Gio

from housekeeper import repositories as module
from housekeeper.models import ManagementError, OperationCancelled
from housekeeper.repositories import (
    FlatpakSourcePlan,
    RepositoryManager,
    SoftwareSource,
    _read_repository_file,
)


def remote(name="flathub", url="https://example.org/repo", enabled=True):
    value = Mock()
    value.get_name.return_value = name
    value.get_title.return_value = "Example Apps"
    value.get_url.return_value = url
    value.get_gpg_verify.return_value = True
    value.get_disabled.return_value = not enabled
    value.set_disabled.side_effect = lambda disabled: setattr(
        value.get_disabled, "return_value", disabled
    )
    return value


def installation(path, source, user=True):
    return NS(
        get_path=lambda: Gio.File.new_for_path(path),
        get_is_user=lambda: user,
        get_id=lambda: "default",
        list_remotes=Mock(return_value=[source] if source else []),
        modify_remote=Mock(return_value=True),
        add_remote=Mock(return_value=True),
    )


@pytest.fixture
def flatpak(monkeypatch):
    user_remote, system_remote = remote(), remote()
    user = installation("/user/flatpak", user_remote)
    system = installation("/system/flatpak", system_remote, False)
    fp = NS(Remote=NS(new_from_file=Mock(return_value=remote("new-source"))))
    monkeypatch.setattr(module, "load_flatpak", lambda: fp)
    monkeypatch.setattr(module, "configured_installations", lambda _fp: ([user, system], []))
    monkeypatch.setattr(
        module, "package_client", Mock(side_effect=ManagementError("Native unavailable"))
    )
    return fp, user, system, user_remote, system_remote


def test_partial_listing_and_separate_flatpak_installations(flatpak):
    groups = RepositoryManager().list_sources()
    assert len(groups) == 3 and groups[0].error == "Native unavailable"
    assert groups[1].title == "Flatpak — User" and groups[1].can_add
    assert groups[2].title == "Flatpak — System (default)"
    assert groups[1].sources[0].identifier == groups[2].sources[0].identifier == "flathub"
    assert groups[1].sources[0].context != groups[2].sources[0].context


def test_flatpak_toggle_preserves_other_installation_and_configuration(flatpak):
    _fp, user, system, user_remote, system_remote = flatpak
    manager = RepositoryManager()
    source = manager.list_sources()[1].sources[0]
    manager.set_enabled(source, False)
    user.modify_remote.assert_called_once_with(user_remote, manager.cancel)
    assert user_remote.get_disabled()
    assert user_remote.get_url() == source.url and user_remote.get_gpg_verify()
    system.modify_remote.assert_not_called()
    system_remote.set_disabled.assert_not_called()


@pytest.mark.parametrize("change", ["url", "enabled", "missing", "installation"])
def test_stale_flatpak_source_is_not_modified(flatpak, monkeypatch, change):
    _fp, user, system, user_remote, _system_remote = flatpak
    manager = RepositoryManager()
    source = manager.list_sources()[1].sources[0]
    if change == "url":
        user_remote.get_url.return_value = "https://changed.org/repo"
    elif change == "enabled":
        user_remote.get_disabled.return_value = True
    elif change == "missing":
        user.list_remotes.return_value = []
    else:
        monkeypatch.setattr(module, "configured_installations", lambda _fp: ([system], []))
    with pytest.raises(ManagementError, match="changed"):
        manager.set_enabled(source, False)
    user.modify_remote.assert_not_called()


def native(monkeypatch, *, denied=False):
    repo = NS(
        get_id=lambda: "updates", get_description=lambda: "System Updates", get_enabled=lambda: True
    )
    pk = NS(ExitEnum=NS(SUCCESS=1, CANCELLED=2))
    listed = NS(
        get_exit_code=lambda: 1,
        get_error_code=lambda: None,
        get_package_array=lambda: [],
        get_repo_detail_array=lambda: [repo],
    )
    changed = NS(
        get_exit_code=lambda: 1,
        get_error_code=lambda: NS(get_details=lambda: "Authorization denied") if denied else None,
        get_package_array=lambda: [],
    )
    client = NS(get_repo_list=Mock(return_value=listed), repo_enable=Mock(return_value=changed))
    monkeypatch.setattr(module, "package_client", lambda _source: (client, pk))
    return client, repo


@pytest.mark.parametrize("denied", [False, True])
def test_packagekit_toggle_uses_exact_repository_and_reports_denial(monkeypatch, denied):
    client, _repo = native(monkeypatch, denied=denied)
    source = SoftwareSource("native", "updates", "System Updates", True)
    manager = RepositoryManager()
    if denied:
        with pytest.raises(ManagementError, match="Authorization denied"):
            manager.set_enabled(source, False)
    else:
        manager.set_enabled(source, False)
    assert client.repo_enable.call_args.args[:2] == ("updates", False)
    assert callable(client.repo_enable.call_args.args[3])
    assert callable(client.get_repo_list.call_args.args[2])


def test_native_changed_repository_and_cancellation_never_write(monkeypatch):
    client, repo = native(monkeypatch)
    source = SoftwareSource("native", "updates", "Old source", True)
    with pytest.raises(ManagementError, match="changed"):
        RepositoryManager().set_enabled(source, False)
    manager = RepositoryManager()
    manager.request_cancel()
    with pytest.raises(OperationCancelled):
        manager.set_enabled(replace(source, title=repo.get_description()), False)
    client.repo_enable.assert_not_called()


def test_import_uses_reviewed_bytes_and_does_not_overwrite(flatpak, tmp_path):
    fp, user, _system, _r1, _r2 = flatpak
    path = tmp_path / "new-source.flatpakrepo"
    original = b"[Flatpak Repo]\nUrl=https://example.org/repo\nTitle=Example Apps\n"
    path.write_bytes(original)
    manager = RepositoryManager()
    plan = manager.prepare_flatpak_source("/user/flatpak", str(path))
    assert plan.name == "new-source" and plan.data == original and plan.verified
    path.write_bytes(b"changed after review")
    manager.add_flatpak_source(plan)
    assert fp.Remote.new_from_file.call_args.args[1].get_data() == original
    fp.Remote.new_from_file.return_value.set_gpg_verify.assert_called_with(True)
    user.add_remote.assert_called_once_with(
        fp.Remote.new_from_file.return_value, False, manager.cancel
    )
    user.list_remotes.return_value.append(remote("new-source"))
    with pytest.raises(ManagementError, match="already exists"):
        manager.add_flatpak_source(plan)
    assert user.add_remote.call_count == 1


@pytest.mark.parametrize("name", ["../source", "--test", "has space", "line\nbreak"])
def test_import_rejects_bad_names(flatpak, tmp_path, name):
    path = tmp_path / "source.flatpakrepo"
    path.write_bytes(b"fixture")
    with pytest.raises(ManagementError, match="source name"):
        RepositoryManager().prepare_flatpak_source("/user/flatpak", str(path), name)


def test_import_rechecks_preview_before_saving(flatpak):
    _fp, user, *_ = flatpak
    plan = FlatpakSourcePlan(
        "/user/flatpak", "new-source", "New Source", "https://unexpected.org", True, b"fixture"
    )
    with pytest.raises(ManagementError, match="details changed"):
        RepositoryManager().add_flatpak_source(plan)
    user.add_remote.assert_not_called()


@pytest.mark.parametrize(
    "location",
    [
        "http://example.org/repo.flatpakrepo",
        "ftp://example.org/repo.flatpakrepo",
        "https://user:password@example.org/repo.flatpakrepo",
    ],
)
def test_import_rejects_non_https_and_credentials(location):
    with pytest.raises(ManagementError, match="HTTPS"):
        _read_repository_file(location)


def test_import_bounds_file_and_https_response(tmp_path, monkeypatch):
    path = tmp_path / "large.flatpakrepo"
    path.write_bytes(b"x" * (module.MAX_REPO_SIZE + 1))
    with pytest.raises(ManagementError, match="1 MB"):
        _read_repository_file(str(path))
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b"fixture"
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(module, "build_opener", Mock(return_value=opener))
    assert _read_repository_file("https://example.org/repo.flatpakrepo") == b"fixture"
    response.read.assert_called_once_with(module.MAX_REPO_SIZE + 1)


@pytest.mark.parametrize("target", ["http://mirror.example.org/hop", "ftp://example.org/x"])
def test_every_redirect_hop_must_stay_on_https(target):
    # Checking only the final URL would let one plain-HTTP hop substitute the file.
    request = Request("https://example.org/repo.flatpakrepo")
    redirects = module._HttpsRedirects()
    with pytest.raises(ManagementError, match="insecure"):
        redirects.redirect_request(request, None, 302, "Found", {}, target)
    followed = redirects.redirect_request(
        request, None, 302, "Found", {}, "https://mirror.example.org/repo.flatpakrepo"
    )
    assert followed.full_url == "https://mirror.example.org/repo.flatpakrepo"


def test_missing_flatpak_does_not_hide_native_sources(monkeypatch):
    native(monkeypatch)
    monkeypatch.setattr(module, "load_flatpak", Mock(side_effect=ValueError("Missing typelib")))
    groups = RepositoryManager().list_sources()
    assert groups[0].sources[0].identifier == "updates"
    assert "not installed" in groups[1].error


def test_import_rejects_fifo_without_waiting(tmp_path):
    path = tmp_path / "source.flatpakrepo"
    module.os.mkfifo(path)
    with pytest.raises(ManagementError, match="regular"):
        _read_repository_file(str(path))


def test_uncommitted_remote_verification_default_is_explicit():
    try:
        fp = module.load_flatpak()
    except (ImportError, ValueError):
        pytest.skip("libflatpak introspection is unavailable")
    remote = module._remote_from_file(
        fp, "source", b"[Flatpak Repo]\nUrl=https://example.org/repo\n"
    )
    assert remote.get_gpg_verify()


@pytest.mark.parametrize(
    "line, verified", [("GPGVerify=false\n", False), ("GPGVerify=true\n", True), ("", True)]
)
def test_review_reports_the_verification_the_file_declares(monkeypatch, line, verified):
    try:
        fp = module.load_flatpak()
    except (ImportError, ValueError):
        pytest.skip("libflatpak introspection is unavailable")
    data = b"[Flatpak Repo]\nUrl=https://example.org/repo\n" + line.encode()
    assert module._remote_from_file(fp, "source", data).get_gpg_verify() == verified
