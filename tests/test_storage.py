from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from housekeeper.models import AppRecord, Source
from housekeeper.services import InventoryService
from housekeeper.storage import StorageUsage, measure_storage


def test_appimage_size_and_missing_file(tmp_path):
    image = tmp_path / "Example.AppImage"
    image.write_bytes(b"12345")
    app = AppRecord("image", "Example", source=Source.APPIMAGE, location=str(image))
    assert measure_storage(app) == StorageUsage(5)
    image.unlink()
    assert measure_storage(app) == StorageUsage()


@pytest.mark.parametrize("source", [Source.OTHER, Source.WEB, Source.STEAM])
def test_unknown_ownership_does_not_count_host_or_browser_data(tmp_path, source):
    app = AppRecord(
        "app",
        "Example",
        source=source,
        location="/usr/bin/true",
        metadata={"user_data_dir": str(tmp_path)},
    )
    assert measure_storage(app) == StorageUsage()


def test_flatpak_size_uses_exact_installation_ref_and_commit(tmp_path, monkeypatch):
    from housekeeper.providers import flatpak

    ref = SimpleNamespace(
        format_ref=lambda: "app/org.example.App/x86_64/stable",
        get_commit=lambda: "current",
        get_installed_size=lambda: 123456,
    )
    installation = SimpleNamespace(
        get_path=lambda: SimpleNamespace(get_path=lambda: "/flatpak"),
        list_installed_refs=lambda _: [ref],
    )
    monkeypatch.setattr(flatpak, "load_flatpak", lambda: None)
    monkeypatch.setattr(flatpak, "configured_installations", lambda _: ([installation], []))
    app = AppRecord(
        "flatpak",
        "Example",
        source=Source.FLATPAK,
        identity=ref.format_ref(),
        metadata={"installation": "/flatpak", "commit": "current", "app_id": "org.example.App"},
    )
    assert measure_storage(app) == StorageUsage(123456)
    app.metadata["commit"] = "old"
    assert measure_storage(app) == StorageUsage()
    app.metadata["commit"] = "current"
    app.metadata["installation"] = "/other-installation"
    assert measure_storage(app).software is None
    app.metadata["installation"] = "/flatpak"
    app.identity = "app/org.example.App/x86_64/beta"
    assert measure_storage(app).software is None


def test_missing_backend_returns_unknown(monkeypatch):
    def unavailable(_app):
        raise ImportError("No Flatpak")

    monkeypatch.setattr("housekeeper.storage._software_size", unavailable)
    assert measure_storage(AppRecord("app", "Example", source=Source.FLATPAK)) == StorageUsage()


def test_rpm_size_matches_version_epoch_and_architecture(monkeypatch):
    headers = [
        {"version": "1", "release": "2", "epoch": 3, "arch": "i686", "size": 11},
        {"version": "1", "release": "2", "epoch": 3, "arch": "x86_64", "size": 22},
    ]
    index = SimpleNamespace(available=True, ts=SimpleNamespace(dbMatch=lambda *args: headers))
    monkeypatch.setattr("housekeeper.providers.rpm.RpmIndex", lambda: index)
    app = AppRecord(
        "rpm",
        "Example",
        source=Source.RPM,
        version="3:1-2",
        metadata={"name": "example", "arch": "x86_64"},
    )
    assert measure_storage(app) == StorageUsage(22)
    app.version = "1-2"
    assert measure_storage(app) == StorageUsage()


def test_storage_worker_dispatches_without_locking_management(monkeypatch):
    received, dispatched = [], []
    service = InventoryService(lambda *args: dispatched.append(args))
    service.executor.shutdown()
    future = Future()
    service.executor = SimpleNamespace(submit=lambda *args: future)
    service.measure_storage(AppRecord("app", "Example"), received.append)
    assert not service.busy
    future.set_result(StorageUsage(0))
    assert not received
    callback, result = dispatched.pop()
    callback(result)
    assert received == [StorageUsage(0)]


def test_storage_worker_ignores_completion_after_close():
    service = InventoryService(lambda *args: pytest.fail("Dispatched after close"))
    service.executor.shutdown()
    future = Future()
    service.executor = SimpleNamespace(submit=lambda *args: future)
    service.measure_storage(AppRecord("app", "Example"), lambda _: None)
    service.closed = True
    future.set_result(StorageUsage(123))
