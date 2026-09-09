import json
import socketserver
import tempfile
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace

import pytest

from housekeeper.attribution import attribute
from housekeeper.identity import classify
from housekeeper.models import Action, AppRecord, Source, UpdateAction
from housekeeper.providers import packages, snap
from housekeeper.storage import StorageUsage, measure_storage
from housekeeper.updates import assign_update_action, update_instructions


@pytest.fixture
def pacman_db(tmp_path, monkeypatch):
    database = tmp_path / "local"
    directory = database / "editor-1.2-1"
    directory.mkdir(parents=True)
    (directory / "desc").write_text(
        "%NAME%\neditor\n\n%VERSION%\n1.2-1\n\n%ARCH%\nx86_64\n\n%SIZE%\n12345\n"
        "\n%INSTALLDATE%\n1735689600\n"
    )
    (directory / "files").write_text(
        "%FILES%\nusr/bin/editor\nusr/share/applications/editor.desktop\n\n"
        "%BACKUP%\netc/editor.conf\t12345\n"
    )
    monkeypatch.setattr(packages, "pacman_paths", lambda: (database, Path("/")))
    monkeypatch.setattr(packages, "APK_DATABASE", tmp_path / "no-apk")
    monkeypatch.setattr(snap, "SNAP_SOCKET", tmp_path / "no-snap")
    return directory


def test_pacman_source_size_and_stale_version(pacman_db, entry):
    app = classify(replace(entry(), path=Path("/usr/share/applications/editor.desktop")))
    app = attribute(app, (packages.PackageIndex(),))
    assert app.source == Source.PACMAN and app.version == "1.2-1"
    assert app.software_size == 12345 and app.updated_at == 1735689600
    assert measure_storage(app) == StorageUsage(12345)
    assert app.action == Action.NONE
    assign_update_action(app, {})
    assert app.update_action == UpdateAction.INSTRUCTIONS
    assert "system package manager" in update_instructions(app)
    assert "dnf" not in update_instructions(app)
    (pacman_db / "desc").write_text((pacman_db / "desc").read_text().replace("1.2-1", "1.2-2"))
    assert measure_storage(app) == StorageUsage()
    updated = classify(app.entries[0])
    updated = attribute(updated, (packages.PackageIndex(),))
    assert updated.key == app.key


@pytest.mark.parametrize("value,expected", [("0", 0), ("", None), ("-1", None), ("4 MB", None)])
def test_pacman_missing_or_invalid_size(pacman_db, value, expected):
    desc = pacman_db / "desc"
    desc.write_text(desc.read_text().replace("12345", value))
    assert packages.pacman_packages()[0].size == expected


def test_pacman_reads_local_size_not_repository_download_size(pacman_db):
    desc = pacman_db / "desc"
    desc.write_text(desc.read_text().replace("%SIZE%", "%ISIZE%") + "\n%CSIZE%\n99\n")
    assert packages.pacman_packages()[0].size is None


def test_pacman_does_not_replace_missing_last_updated_date_with_build_or_file_time(pacman_db):
    desc = pacman_db / "desc"
    desc.write_text(desc.read_text().replace("%INSTALLDATE%", "%BUILDDATE%"))
    assert packages.pacman_packages()[0].updated_at is None


def test_pacman_corrupt_package_does_not_hide_good_package(pacman_db):
    bad = pacman_db.parent / "broken"
    bad.mkdir()
    (bad / "desc").write_text("%NAME%\none\n%NAME%\ntwo\n")
    assert len(packages.pacman_packages()) == 1


def test_pacman_respects_configured_database_and_root(monkeypatch):
    calls = []
    monkeypatch.setattr(packages.shutil, "which", lambda _: "/usr/bin/pacman-conf")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout="/custom/db\n" if argv[-1] == "DBPath" else "/target\n")

    monkeypatch.setattr(packages.subprocess, "run", run)
    assert packages.pacman_paths() == (Path("/custom/db/local"), Path("/target"))
    assert all(options["check"] and options["timeout"] == 3 for _, options in calls)


@pytest.fixture
def apk_db(tmp_path, monkeypatch):
    database = tmp_path / "installed"
    database.write_text(
        "P:editor\nV:1.2-r0\nA:x86_64\nS:500\nI:8192\n"
        "F:usr/bin\nR:editor\nF:usr/share/applications\nR:editor.desktop\nZ:Q1abc\n\n"
        "P:other\nV:2.0-r1\nA:noarch\nI:4096\nF:opt/other\nR:other.desktop\n\n"
    )
    monkeypatch.setattr(packages, "APK_DATABASE", database)
    monkeypatch.setattr(packages, "pacman_paths", lambda: (tmp_path / "no-pacman", Path("/")))
    monkeypatch.setattr(snap, "SNAP_SOCKET", tmp_path / "no-snap")
    return database


def test_apk_installed_size_and_directory_file_attribution(apk_db, entry):
    app = classify(replace(entry(), path=Path("/usr/share/applications/editor.desktop")))
    app = attribute(app, (packages.PackageIndex(),))
    assert app.source == Source.APK and app.identity == "editor:x86_64"
    assert app.software_size == 8192 and app.updated_at is None
    assert measure_storage(app) == StorageUsage(8192)
    assert packages.apk_packages()[1].desktops == ("/opt/other/other.desktop",)
    apk_db.write_text(apk_db.read_text().replace("V:1.2-r0", "V:1.2-r1"))
    assert measure_storage(app) == StorageUsage()


@pytest.mark.parametrize("extra", ["f:f\n", "P:conflicting\n", "invalid\n"])
def test_broken_apk_records_are_not_attributed(apk_db, extra):
    apk_db.write_text(apk_db.read_text().replace("P:editor\n", "P:editor\n" + extra))
    assert [package.name for package in packages.apk_packages()] == ["other"]


