"""Cached checks survive navigation/restarts without trusting changed installations."""

import json
from dataclasses import replace

import pytest

from housekeeper.batch_updates import UpdateItem, UpdateReport
from housekeeper.models import AppRecord, DesktopEntry, UpdateChange, UpdatePlan
from housekeeper.update_cache import UpdateCache, cache_expired, cache_path
from housekeeper.update_cache import snapshot as app_snapshot


@pytest.mark.parametrize("age,expired", [(86399, False), (86400, True), (86401, True)])
def test_cache_expires_after_24_hours(cached, age, expired):
    cache, app, _ = cached
    result = cache.load([app])
    assert cache_expired(result.checked_at, now=result.checked_at + age) is expired


@pytest.fixture
def cached(tmp_path):
    app = AppRecord(
        "app",
        "Example",
        provider="flatpak",
        identity="app/example/x86_64/stable",
        entries=[DesktopEntry("example.desktop", tmp_path / "example.desktop", "Example")],
        metadata={"installation": "/var/lib/flatpak"},
    )
    change = UpdateChange(app.identity, "new-commit", "update", "origin", "old", "new")
    plan = UpdatePlan(
        app.key, app.provider, "new-commit", "system", "old", (change,), "hash", "Update"
    )
    report = UpdateReport((UpdateItem(app, plan, (app.name,)),), unsupported=2)
    cache = UpdateCache(tmp_path / "cache/updates.json")
    cache.save(report, [app], 1_700_000_000)
    return cache, app, report


def test_round_trip_with_paths_and_exact_plan(cached):
    cache, app, report = cached
    result = UpdateCache(cache.path).load([app])
    assert result.report == report
    assert result.checked_at == 1_700_000_000
    assert not result.stale
    assert cache.path.stat().st_mode & 0o777 == 0o600


def test_load_snapshots_each_app_once(cached, monkeypatch):
    cache, app, report = cached
    inventory = [app, *(replace(app, key=f"other-{index}") for index in range(100))]
    cache.save(report, inventory, 1_700_000_000)
    calls = []

    def counted_snapshot(record):
        calls.append(record.key)
        return app_snapshot(record)

    monkeypatch.setattr("housekeeper.update_cache.snapshot", counted_snapshot)
    result = cache.load(inventory)
    assert result.report == report and not result.stale
    assert len(calls) == len(inventory) and set(calls) == {record.key for record in inventory}


def test_previous_schema_cannot_restore_unbound_previews(cached):
    cache, app, _ = cached
    payload = json.loads(cache.path.read_text())
    payload["schema"] = 1
    cache.path.write_text(json.dumps(payload))
    assert cache.load([app]) is None


def test_empty_results_are_cached(cached):
    cache, app, _ = cached
    cache.save(UpdateReport(()), [app], 1_700_000_001)
    result = cache.load([app])
    assert not result.report.items and not result.stale


def test_legacy_dependency_only_plan_is_hidden_without_renewing_check_time(cached):
    cache, app, report = cached
    item = report.items[0]
    dependency = replace(item.plan.changes[0], identity="runtime/example/x86_64/stable")
    item = replace(item, plan=replace(item.plan, changes=(dependency,)))
    cache.save(replace(report, items=(item,)), [app], 1_700_000_000)
    result = cache.load([app])
    assert not result.report.items and result.checked_at == 1_700_000_000


def test_cached_hidden_auxiliary_entry_is_not_an_update_row(cached):
    cache, app, report = cached
    hidden = replace(app, visible=False)
    cache.save(
        replace(report, items=(replace(report.items[0], app=hidden),)), [hidden], 1_700_000_000
    )
    assert not cache.load([hidden]).report.items


@pytest.mark.parametrize("age,expired", [(86400, False), (604799, False), (604800, True)])
def test_weekly_interval(cached, age, expired):
    cache, app, _ = cached
    result = cache.load([app])
    assert cache_expired(result.checked_at, now=result.checked_at + age, ttl=604800) is expired


def test_cache_is_scoped_to_checked_providers(cached):
    cache, app, report = cached
    assert cache.load([app], providers=("flatpak",)) is None
    cache.save(report, [app], 1_700_000_001, providers=("flatpak",))
    assert cache.load([app], providers=("flatpak",)).report == report
    assert cache.load([app]) is None
    assert cache.load([app], providers=("rpm",)) is None


def test_legacy_cache_requires_both_providers(cached):
    cache, app, report = cached
    data = json.loads(cache.path.read_text())
    del data["providers"]
    cache.path.write_text(json.dumps(data))
    assert cache.load([app]).report == report
    assert cache.load([app], providers=("flatpak",)) is None


