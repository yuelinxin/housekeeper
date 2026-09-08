"""Cached checks survive navigation/restarts without trusting changed installations."""

import json
from dataclasses import replace

import pytest

from housekeeper.batch_updates import UpdateItem, UpdateReport
from housekeeper.models import AppRecord, DesktopEntry, UpdateChange, UpdatePlan
from housekeeper.update_cache import UpdateCache, cache_expired, cache_path


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


def test_empty_results_are_cached(cached):
    cache, app, _ = cached
    cache.save(UpdateReport(()), [app], 1_700_000_001)
    result = cache.load([app])
    assert not result.report.items and not result.stale


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
