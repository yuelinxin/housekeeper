"""Serialized operation lifecycle, including submission and provider failures."""

from concurrent.futures import Future
from types import SimpleNamespace as NS

import pytest

from housekeeper.models import (
    AppRecord,
    ManagementError,
    OperationCancelled,
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

    def prepare(*_a):
        assert cancelled
        raise OperationCancelled("Cancelled")

    provider.prepare_update = prepare
    value.prepare_update(AppRecord("a", "A"), lambda *_: None, None, errors.append)
    value.cancel()
    value.executor.finish()
    assert isinstance(errors[0], OperationCancelled) and not value.busy


def test_execution_cancellation_preserves_outcome(service):
    value, provider = service

    def execute(*_a):
        raise OperationCancelled("Cancelled")

    provider.execute_update = execute
    completed = []
    value.execute_update(AppRecord("a", "A"), None, lambda *_: None, completed.append)
    value.executor.finish()
    assert completed[0].outcome == Outcome.CANCELLED


def test_scan_submission_failure_releases_scanning(service):
    value, _provider = service
    value.executor.closed = True
    errors = []
    assert not value.scan(None, None, errors.append)
    assert errors and not value.scanning


def test_batch_check_owns_service_until_complete(service):
    value, _provider = service
    from housekeeper.models import UpdateAction

    value.inventory = [AppRecord("a", "A", update_action=UpdateAction.CHECK)]
    results = []
    value.check_updates(lambda *_: None, results.append, pytest.fail)
    assert value.busy and not value.scan(None, None, None)
    value.cancel()
    value.executor.finish()
    assert results[0].cancelled and not value.busy and value.active_provider is None


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
