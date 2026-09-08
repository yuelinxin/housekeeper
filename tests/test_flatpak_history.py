import json
import subprocess
from types import SimpleNamespace

import pytest

from housekeeper.models import AppRecord, Source
from housekeeper.providers import flatpak_history as history

REF = "app/org.example.Editor/x86_64/stable"
COMMIT = "a" * 64
TIME = 1735689600


def event(name="system", operation="deploy update", timestamp=TIME, **fields):
    return {
        "INSTALLATION": name,
        "REF": REF,
        "COMMIT": COMMIT,
        "OPERATION": operation,
        "__REALTIME_TIMESTAMP": str(timestamp * 1_000_000 + 123456),
        "_UID": "1000",
        **fields,
    }


def installation(identity="default", user=False):
    return SimpleNamespace(get_id=lambda: identity, get_is_user=lambda: user)


def app(path="/flatpak", ref=REF):
    return AppRecord(
        path + ref,
        "Editor",
        source=Source.FLATPAK,
        provider="flatpak",
        identity=ref,
        metadata={"installation": path, "commit": COMMIT},
    )


@pytest.fixture
def journal(monkeypatch):
    records = []
    monkeypatch.setattr(history, "read_history", lambda: records)
    monkeypatch.setattr(history.os, "getuid", lambda: 1000)
    return records


def test_latest_matching_deployment_uses_full_timestamp(journal):
    current = app()
    journal.extend([event(timestamp=TIME + 10), event(operation="deploy install")])
    history.enrich_history([current], {"/flatpak": installation()})
    assert current.updated_at == TIME + 10


def test_scope_custom_installation_branch_architecture_and_user_are_separate(journal):
    apps = [app(path) for path in ("/flatpak", "/user", "/extra")]
    apps.extend([app(ref=REF.replace("stable", "beta")), app(ref=REF.replace("x86_64", "aarch64"))])
    journal.extend(
        [
            event("user", timestamp=TIME + 99, _UID="2000"),
            event("user", timestamp=TIME + 3),
            event("system (extra)", timestamp=TIME + 2),
            event("system", timestamp=TIME + 1),
        ]
    )
    history.enrich_history(
        apps,
        {
            "/flatpak": installation(),
            "/user": installation("user", True),
            "/extra": installation("extra"),
        },
    )
    assert [current.updated_at for current in apps] == [TIME + 1, TIME + 3, TIME + 2, None, None]


@pytest.mark.parametrize(
    "latest",
    [
        event(operation="uninstall"),
        event(COMMIT="b" * 64),
        event(COMMIT=COMMIT[:12]),
        event(__REALTIME_TIMESTAMP="invalid"),
        event(__REALTIME_TIMESTAMP=[str(TIME * 1_000_000)]),
        event(__REALTIME_TIMESTAMP="9" * 5000),
    ],
)
def test_latest_change_blocks_fallback_to_old_deployment(journal, latest):
    current = app()
    journal.extend([latest, event(timestamp=TIME - 10)])
    history.enrich_history([current], {"/flatpak": installation()})
    assert current.updated_at is None


def test_reinstall_of_same_commit_gets_new_time_and_pulls_are_not_updates(journal):
    current = app()
    journal.extend(
        [
            event(operation="pull", timestamp=TIME + 100),
            event(operation="deploy install", timestamp=TIME + 10),
            event(operation="uninstall"),
            event(timestamp=TIME - 10),
        ]
    )
    history.enrich_history([current], {"/flatpak": installation()})
    assert current.updated_at == TIME + 10
    journal[:] = [event(operation="pull local")]
    history.enrich_history([current], {"/flatpak": installation()})
    assert current.updated_at is None


def test_ambiguous_installation_names_and_missing_history_remain_unknown(journal):
    current = app()
    journal.append(event())
    history.enrich_history([current], {"/flatpak": installation(), "/other": installation()})
    assert current.updated_at is None
    journal.clear()
    history.enrich_history([current], {"/flatpak": installation()})
    assert current.updated_at is None


def test_no_apps_does_not_read_journal(monkeypatch):
    monkeypatch.setattr(history, "read_history", lambda: pytest.fail("Unexpected journal query"))
    history.enrich_history([], {"/flatpak": installation()})


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(history.shutil, "which", lambda _: "/usr/bin/journalctl")
    response = {"payload": json.dumps(event()).encode() + b"\n"}
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "error" in response:
            raise response["error"]
        kwargs["stdout"].write(response["payload"])

    monkeypatch.setattr(history.subprocess, "run", run)
    return response, calls


def test_journal_query_is_filtered_bounded_and_keeps_newest_order(transport):
    response, calls = transport
    response["payload"] += b"bad json\n[]\n\xff\n" + json.dumps(event(timestamp=TIME - 10)).encode()
    assert history.read_history() == [event(), event(timestamp=TIME - 10)]
    argv, options = calls[0]
    assert f"MESSAGE_ID={history.MESSAGE_ID}" in argv and "--reverse" in argv
    assert f"--lines={history.MAX_RECORDS}" in argv
    assert all(f"OPERATION={operation}" in argv for operation in history.OPERATIONS)
    assert "--output=json" in argv and "--user" not in argv
    assert options["check"] and options["timeout"] == 3
    assert options["stdin"] == subprocess.DEVNULL and options["env"]["LC_ALL"] == "C"
    assert not options.get("shell")


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(),
        subprocess.TimeoutExpired("journalctl", 3),
        subprocess.CalledProcessError(1, "journalctl"),
    ],
)
def test_unavailable_journal_returns_no_history(transport, error):
    response, _ = transport
    response["error"] = error
    assert history.read_history() == []


def test_missing_command_and_oversized_output_return_no_history(transport, monkeypatch):
    response, calls = transport
    monkeypatch.setattr(history, "MAX_BYTES", 10)
    assert history.read_history() == []
    calls.clear()
    monkeypatch.setattr(history.shutil, "which", lambda _: None)
    assert history.read_history() == [] and not calls
