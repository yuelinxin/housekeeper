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


def test_component_batch_executes_shared_target_once_and_refreshes_every_component():
    from housekeeper.models import InstallationInstance, ManagementTarget

    first = app()
    first.installation = InstallationInstance("installation", "flatpak", "/user", "suite", "1")
    first.target = ManagementTarget("target", "installation", "ref", "suite")
    second = replace(first, key="second", name="Second component")
    executions = []
    provider = NS(
        prepare_update=lambda record, *_: UpdateCheckResult(UpdateState.AVAILABLE, plan(record)),
        execute_update=lambda *args: (
            executions.append(args) or OperationResult(Outcome.SUCCESS, "done")
        ),
    )
    batch = UpdateBatch(lambda record: provider, [first, second])
    report = batch.check(lambda *_: None)
    assert len(report.items) == 1 and report.items[0].names == (first.name, second.name)
    result = batch.execute(report.items, lambda *_: None)
    assert len(executions) == 1
    assert set(result.completed_app_keys) == {first.key, second.key}


def test_shared_dependency_exception_does_not_ignore_ownership_changes():
    original = replace(
        plan(app()), instance_id="installation", target_id="target", evidence_digest="old"
    )
    changed = replace(original, fingerprint="different", evidence_digest="changed")
    with pytest.raises(ManagementError):
        UpdateBatch._remaining_plan(original, changed, {})


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


def test_check_names_every_launcher_once_with_the_subject_first():
    a = app()
    records = [
        a,
        replace(a, key="repeat", name="A"),
        replace(a, key="alias", name="Alias"),
        replace(a, key="twin", name="Alias"),
    ]
    worker = UpdateBatch(
        lambda _app: NS(
            prepare_update=lambda record, *_args: UpdateCheckResult(
                UpdateState.AVAILABLE, plan(record)
            )
        ),
        records,
    )
    result = worker.check(lambda *_: None)
    assert result.items[0].names == ("A", "Alias")


def test_check_errors_and_current_are_distinct():
    def factory(record):
        if record.key == "a":
            raise RuntimeError("Offline")
        return NS(prepare_update=lambda *_: UpdateCheckResult(UpdateState.CURRENT))

    report = UpdateBatch(factory, [app(), app("b")]).check(lambda *_: None)
    assert not report.items and report.errors == ("A: Offline",) and not report.cancelled


@pytest.mark.parametrize("providers", [("rpm",), ("flatpak",), (), ("rpm", "flatpak")])
def test_check_only_contacts_enabled_providers_with_full_inventory(providers):
    records = [
        app(),
        replace(app("rpm"), provider="rpm"),
        replace(app("web"), provider="web", update_action=UpdateAction.INSTRUCTIONS),
    ]
    calls = []

    def check(record, inventory, _progress, **_kwargs):
        assert inventory == records
        calls.append(record.provider)
        return UpdateCheckResult(UpdateState.CURRENT)

    result = UpdateBatch(lambda _: NS(prepare_update=check), records).check(
        lambda *_: None, providers=providers
    )
    assert set(calls) == set(providers)
    assert result.unsupported == 1
    assert not result.errors and not result.cancelled


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


def test_bulk_discovery_previews_only_candidates_per_installation():
    records = [app(str(i)) for i in range(100)] + [app("system", installation="system")]
    records.append(replace(records[0], key="alias", name="Alias"))
    discoveries, previews = [], []

    def factory(record):
        def discover(apps, progress):
            discoveries.append(tuple(a.key for a in apps))
            progress("Querying installation", None, True)
            return {apps[0].key}

        def prepare(record, inventory, progress):
            assert inventory == records
            previews.append(record.key)
            return UpdateCheckResult(UpdateState.AVAILABLE, plan(record))

        return NS(discover_updates=discover, prepare_update=prepare)

    report = UpdateBatch(factory, records).check(lambda *_: None)
    assert not report.errors
    assert [len(group) for group in discoveries] == [100, 1]
    assert previews == ["0", "system"]
    assert report.items[0].names == ("0", "Alias")


def test_failed_discovery_is_not_retried_for_each_app_and_other_installation_continues():
    calls = []

    def factory(record):
        def discover(apps, progress):
            calls.append(record.metadata["installation"])
            if record.metadata["installation"] == "user":
                raise ManagementError("Offline")
            return set()

        return NS(discover_updates=discover, prepare_update=lambda *_: pytest.fail("No preview"))

    records = [app(), app("b"), app("system", installation="system")]
    report = UpdateBatch(factory, records).check(lambda *_: None)
    assert calls == ["user", "system"]
    assert report.errors == ("A: Offline", "B: Offline")
    assert not report.items and not report.cancelled


def test_cancellation_after_discovery_never_starts_preview():
    events = []
    worker = None

    def discover(apps, progress):
        worker.request_cancel()
        return {a.key for a in apps}

    provider = NS(
        discover_updates=discover,
        prepare_update=lambda *_: pytest.fail("Cancelled discovery must not start a preview"),
        request_cancel=lambda: None,
    )
    worker = UpdateBatch(lambda _: provider, [app()])
    assert worker.check(lambda *event: events.append(event)).cancelled
    assert all(fraction < 1 for _, fraction, _ in events)


