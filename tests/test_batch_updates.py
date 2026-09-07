"""Batch cancellation, installation identity and approved shared dependency changes."""

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from housekeeper.batch_updates import UpdateBatch, UpdateItem
from housekeeper.models import (
    AppRecord,
    ManagementError,
    OperationCancelled,
    OperationResult,
    Outcome,
    UpdateAction,
    UpdateChange,
    UpdateCheckResult,
    UpdatePlan,
    UpdateState,
)


def app(key="a", installation="user"):
    return AppRecord(
        key,
        key.upper(),
        provider="flatpak",
        identity=key,
        update_action=UpdateAction.CHECK,
        metadata={"installation": installation},
    )


def change(identity):
    return UpdateChange(identity, "new", "update", "origin", "old", "new")


def plan(record, changes=None):
    return UpdatePlan(
        record.key,
        record.provider,
        "new",
        record.metadata["installation"],
        "old",
        changes or (change(record.identity),),
        "original",
        "Preview",
        environment="sources",
    )


def test_check_deduplicates_installations_but_keeps_all_names():
    a = app()
    duplicate = replace(a, key="alias", name="Alias")
    other = app(installation="system")
    manual = replace(app("manual"), update_action=UpdateAction.INSTRUCTIONS)
    calls = []

    def check(record, *_args):
        calls.append(record)
        return UpdateCheckResult(UpdateState.AVAILABLE, plan(record))

    worker = UpdateBatch(lambda _app: NS(prepare_update=check), [a, duplicate, other, manual])
    result = worker.check(lambda *_: None)
    assert calls == [a, other]
    assert result.items[0].names == ("A", "Alias")
    assert result.unsupported == 1 and len(result.items) == 2


def test_check_errors_and_current_are_distinct():
    def factory(record):
        if record.key == "a":
            raise RuntimeError("Offline")
        return NS(prepare_update=lambda *_: UpdateCheckResult(UpdateState.CURRENT))

    report = UpdateBatch(factory, [app(), app("b")]).check(lambda *_: None)
    assert not report.items and report.errors == ("A: Offline",) and not report.cancelled


def test_check_cancel_keeps_results_and_does_not_start_next_provider():
    created = []
    worker = None

    def factory(record):
        created.append(record.key)

        def check(*_args):
            worker.request_cancel()
            return UpdateCheckResult(UpdateState.AVAILABLE, plan(record))

        return NS(prepare_update=check, request_cancel=lambda: None)

    worker = UpdateBatch(factory, [app(), app("b")])
    report = worker.check(lambda *_: None)
    assert report.cancelled and len(report.items) == 1 and created == ["a"]


def test_batch_cancel_before_start():
    worker = UpdateBatch(lambda _a: pytest.fail("Provider must not be created"), [app()])
    worker.request_cancel()
    assert worker.check(lambda *_: None).cancelled


@pytest.mark.parametrize(
    "mutation", ["new_dependency", "new_target", "sources", "external_removal"]
)
def test_shared_dependency_reconciliation_rejects_unapproved_changes(mutation):
    a = app()
    original = plan(a, (change("a"), change("runtime")))
    fresh = replace(original, fingerprint="new", changes=(change("a"),))
    verified = {("flatpak", "user", "runtime"): change("runtime")}
    if mutation == "new_dependency":
        fresh = replace(fresh, changes=(*fresh.changes, change("other")))
    elif mutation == "new_target":
        fresh = replace(fresh, target="newer")
    elif mutation == "sources":
        fresh = replace(fresh, environment="changed")
    else:
        verified = {}
    with pytest.raises(ManagementError, match="plan changed"):
        UpdateBatch._remaining_plan(original, fresh, verified)


def test_batch_accepts_only_previously_completed_shared_dependency():
    a, b = app(), app("b")
    first = plan(a, (change("a"), change("runtime")))
    second = plan(b, (change("b"), change("runtime")))
    fresh_second = replace(second, changes=(change("b"),), fingerprint="after-first")
    executed = []

    def factory(record):
        current = first if record.key == "a" else fresh_second

        def execute(_app, actual, _progress):
            executed.append(actual)
            return OperationResult(
                Outcome.SUCCESS, "Done", tuple(c.identity for c in actual.changes)
            )

        return NS(
            prepare_update=lambda *_: UpdateCheckResult(UpdateState.AVAILABLE, current),
            execute_update=execute,
        )

    worker = UpdateBatch(factory, [a, b])
    result = worker.execute(
        [UpdateItem(a, first, ("A",)), UpdateItem(b, second, ("B",))], lambda *_: None
    )
    assert result.outcome == Outcome.SUCCESS and result.completed == ("A", "B")
    assert executed == [first, fresh_second]


def test_batch_failure_stops_remaining_work_and_reports_partial():
    records = [app(), app("b"), app("c")]
    created = []

    def factory(record):
        created.append(record.key)

        def execute(*_args):
            if record.key == "b":
                raise OperationCancelled("Cancelled")
            return OperationResult(Outcome.SUCCESS, "Done")

        return NS(
            prepare_update=lambda *_: UpdateCheckResult(UpdateState.AVAILABLE, plan(record)),
            execute_update=execute,
        )

    result = UpdateBatch(factory, records).execute(
        [UpdateItem(a, plan(a), (a.name,)) for a in records], lambda *_: None
    )
    assert result.outcome == Outcome.PARTIAL and result.completed == ("A",)
    assert created == ["a", "b"] and "Cancelled" in result.errors[0]
