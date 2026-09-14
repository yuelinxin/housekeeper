import os
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from test_transactions import Transaction

from housekeeper.models import AppRecord, ManagementError, Outcome, Source
from housekeeper.providers import flatpak_data
from housekeeper.providers.flatpak import FlatpakProvider


@pytest.fixture
def data_fixture(tmp_path, monkeypatch):
    from gi.repository import GLib

    from housekeeper.providers.flatpak import load_flatpak

    try:
        load_flatpak()
    except (ImportError, ValueError):
        pytest.skip("libflatpak introspection is unavailable")

    monkeypatch.setattr(GLib, "get_home_dir", lambda: str(tmp_path))
    app = AppRecord(
        "flatpak",
        "Example",
        source=Source.FLATPAK,
        provider="flatpak",
        identity="app/org.example.App/x86_64/stable",
        metadata={"installation": "/example/flatpak", "app_id": "org.example.App"},
    )
    data = tmp_path / ".var/app/org.example.App"
    (data / "config").mkdir(parents=True)
    (data / "config/settings").write_text("personal settings")
    other = data.with_name("org.example.Other")
    other.mkdir()
    (other / "keep").write_text("unrelated data")
    calls = []
    monkeypatch.setattr(flatpak_data.shutil, "which", lambda _: "/usr/bin/flatpak")
    monkeypatch.setattr(
        flatpak_data.subprocess,
        "run",
        lambda argv, **kwargs: (calls.append(argv), NS(returncode=0, stderr=""))[1],
    )
    return app, data, other, calls


def test_cleanup_deletes_only_selected_data_and_resets_exact_app_permissions(data_fixture):
    app, data, other, calls = data_fixture
    (data / "external").symlink_to(other, target_is_directory=True)
    command = flatpak_data.cleanup_command(app)
    assert data.exists() and not calls  # Preflight must never delete anything.
    flatpak_data.delete_user_data(app, command)
    assert not data.exists()
    assert (other / "keep").read_text() == "unrelated data"
    assert calls == [("/usr/bin/flatpak", "permission-reset", "--", "org.example.App")]


def test_absent_data_still_resets_permissions(data_fixture):
    app, data, other, calls = data_fixture
    app = replace(
        app,
        identity="app/org.example.Absent/x86_64/stable",
        metadata={**app.metadata, "app_id": "org.example.Absent"},
    )
    flatpak_data.delete_user_data(app, flatpak_data.cleanup_command(app))
    assert data.exists() and other.exists() and len(calls) == 1


def test_symlinked_app_data_never_deletes_target(data_fixture):
    app, data, other, calls = data_fixture
    moved = data.with_name("saved")
    data.rename(moved)
    data.symlink_to(moved, target_is_directory=True)
    flatpak_data.delete_user_data(app, flatpak_data.cleanup_command(app))
    assert not data.is_symlink()
    assert (moved / "config/settings").read_text() == "personal settings"


def test_symlinked_parent_is_rejected(data_fixture):
    app, data, other, calls = data_fixture
    parent = data.parent
    moved = parent.with_name("moved")
    parent.rename(moved)
    parent.symlink_to(moved, target_is_directory=True)
    with pytest.raises(OSError):
        flatpak_data.delete_user_data(app, flatpak_data.cleanup_command(app))
    assert (data / "config/settings").exists() and not calls


@pytest.mark.parametrize(
    "identity",
    [
        "../../outside",
        "runtime/org.example.App/x86_64/stable",
        "app/org.example.Other/x86_64/stable",
    ],
)
def test_invalid_or_mismatched_app_id_cannot_authorize_cleanup(data_fixture, identity):
    from gi.repository import GLib

    app, data, other, calls = data_fixture
    with pytest.raises((GLib.Error, ManagementError)):
        flatpak_data.cleanup_command(replace(app, identity=identity))
    assert data.exists() and other.exists() and not calls