@pytest.mark.parametrize(
    "path", ["/absolute.desktop", "../escape.desktop", "a/../../x.desktop", "bin/program"]
)
def test_database_paths_do_not_escape_root(path):
    assert packages.desktop_path(path) is None


@pytest.mark.parametrize(
    "source", [Source.WEB, Source.STEAM, Source.APPIMAGE, Source.FLATPAK, Source.RPM, Source.DEB]
)
def test_candidate_queries_do_not_trust_previous_source(source, monkeypatch, entry):
    calls = []
    monkeypatch.setattr(packages, "load_packages", lambda source: calls.append(source) or [])
    app = AppRecord("app", "Example", source=source, entries=[entry()])
    app = attribute(app, (packages.PackageIndex(),))
    assert calls
    assert app.action != Action.UNINSTALL


def test_ambiguous_owners_and_unowned_wrappers_remain_unknown(pacman_db, monkeypatch, entry):
    package = packages.pacman_packages()[0]
    monkeypatch.setattr(
        packages,
        "load_packages",
        lambda source: (
            [package, replace(package, name="different")] if source == Source.PACMAN else []
        ),
    )
    app = classify(replace(entry(), path=Path(package.desktops[0])))
    app = attribute(app, (packages.PackageIndex(),))
    assert app.source == Source.OTHER
    app = classify(entry(("/usr/bin/editor",)))
    app = attribute(app, (packages.PackageIndex(),))
    assert app.source == Source.OTHER


def test_provider_failure_does_not_hide_other_formats(apk_db, monkeypatch, entry):
    def unavailable():
        raise PermissionError("Unavailable Pacman database")

    monkeypatch.setattr(packages, "pacman_packages", unavailable)
    index = packages.PackageIndex()
    app = classify(replace(entry(), path=Path("/usr/share/applications/editor.desktop")))
    app = attribute(app, (index,))
    assert app.source == Source.APK
    assert len(index.warnings) == 1


def test_verified_icon_override_preserves_package_ownership(pacman_db, monkeypatch, entry):
    monkeypatch.setattr(
        "housekeeper.appearance.verified_icon_source",
        lambda _: Path("/usr/share/applications/editor.desktop"),
    )
    app = classify(entry())
    app = attribute(app, (packages.PackageIndex(),))
    assert app.source == Source.PACMAN
    assert measure_storage(app).software == 12345


def snap_info(**kwargs):
    return {
        "name": "editor_test",
        "version": "1.0",
        "revision": "12",
        "status": "active",
        "type": "app",
        "installed-size": 123456,
        "install-date": "2025-01-01T00:00:00Z",
        "apps": [{"desktop-file": "/var/lib/snapd/desktop/applications/editor_test.desktop"}],
        **kwargs,
    }


@pytest.fixture
def snap_api(monkeypatch, tmp_path):
    marker = tmp_path / "snap.socket"
    marker.touch()
    monkeypatch.setattr(snap, "SNAP_SOCKET", marker)
    response = {"status": 200, "body": {"type": "sync", "result": [snap_info()]}}
    calls = []

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args):
            calls.append(args)

        def getresponse(self):
            return SimpleNamespace(
                status=response["status"],
                read=lambda limit: json.dumps(response["body"]).encode()[:limit],
            )

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(snap, "SnapConnection", Connection)
    return response, calls


def test_snap_reads_enabled_revision_and_excludes_runtimes(snap_api, monkeypatch, tmp_path, entry):
    response, calls = snap_api
    response["body"]["result"] += [
        snap_info(status="installed"),
        snap_info(type="base"),
        snap_info(type="kernel"),
    ]
    monkeypatch.setattr(packages, "pacman_paths", lambda: (tmp_path / "absent", Path("/")))
    monkeypatch.setattr(packages, "APK_DATABASE", tmp_path / "absent-apk")
    app = classify(replace(entry(), path=Path(snap_info()["apps"][0]["desktop-file"])))
    app = attribute(app, (packages.PackageIndex(),))
    assert app.source == Source.SNAP and app.identity == "editor_test"
    assert app.software_size == 123456 and app.updated_at == 1735689600
    assert measure_storage(app) == StorageUsage(123456)
    assert calls[:2] == [("GET", "/v2/snaps"), "closed"]
    response["body"]["result"][0]["revision"] = "13"
    assert measure_storage(app) == StorageUsage()


def test_snap_failed_response_and_invalid_payload(snap_api):
    response, calls = snap_api
    response["status"] = 500
    with pytest.raises(ValueError):
        snap.snap_packages()
    assert calls[-1] == "closed"
    response["status"], response["body"] = 200, []
    with pytest.raises(ValueError):
        snap.snap_packages()


@pytest.mark.parametrize("value", [True, -1, 2**64, "unknown", None])
def test_invalid_sizes_remain_unknown(value):
    assert packages.byte_size(value) is None


def test_snap_real_local_socket_transport(monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            body = json.dumps({"type": "sync", "result": [snap_info()]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with tempfile.TemporaryDirectory(prefix="hk-snap-") as directory:
        path = Path(directory) / "socket"
        monkeypatch.setattr(snap, "SNAP_SOCKET", path)
        try:
            server = socketserver.UnixStreamServer(str(path), Handler)
        except PermissionError:
            pytest.skip("This sandbox does not allow Unix sockets")
        with server:
            server.timeout = 3
            worker = threading.Thread(target=server.handle_request)
            worker.start()
            try:
                assert snap.snap_packages()[0].size == 123456
                assert requests == ["/v2/snaps"]
            finally:
                worker.join(timeout=5)
