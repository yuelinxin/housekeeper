"""Serialized operation lifecycle, including submission and provider failures."""

from concurrent.futures import Future
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from housekeeper.models import (
    AppRecord,
    ManagementError,
    OperationCancelled,
    OperationResult,
    Outcome,
    UpdateCheckResult,
    UpdateState,
)
from housekeeper.services import InventoryService


class Executor:
    def __init__(self):
        self.pending = []
        self.closed = False

    def submit(self, fn, *args):
        if self.closed:
            raise RuntimeError("Executor closed")
        future = Future()
        self.pending.append((future, fn, args))
        return future

    def finish(self):
        future, fn, args = self.pending.pop(0)
        if future.cancelled():
            return
        try:
            value = fn(*args)
        except Exception as error:
            future.set_exception(error)
        else:
            future.set_result(value)

    def shutdown(self, **_kwargs):
        self.closed = True


@pytest.fixture
def service(monkeypatch):
    value = InventoryService(lambda fn, *args: fn(*args))
    value.executor.shutdown()
    value.executor = Executor()
    provider = NS(prepare_update=lambda *_a: UpdateCheckResult(UpdateState.CURRENT))
    monkeypatch.setattr(value, "_provider", lambda _app: provider)
    yield value, provider
    value.close()


def test_check_blocks_other_work_and_releases_before_callback(service):
    value, _provider = service
    app = AppRecord("a", "Example")
    completed, errors = [], []
    value.prepare_update(
        app, lambda *_: None, lambda result: completed.append((result, value.busy)), errors.append
    )
    assert value.busy and value.active_provider is not None
    assert not value.scan(None, None, None)
    value.prepare_update(app, None, completed.append, errors.append)
    assert isinstance(errors[0], ManagementError)
    value.executor.finish()
    assert completed[0][0].state == UpdateState.CURRENT and completed[0][1] is False
    assert value.active_provider is None


@pytest.mark.parametrize("succeeds", [True, False])
def test_icon_change_uses_serial_worker_without_a_package_provider(service, monkeypatch, succeeds):
    value, _provider = service
    calls, completed, errors = [], [], []

    def save(entry, image):
        calls.append((entry, image))
        if not succeeds:
            raise ManagementError("Invalid image")
        return "saved.desktop"

    def unexpected_provider(_app):
        raise AssertionError("An icon change must not call a package manager")

    monkeypatch.setattr("housekeeper.appearance.save_icon", save)
    monkeypatch.setattr(value, "_provider", unexpected_provider)
    value.change_icon(AppRecord("a", "Example"), "entry", "image", completed.append, errors.append)
    assert value.busy and not value.scan(None, None, None)
    value.executor.finish()
    assert calls == [("entry", "image")]
    assert not value.busy and value.active_provider is None
    assert completed == (["saved.desktop"] if succeeds else [])
    assert errors == ([] if succeeds else ["Invalid image"])


@pytest.mark.parametrize("failure", ["provider", "submit", "worker"])
def test_check_failure_never_leaves_service_busy(service, monkeypatch, failure):
    value, provider = service

    def fail(*_a):
        raise RuntimeError("Unavailable")

    if failure == "provider":
        monkeypatch.setattr(value, "_provider", fail)
    elif failure == "submit":
        value.executor.closed = True
    else:
        provider.prepare_update = fail
    errors = []
    value.prepare_update(AppRecord("a", "A"), lambda *_: None, None, errors.append)
    if failure == "worker":
        value.executor.finish()
    assert errors and not value.busy and value.active_provider is None


def test_check_cancel_reaches_active_provider(service):
    value, provider = service
    cancelled, errors = [], []
    provider.request_cancel = lambda: cancelled.append(True)
    provider.prepare_update = lambda *_a: pytest.fail(
        "A withdrawn check must not contact a provider"
    )
    value.prepare_update(AppRecord("a", "A"), lambda *_: None, None, errors.append)
    value.cancel()
    # The request reaches the provider, and work still queued is withdrawn at once
    # instead of holding the service until the worker gets to it.
    assert cancelled == [True]
    assert isinstance(errors[0], OperationCancelled) and not value.busy
    value.executor.finish()
    assert value.active_provider is None