def test_hidden_auxiliary_entries_are_excluded_but_dependencies_are_retained(desktop, tmp_path):
    from housekeeper.discovery import read_entry
    from housekeeper.identity import classify

    hidden = classify(read_entry(desktop(NoDisplay="true"), tmp_path, {"GNOME"}))
    hidden = replace(hidden, provider="rpm", update_action=UpdateAction.CHECK)
    visible = app()
    dependency = change("runtime/org.example.Runtime/x86_64/stable")
    calls = []

    def prepare(record, inventory, progress):
        assert inventory == [hidden, visible]
        calls.append(record.key)
        return UpdateCheckResult(
            UpdateState.AVAILABLE, plan(record, (change(record.identity), dependency))
        )

    report = UpdateBatch(lambda _: NS(prepare_update=prepare), [hidden, visible]).check(
        lambda *_: None
    )
    assert calls == [visible.key] and report.unsupported == 0
    assert report.items[0].plan.changes[-1] == dependency


def test_runtime_only_plan_does_not_mark_application_updatable():
    record = app()
    provider = NS(
        prepare_update=lambda *_: UpdateCheckResult(
            UpdateState.AVAILABLE, plan(record, (change("runtime"),))
        )
    )
    report = UpdateBatch(lambda _: provider, [record]).check(lambda *_: None)
    assert not report.items and not report.errors


def test_check_uses_one_monotonic_progress_for_all_sources():
    records = [app(), replace(app("rpm"), provider="rpm"), app("last")]
    records.append(replace(records[0], key="alias"))
    events = []

    def check(record, inventory, progress, **kwargs):
        for fraction in (None, 0.8, 1.0, 0.0, None, 0.2, 1.0):
            progress("Checking a backend phase", fraction, fraction != 0.2)
        return UpdateCheckResult(UpdateState.CURRENT)

    result = UpdateBatch(lambda _: NS(prepare_update=check), records).check(
        lambda *event: events.append(event)
    )
    assert not result.errors and not result.cancelled
    fractions = [fraction for _, fraction, _ in events]
    assert fractions == sorted(fractions)
    assert set(fractions) == {0, 1 / 3, 2 / 3, 1}
    assert fractions.count(1) == 1
    phases = [
        (message, fraction, cancel) for message, fraction, cancel in events if "backend" in message
    ]
    assert {fraction for _, fraction, _ in phases} == {0, 1 / 3, 2 / 3}
    assert all("of 3)" in message for message, _, _ in phases)
    assert any(not cancel for _, _, cancel in phases)


def test_failed_checks_advance_overall_progress_and_empty_check_completes():
    events = []

    def factory(record):
        if record.key == "a":
            raise RuntimeError("Offline")
        return NS(prepare_update=lambda *_: UpdateCheckResult(UpdateState.CURRENT))

    result = UpdateBatch(factory, [app(), app("b")]).check(lambda *event: events.append(event))
    assert result.errors == ("A: Offline",)
    assert [fraction for _, fraction, _ in events] == [0.5, 0.5, 1]
    events.clear()
    UpdateBatch(factory, []).check(lambda *event: events.append(event))
    assert len(events) == 1 and events[0][1:] == (1.0, False)


def test_cancelled_check_never_reports_total_completion():
    events = []
    worker = None

    def check(record, inventory, progress):
        progress("Finishing backend phase", 1.0, True)
        worker.request_cancel()
        raise OperationCancelled("Cancelled")

    worker = UpdateBatch(lambda _: NS(prepare_update=check, request_cancel=lambda: None), [app()])
    assert worker.check(lambda *event: events.append(event)).cancelled
    assert all(fraction == 0 for _, fraction, _ in events)


def test_cancel_request_during_non_interruptible_phase_stops_next_check():
    created, requests = [], []
    worker = None

    def factory(record):
        created.append(record.key)

        def check(_record, _inventory, progress):
            progress("Finishing a non-interruptible phase", None, False)
            # The backend finishes its current read even after receiving cancel.
            return UpdateCheckResult(UpdateState.CURRENT)

        return NS(prepare_update=check, request_cancel=lambda: requests.append(True))

    worker = UpdateBatch(factory, [app(), app("b")])

    def progress(_message, _fraction, can_cancel):
        if not can_cancel:
            worker.request_cancel()

    assert worker.check(progress).cancelled
    assert created == ["a"] and requests == [True]


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
    assert result.completed_app_keys == ("a", "b")
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
    assert result.completed_app_keys == ("a",)
    assert created == ["a", "b"] and "Cancelled" in result.errors[0]


def test_completed_keys_distinguish_names_and_partial_components():
    a = app()
    b = replace(app("b"), name=a.name)

    def factory(record):
        result = (
            OperationResult(Outcome.SUCCESS, "Done")
            if record.key == a.key
            else OperationResult(Outcome.PARTIAL, "Stopped", ("runtime",))
        )
        return NS(
            prepare_update=lambda *_: UpdateCheckResult(UpdateState.AVAILABLE, plan(record)),
            execute_update=lambda *_: result,
        )

    result = UpdateBatch(factory, [a, b]).execute(
        [UpdateItem(record, plan(record), (record.name,)) for record in (a, b)], lambda *_: None
    )
    assert result.outcome == Outcome.PARTIAL
    assert result.completed_app_keys == (a.key,)
    assert result.completed == ("A", "A: runtime")


def test_completed_keys_include_plans_satisfied_by_preceding_update():
    a, b = app(), app("b")
    first = plan(a, (change("a"), change("b")))
    second = plan(b)
    executed = []

    def factory(record):
        def execute(*_args):
            executed.append(record.key)
            return OperationResult(Outcome.SUCCESS, "Done")

        return NS(
            prepare_update=lambda *_: UpdateCheckResult(UpdateState.AVAILABLE, first),
            execute_update=execute,
        )

    result = UpdateBatch(factory, [a, b]).execute(
        [UpdateItem(a, first, (a.name,)), UpdateItem(b, second, (b.name,))], lambda *_: None
    )
    assert result.outcome == Outcome.SUCCESS
    assert result.completed_app_keys == (a.key, b.key)
    assert executed == [a.key]