@pytest.fixture
def removal(data_fixture, monkeypatch):
    app, data, other, calls = data_fixture
    provider = FlatpakProvider()
    transactions = []

    def transaction(_app):
        tx = Transaction(app.identity)
        transactions.append(tx)
        return NS(TransactionOperationType=NS(UNINSTALL=2)), tx, "commit"

    monkeypatch.setattr(provider, "_transaction", transaction)
    plan = provider.prepare(app, [app])
    return provider, plan, transactions


@pytest.mark.parametrize("delete", [False, True])
def test_only_explicit_choice_deletes_data_after_uninstall(data_fixture, removal, delete):
    app, data, other, calls = data_fixture
    provider, plan, transactions = removal
    assert not plan.delete_user_data and data.exists()
    result = provider.execute(app, replace(plan, delete_user_data=delete), lambda *_: None)
    assert result.outcome == Outcome.SUCCESS and transactions[-1].executed
    assert data.exists() != delete
    assert bool(calls) == delete
    assert other.exists()


@pytest.mark.parametrize("failure", ["cancel", "stale", "error", "false", "no-ready"])
def test_unsuccessful_uninstall_never_deletes_data(data_fixture, removal, monkeypatch, failure):
    app, data, other, calls = data_fixture
    provider, plan, transactions = removal
    plan = replace(plan, delete_user_data=True)
    if failure == "cancel":
        provider.request_cancel()
        assert provider.execute(app, plan, lambda *_: None).outcome == Outcome.CANCELLED
    else:
        if failure == "stale":
            plan = replace(plan, fingerprint="changed")
        else:

            def run(tx, _cancel):
                if failure == "no-ready":
                    return True
                assert tx.handlers["ready"](tx)
                if failure == "error":
                    raise RuntimeError("uninstall failed")
                return False

            monkeypatch.setattr(Transaction, "run", run)
        with pytest.raises((ManagementError, RuntimeError)):
            provider.execute(app, plan, lambda *_: None)
    assert data.exists() and other.exists() and not calls


def test_missing_cli_stops_before_uninstall(data_fixture, removal, monkeypatch):
    app, data, other, calls = data_fixture
    provider, plan, transactions = removal
    monkeypatch.setattr(flatpak_data.shutil, "which", lambda _: None)
    with pytest.raises(ManagementError, match="Flatpak is required"):
        provider.execute(app, replace(plan, delete_user_data=True), lambda *_: None)
    assert len(transactions) == 1 and data.exists() and not calls


@pytest.mark.parametrize("failure", ["permissions", "data"])
def test_cleanup_failure_reports_completed_uninstall_as_partial(
    data_fixture, removal, monkeypatch, failure
):
    app, data, other, calls = data_fixture
    provider, plan, transactions = removal
    if failure == "permissions":
        monkeypatch.setattr(
            flatpak_data.subprocess,
            "run",
            lambda *_a, **_kw: NS(returncode=1, stderr="Permission store unavailable"),
        )
    else:

        def denied(*_args):
            raise PermissionError("Cannot delete user data")

        monkeypatch.setattr(flatpak_data, "_remove_directory", denied)
    result = provider.execute(app, replace(plan, delete_user_data=True), lambda *_: None)
    assert transactions[-1].executed
    assert result.outcome == Outcome.PARTIAL and result.completed == (app.identity,)
    assert result.errors and other.exists()


def test_directory_swap_cannot_follow_an_outside_symlink(data_fixture, monkeypatch):
    app, data, other, calls = data_fixture
    original_open = os.open

    def swapped(path, flags, **kwargs):
        if path == "config":
            (data / "config").rename(data / "saved")
            (data / "config").symlink_to(other, target_is_directory=True)
        return original_open(path, flags, **kwargs)

    monkeypatch.setattr(flatpak_data.os, "open", swapped)
    with pytest.raises(OSError):
        flatpak_data.delete_user_data(app, flatpak_data.cleanup_command(app))
    assert (other / "keep").read_text() == "unrelated data" and not calls
