from types import SimpleNamespace

import pytest

from housekeeper.identity import classify
from housekeeper.models import AppRecord, Source
from housekeeper.providers import flatpak, rpm
from housekeeper.sorting import package_timestamp, sort_key


@pytest.mark.parametrize("mode,field", [("size", "software_size"), ("installed", "updated_at")])
def test_numeric_sort_uses_numbers_with_unknown_last_and_stable_name_ties(mode, field):
    apps = [
        AppRecord("unknown", "Aardvark"),
        AppRecord("small", "Small", **{field: 9}),
        AppRecord("tie-z", "Beta", **{field: 100}),
        AppRecord("tie-a", "alpha", **{field: 100}),
        AppRecord("zero", "Zero", **{field: 0}),
    ]
    assert [app.key for app in sorted(apps, key=lambda app: sort_key(app, mode))] == [
        "tie-a",
        "tie-z",
        "small",
        "zero",
        "unknown",
    ]
    assert [app.key for app in sorted(apps, key=lambda app: sort_key(app, "name"))] == [
        "unknown",
        "tie-a",
        "tie-z",
        "small",
        "zero",
    ]


def test_same_name_and_metric_keep_distinct_installations_in_stable_order():
    apps = [AppRecord(key, "Editor", software_size=10) for key in ("user", "system")]
    for mode in ("name", "size", "installed"):
        assert [app.key for app in sorted(apps, key=lambda app: sort_key(app, mode))] == [
            "system",
            "user",
        ]


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        -1,
        0,
        1.5,
        "unknown",
        "2026-01-01",
        "2026-01-01T12:00:00",
        "-1",
        253402300800,
        "9" * 5000,
    ],
)
def test_missing_invalid_and_timezone_free_timestamps_remain_unknown(value):
    assert package_timestamp(value) is None


@pytest.mark.parametrize(
    "value",
    [
        1735689600,
        "1735689600",
        "2025-01-01T00:00:00Z",
        "2025-01-01T08:00:00+08:00",
    ],
)
def test_package_timestamps_use_unix_seconds(value):
    assert package_timestamp(value) == 1735689600


@pytest.mark.parametrize("optional", [{"size": 12345, "installtime": 1735689600}, {}])
def test_rpm_reads_inventory_metrics_from_owned_installed_header(entry, optional):
    desktop = entry()
    header = {
        "name": "editor",
        "version": "1",
        "release": "2",
        "epoch": 0,
        "arch": "x86_64",
        "filenames": [str(desktop.path), desktop.executable],
        **optional,
    }
    index = rpm.RpmIndex.__new__(rpm.RpmIndex)
    calls = []

    def match(tag, path):
        calls.append((tag, path))
        return [header]

    index.ts = SimpleNamespace(dbMatch=match)
    index._cache = {}
    app = classify(desktop)
    index.enrich(app)
    assert app.source == Source.RPM
    assert app.software_size == optional.get("size")
    assert app.updated_at == optional.get("installtime")
    before = len(calls)
    index.enrich(classify(desktop))
    assert len(calls) == before  # Reuse ownership metadata across launchers.


def test_flatpak_metrics_keep_installations_separate_and_do_not_infer_dates(monkeypatch, tmp_path):
    def installation(path, size):
        path.mkdir()

        def get_size():
            if size is None:
                raise RuntimeError("Size unavailable")
            return size

        ref = SimpleNamespace(
            get_kind=lambda: "app",
            format_ref=lambda: "app/org.example.Editor/x86_64/stable",
            get_is_current=lambda: True,
            get_appdata_name=lambda: "Editor",
            get_name=lambda: "org.example.Editor",
            get_appdata_version=lambda: "1",
            get_origin=lambda: "example",
            get_deploy_dir=lambda: str(path),
            get_commit=lambda: "current",
            get_installed_size=get_size,
        )
        return SimpleNamespace(
            get_path=lambda: SimpleNamespace(get_path=lambda: str(path)),
            get_id=lambda: "default",
            list_installed_refs=lambda _: [ref],
            get_is_user=lambda: True,
        )

    installations = [installation(tmp_path / "first", None), installation(tmp_path / "second", 99)]
    monkeypatch.setattr(
        flatpak, "load_flatpak", lambda: SimpleNamespace(RefKind=SimpleNamespace(APP="app"))
    )
    monkeypatch.setattr(flatpak, "configured_installations", lambda _: (installations, []))
    index = flatpak.FlatpakIndex()
    assert not index.warnings
    assert len(index.apps) == 2 and index.apps[0].key != index.apps[1].key
    assert [app.software_size for app in index.apps] == [None, 99]
    assert all(app.updated_at is None for app in index.apps)
