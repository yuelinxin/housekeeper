import subprocess
from pathlib import Path

import pytest

from housekeeper.models import FileOwnershipResult as Result
from housekeeper.models import FileOwnershipState as State
from housekeeper.ownership import FileOwnershipIndex
from housekeeper.providers import deb, packages


def test_negative_answers_must_be_complete_and_are_cached():
    calls = []

    def query(path):
        calls.append(path)
        return Result(State.UNOWNED)

    index = FileOwnershipIndex({"one": query, "absent": lambda p: Result(State.NOT_APPLICABLE)})
    assert index.query(Path("/example")).state == State.UNOWNED
    assert index.query(Path("/example")).state == State.UNOWNED and len(calls) == 1
    assert FileOwnershipIndex({}).query(Path("/example")).state == State.ERROR


def test_one_manager_cannot_hide_another_managers_owner_or_error():
    index = FileOwnershipIndex(
        {
            "rpm": lambda p: Result(State.UNOWNED),
            "deb": lambda p: Result(State.OWNED, ("deb:example",)),
        }
    )
    assert index.query(Path("/example")).owners == ("deb:example",)

    def fail(path):
        raise PermissionError("cannot read database")

    index = FileOwnershipIndex({"rpm": lambda p: Result(State.UNOWNED), "deb": fail})
    assert index.query(Path("/example")).state == State.ERROR
    assert "deb" in index.query(Path("/example")).reason


def test_pacman_queries_all_files_with_configured_root(tmp_path, monkeypatch):
    database, root = tmp_path / "db", tmp_path / "root"
    package = database / "example"
    package.mkdir(parents=True)
    (package / "desc").write_text("%NAME%\nexample\n\n%VERSION%\n1\n\n%ARCH%\nx86_64\n")
    (package / "files").write_text("%FILES%\nopt/example/program\n")
    monkeypatch.setattr(packages, "pacman_paths", lambda: (database, root))
    index = FileOwnershipIndex()
    assert index.pacman(root / "opt/example/program").state == State.OWNED
    assert index.pacman(Path("/opt/example/program")).state == State.UNOWNED
    (package / "files").unlink()
    fresh = FileOwnershipIndex()
    fresh.queries = {"pacman": fresh.pacman}
    assert fresh.query(root / "opt/example/program").state == State.ERROR


def test_apk_queries_files_and_rejects_corrupt_metadata(tmp_path, monkeypatch):
    database = tmp_path / "installed"
    database.write_text("P:example\nV:1\nA:x86_64\nF:opt/example\nR:program\n")
    monkeypatch.setattr(packages, "APK_DATABASE", database)
    assert FileOwnershipIndex().apk(Path("/opt/example/program")).state == State.OWNED
    database.write_text("corrupt metadata")
    index = FileOwnershipIndex()
    index.queries = {"apk": index.apk}
    assert index.query(Path("/opt/example/program")).state == State.ERROR


def test_dpkg_literal_path_and_diversion(monkeypatch):
    path = Path("/opt/a[1]*?.AppImage")
    calls = []
    monkeypatch.setattr(
        deb, "query", lambda *args: calls.append(args) or f"example: {path}\nother: /different\n"
    )
    assert deb.file_owners(path) == ("deb:example",)
    assert calls[0] == ("--search", "--", "/opt/a[[]1[]][*][?].AppImage")
    monkeypatch.setattr(deb, "query", lambda *args: f"local diversion from: {path}\n")
    with pytest.raises(ValueError, match="diverted"):
        deb.file_owners(path)


def test_dpkg_failure_is_not_an_empty_owner_list(monkeypatch):
    monkeypatch.setattr(deb.shutil, "which", lambda name: "/usr/bin/dpkg-query")
    monkeypatch.setattr(
        deb.subprocess,
        "run",
        lambda *args, **kw: subprocess.CompletedProcess(args, 1, "", "permission denied"),
    )
    with pytest.raises(subprocess.CalledProcessError):
        deb.file_owners(Path("/example"))


def test_flatpak_known_installation_boundary(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS

    root = tmp_path / "flatpak"
    root.mkdir()
    monkeypatch.setattr(
        "housekeeper.providers.flatpak.FlatpakIndex",
        lambda: NS(available=True, warnings=[], installations={str(root): object()}),
    )
    assert FileOwnershipIndex().flatpak(root / "app/program").state == State.OWNED
    assert FileOwnershipIndex().flatpak(tmp_path / "independent").state == State.UNOWNED