def test_cache_rejects_items_from_an_unchecked_provider(cached):
    cache, app, _ = cached
    data = json.loads(cache.path.read_text())
    data["providers"] = ["rpm"]
    cache.path.write_text(json.dumps(data))
    assert cache.load([app], providers=("rpm",)) is None


@pytest.mark.parametrize("changes", [{"version": "2"}, {"metadata": {"installation": "/other"}}])
def test_changed_installation_is_removed_from_cached_results(cached, changes):
    cache, app, _ = cached
    result = cache.load([replace(app, **changes)])
    assert result.stale and not result.report.items


def test_added_apps_mark_cache_stale_but_keep_valid_results(cached):
    cache, app, report = cached
    result = cache.load([app, replace(app, key="new")])
    assert result.stale and result.report == report
    result = cache.load([])
    assert result.stale and not result.report.items


@pytest.mark.parametrize(
    "report", [UpdateReport((), ("Offline",)), UpdateReport((), cancelled=True)]
)
def test_failed_or_cancelled_check_preserves_previous_success(cached, report):
    cache, app, original = cached
    before = cache.path.read_bytes()
    cache.save(report, [app], 1_700_000_001)
    assert cache.path.read_bytes() == before
    assert cache.load([app]).report == original


@pytest.mark.parametrize(
    "field,value", [("schema", 999), ("version", "old"), ("checked_at", 1e100), ("items", None)]
)
def test_invalid_or_incompatible_cache_is_ignored(cached, field, value):
    cache, app, _ = cached
    data = json.loads(cache.path.read_text())
    data[field] = value
    cache.path.write_text(json.dumps(data))
    assert cache.load([app]) is None


def test_invalid_plan_and_truncated_file_are_ignored(cached):
    cache, app, _ = cached
    data = json.loads(cache.path.read_text())
    data["items"][0]["plan"]["changes"][0]["target_version"] = []
    cache.path.write_text(json.dumps(data))
    assert cache.load([app]) is None
    cache.path.write_text('{"schema":')
    assert cache.load([app]) is None


def test_clear_and_unwritable_cache(cached, tmp_path):
    cache, app, report = cached
    cache.clear()
    assert cache.load([app]) is None
    cache.clear()
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    cache = UpdateCache(blocked / "updates.json")
    cache.save(report, [app], 1_700_000_000)
    assert cache.load([app]) is None
    cache.clear()


def test_xdg_cache_location(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert cache_path() == tmp_path / "housekeeper/updates.json"
    monkeypatch.setenv("XDG_CACHE_HOME", "relative")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cache_path() == tmp_path / ".cache/housekeeper/updates.json"


def test_completed_update_keeps_remaining_cache_and_original_check_time(cached):
    cache, app, report = cached
    other = replace(app, key="other", identity="other", name=app.name)
    other_plan = replace(
        report.items[0].plan,
        app_key=other.key,
        changes=(replace(report.items[0].plan.changes[0], identity=other.identity),),
    )
    other_item = UpdateItem(other, other_plan, (other.name,))
    report = replace(report, items=(*report.items, other_item))
    cache.save(report, [app, other], 1_700_000_000)
    cache.reconcile([app, other], completed_keys=(app.key,))
    result = cache.load([app, other])
    assert result.report.items == (other_item,)
    assert result.report.unsupported == 2 and result.checked_at == 1_700_000_000
    updated = replace(app, version="2")
    cache.reconcile([updated, other], updated_keys=(app.key,))
    result = UpdateCache(cache.path).load([updated, other])
    assert result.report.items == (other_item,) and not result.stale
    assert result.checked_at == 1_700_000_000


def test_runtime_only_completion_persists_empty_cache(cached):
    cache, app, _ = cached
    cache.reconcile([app], completed_keys=(app.key,))
    cache.reconcile([app], updated_keys=(app.key,))
    result = cache.load([app])
    assert not result.report.items and not result.stale
    assert result.checked_at == 1_700_000_000


def test_reconciliation_keeps_unrelated_inventory_changes_stale(cached):
    cache, app, _ = cached
    added = replace(app, key="added")
    cache.reconcile([app, added], completed_keys=(app.key,))
    assert cache.load([app, added]).stale
    updated = replace(app, version="2")
    cache.reconcile([updated, added], updated_keys=(app.key,))
    result = cache.load([updated, added])
    assert result.stale and not result.report.items
    assert result.checked_at == 1_700_000_000


def test_reconciliation_does_not_create_cache_or_cross_provider_settings(cached):
    cache, app, _ = cached
    before = cache.path.read_bytes()
    cache.reconcile([app], completed_keys=(app.key,), providers=("rpm",))
    assert cache.path.read_bytes() == before
    cache.clear()
    cache.reconcile([app], completed_keys=(app.key,))
    assert not cache.path.exists()
