import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from housekeeper.attribution import attribute
from housekeeper.identity import classify
from housekeeper.models import Action, AppRecord, Source, UpdateAction
from housekeeper.providers import deb
from housekeeper.storage import StorageUsage, measure_storage
from housekeeper.updates import assign_update_action, update_instructions


@pytest.fixture
def database(monkeypatch, entry):
    desktop = entry(("/usr/bin/example", "%U"))
    rows = {
        "packages": "example\t1:2.0-3\tamd64\tinstalled\t42\n",
        "owners": f"example:amd64: {desktop.path}\n",
    }
    calls = []

    def query(*args):
        calls.append(args)
        return rows["owners" if args[0] == "--search" else "packages"]

    monkeypatch.setattr(deb, "query", query)
    return desktop, rows, calls


def test_deb_attribution_size_and_manual_management(database):
    desktop, rows, calls = database
    app = classify(desktop)
    index = deb.DebIndex()
    app = attribute(app, (index,))
    assert (app.source, app.provider, app.scope) == (Source.DEB, "deb", "System")
    assert app.identity == "example:amd64"
    assert app.version == "1:2.0-3"
    assert app.software_size == 42 * 1024 and app.updated_at is None
    assert app.action == Action.NONE
    assert measure_storage(app) == StorageUsage(42 * 1024)
    assert calls[-1][-2:] == ("--", "example:amd64")
    assign_update_action(app, {})
    assert app.update_action == UpdateAction.INSTRUCTIONS
    assert "system package manager" in update_instructions(app)
    assert "dnf" not in update_instructions(app)
    key = app.key
    rows["packages"] = rows["packages"].replace("1:2.0-3", "1:2.0-4")
    assert measure_storage(app) == StorageUsage()
    updated = classify(desktop)
    updated = attribute(updated, (deb.DebIndex(),))
    assert updated.key == key


@pytest.mark.parametrize("status", ["config-files", "unpacked", "not-installed"])
def test_uninstalled_or_partial_packages_are_not_attributed(database, status):
    desktop, rows, _ = database
    rows["packages"] = rows["packages"].replace("installed", status)
    app = classify(desktop)
    app = attribute(app, (deb.DebIndex(),))
    assert app.source == Source.OTHER


@pytest.mark.parametrize("owners", ["example, another", "example:arm64", "unknown"])
def test_ambiguous_or_mismatched_owners_are_not_attributed(database, owners):
    desktop, rows, _ = database
    rows["owners"] = f"{owners}: {desktop.path}\n"
    app = classify(desktop)
    app = attribute(app, (deb.DebIndex(),))
    assert app.source == Source.OTHER


def test_multiarch_uses_exact_owner(database):
    desktop, rows, _ = database
    rows["packages"] += "example\t1:2.0-3\tarm64\tinstalled\t99\n"
    app = classify(desktop)
    app = attribute(app, (deb.DebIndex(),))
    assert app.identity == "example:amd64"
    assert measure_storage(app) == StorageUsage(42 * 1024)
    rows["owners"] = f"example: {desktop.path}\n"
    app = classify(desktop)
    app = attribute(app, (deb.DebIndex(),))
    assert app.source == Source.OTHER


def test_diverted_and_unowned_launchers_are_unknown(database):
    desktop, rows, _ = database
    rows["owners"] += f"diversion by other from: {desktop.path}\n"
    app = classify(desktop)
    app = attribute(app, (deb.DebIndex(),))
    assert app.source == Source.OTHER
    rows["owners"] = f"example: {desktop.path}.backup\n"
    app = attribute(app, (deb.DebIndex(),))
    assert app.source == Source.OTHER


@pytest.mark.parametrize(
    "source", [Source.WEB, Source.STEAM, Source.APPIMAGE, Source.FLATPAK, Source.RPM]
)
def test_guest_apps_and_other_providers_are_not_reclassified(database, source):
    desktop, _, calls = database
    app = AppRecord("app", "Example", source=source, entries=[desktop])
    app = attribute(app, (deb.DebIndex(),))
    assert app.source == (source if source in {Source.WEB, Source.STEAM} else Source.DEB)
    assert calls
    assert app.action == Action.NONE


def test_icon_override_uses_only_verified_source(database, monkeypatch):
    desktop, _, _ = database
    override = replace(desktop, path=desktop.path.parent / "override.desktop")
    monkeypatch.setattr("housekeeper.appearance.verified_icon_source", lambda _: desktop.path)
    app = classify(override)
    app = attribute(app, (deb.DebIndex(),))
    assert app.source == Source.DEB
    monkeypatch.setattr("housekeeper.appearance.verified_icon_source", lambda path: path)
    app = classify(override)
    app = attribute(app, (deb.DebIndex(),))
    assert app.source == Source.OTHER


@pytest.mark.parametrize("size,expected", [("0", 0), ("", None), ("-10", None), ("bad", None)])
def test_unknown_and_zero_sizes(database, size, expected):
    desktop, rows, _ = database
    rows["packages"] = rows["packages"].replace("\t42\n", "\t" + size + "\n")
    app = classify(desktop)
    app = attribute(app, (deb.DebIndex(),))
    assert app.source == Source.DEB
    assert measure_storage(app).software == expected
    assert app.software_size == expected


def test_index_batches_queries_across_launchers(database):
    desktop, _, calls = database
    index = deb.DebIndex()
    for _ in range(10):
        attribute(classify(desktop), (index,))
    assert len(calls) == 2


def test_query_is_offline_bounded_and_handles_missing_tool(monkeypatch):
    monkeypatch.setattr(deb.shutil, "which", lambda _: None)
    assert deb.query("--show") == ""
    monkeypatch.setattr(deb.shutil, "which", lambda _: "/usr/bin/dpkg-query")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(deb.subprocess, "run", run)
    assert deb.query("--search", "*.desktop") == ""
    argv, options = calls[0]
    assert argv == ["/usr/bin/dpkg-query", "--search", "*.desktop"]
    assert options["timeout"] == 10 and options["env"]["LC_ALL"] == "C"
    assert not options.get("shell")


def test_query_failure_gives_unknown_size(monkeypatch):
    def fail(*args):
        raise subprocess.TimeoutExpired("dpkg-query", 10)

    monkeypatch.setattr(deb, "query", fail)
    app = AppRecord(
        "app", "Example", source=Source.DEB, metadata={"name": "example", "arch": "amd64"}
    )
    assert measure_storage(app) == StorageUsage()


@pytest.mark.skipif(shutil.which("dpkg-query") is None, reason="dpkg-query is unavailable")
def test_real_dpkg_query_with_isolated_database(tmp_path, monkeypatch, desktop):
    from housekeeper.services import collect

    path = desktop(root=tmp_path / "applications", Exec="/usr/bin/true")
    db = tmp_path / "dpkg"
    (db / "info").mkdir(parents=True)
    (db / "status").write_text(
        "Package: example\nStatus: install ok installed\nArchitecture: amd64\n"
        "Version: 2.0-1\nInstalled-Size: 37\nMaintainer: Test <test@example.org>\n"
        "Description: Isolated storage fixture\n\n"
    )
    (db / "info/example.list").write_text(str(path) + "\n")
    monkeypatch.setenv("DPKG_ADMINDIR", str(db))
    monkeypatch.setattr("housekeeper.providers.rpm.RpmIndex.candidates", lambda *args: ())
    apps, warnings, *_ = collect(roots=[Path(path).parent])
    app = next(app for app in apps if app.source == Source.DEB)
    assert not warnings
    assert app.identity == "example:amd64"
    assert measure_storage(app) == StorageUsage(37 * 1024)