def test_execution_cancellation_preserves_outcome(service):
    value, provider = service

    def execute(*_a):
        raise OperationCancelled("Cancelled")

    provider.execute_update = execute
    completed = []
    value.execute_update(AppRecord("a", "A"), None, lambda *_: None, completed.append)
    value.executor.finish()
    assert completed[0].outcome == Outcome.CANCELLED


@pytest.mark.parametrize("batch", [False, True])
def test_confirmed_update_waits_for_scan_without_being_rejected(service, monkeypatch, batch):
    value, provider = service
    app = AppRecord("a", "Example")
    monkeypatch.setattr("housekeeper.services.collect", lambda _partial: ([app], [], [], {}))
    calls, completed = [], []

    def execute(*_args):
        assert not value.scanning and value.inventory == [app]
        calls.append("execute")
        return OperationResult(Outcome.SUCCESS, "Updated")

    provider.execute_update = execute
    if batch:

        def execute_batch(worker, *_args):
            assert worker.inventory == [app]
            return execute()

        monkeypatch.setattr("housekeeper.batch_updates.UpdateBatch.execute", execute_batch)
    value.scan(lambda *_: None, lambda *_: None, pytest.fail)
    if batch:
        value.execute_updates([], lambda *_: None, completed.append)
    else:
        value.execute_update(app, None, lambda *_: None, completed.append)
    assert value.busy and not completed and not calls
    value.executor.finish()
    assert not calls
    value.executor.finish()
    assert calls == ["execute"] and completed[0].outcome == Outcome.SUCCESS
    assert not value.busy


def test_scan_submission_failure_releases_scanning(service):
    value, _provider = service
    value.executor.closed = True
    errors = []
    assert not value.scan(None, None, errors.append)
    assert errors and not value.scanning


@pytest.mark.parametrize("method", ["prepare", "prepare_update"])
def test_preview_queues_behind_scan_and_uses_finished_inventory(service, monkeypatch, method):
    value, provider = service
    app = AppRecord("a", "Example")
    fresh = [app, AppRecord("b", "Another launcher")]
    events, errors = [], []

    def collect(_partial):
        events.append("scan")
        return fresh, [], [], {}

    def preview(_app, inventory, *_args):
        assert not value.scanning and inventory == fresh
        events.append("preview")
        return "ready"

    monkeypatch.setattr("housekeeper.services.collect", collect)
    setattr(provider, method, preview)
    assert value.scan(lambda *_: None, lambda *_: events.append("inventory"), errors.append)
    args = (app, lambda *_: None) if method == "prepare_update" else (app,)
    getattr(value, method)(*args, events.append, errors.append)
    assert value.scanning and value.busy and not errors and not events
    value.prepare(app, events.append, errors.append)
    assert len(errors) == 1  # A second management operation still conflicts.
    value.executor.finish()
    assert events == ["scan", "inventory"] and value.busy
    value.executor.finish()
    assert events == ["scan", "inventory", "preview", "ready"]
    assert not value.busy and not value.scanning


@pytest.mark.parametrize("cancelled", [False, True])
def test_batch_queued_during_scan_uses_new_apps_and_accepts_cancel(service, monkeypatch, cancelled):
    from housekeeper.models import UpdateAction

    value, provider = service
    app = AppRecord("a", "Example", provider="rpm", update_action=UpdateAction.CHECK)
    monkeypatch.setattr("housekeeper.services.collect", lambda _partial: ([app], [], [], {}))
    contacted, results, errors = [], [], []

    def preview(current, inventory, _progress, **_kwargs):
        contacted.append(current)
        assert inventory == [app] and not value.scanning
        return UpdateCheckResult(UpdateState.CURRENT)

    provider.prepare_update = preview
    value.scan(lambda *_: None, lambda *_: None, pytest.fail)
    value.check_updates(lambda *_: None, results.append, errors.append)
    if cancelled:
        value.cancel()
        # Withdrawn at once: the service is free again without waiting out the scan.
        assert not value.busy and value.scanning
    value.executor.finish()
    value.executor.finish()
    if cancelled:
        assert isinstance(errors[0], OperationCancelled) and not results
    else:
        assert not errors and not results[0].cancelled and not results[0].errors
    assert contacted == ([] if cancelled else [app])
    assert not value.busy and value.active_provider is None


def test_queued_icon_change_revalidates_a_refreshed_launcher(service, monkeypatch):
    value, _provider = service
    entry, other = "entry", "moved"
    app = AppRecord("a", "Example", entries=[entry])
    saved, errors = [], []
    monkeypatch.setattr(
        "housekeeper.services.collect",
        lambda _partial: ([replace(app, entries=[other])], [], [], {}),
    )
    monkeypatch.setattr("housekeeper.appearance.save_icon", lambda *args: saved.append(args))
    value.scan(lambda *_: None, lambda *_: None, pytest.fail)
    value.change_icon(app, entry, "image", pytest.fail, errors.append)
    value.executor.finish()
    value.executor.finish()
    # The window validated the launcher against the inventory the scan replaced.
    assert not saved and "Refresh and try again." in errors[0]
    assert not value.busy


def test_failed_scan_does_not_drop_queued_icon_change(service, monkeypatch):
    value, _provider = service
    events, errors = [], []

    def collect(_partial):
        raise RuntimeError("Scan unavailable")

    def save(*_args):
        assert not value.scanning
        events.append("saved")

    monkeypatch.setattr("housekeeper.services.collect", collect)
    monkeypatch.setattr("housekeeper.appearance.save_icon", save)
    value.scan(lambda *_: None, pytest.fail, errors.append)
    value.change_icon(AppRecord("a", "Example"), "entry", "image", events.append, pytest.fail)
    assert not events
    value.executor.finish()
    assert errors == ["Scan unavailable"] and value.busy
    value.executor.finish()
    assert events == ["saved", None] and not value.busy


def test_batch_check_owns_service_until_complete(service):
    value, _provider = service
    from housekeeper.models import UpdateAction

    value.inventory = [AppRecord("a", "A", update_action=UpdateAction.CHECK)]
    results, errors = [], []
    value.check_updates(lambda *_: None, results.append, errors.append)
    assert value.busy and not value.scan(None, None, None)
    value.cancel()
    value.executor.finish()
    # A withdrawn check reports no snapshot, so previous results keep standing.
    assert isinstance(errors[0], OperationCancelled) and not results
    assert not value.busy and value.active_provider is None


def test_batch_submit_failure_recovers(service):
    value, _provider = service
    value.executor.closed = True
    results = []
    value.execute_updates([], lambda *_: None, results.append)
    assert results[0].outcome == Outcome.FAILED
    assert not value.busy and value.active_provider is None


def test_batch_check_forwards_provider_selection(service, monkeypatch):
    value, _provider = service
    from housekeeper.models import UpdateAction

    value.inventory = [
        AppRecord(source, source, provider=source, update_action=UpdateAction.CHECK)
        for source in ("rpm", "flatpak")
    ]
    contacted = []

    def provider(app):
        contacted.append(app.provider)
        return NS(prepare_update=lambda *_: UpdateCheckResult(UpdateState.CURRENT))

    monkeypatch.setattr(value, "_provider", provider)
    results = []
    value.check_updates(lambda *_: None, results.append, pytest.fail, providers=("flatpak",))
    value.executor.finish()
    assert contacted == ["flatpak"]
    assert not results[0].errors and not value.busy
