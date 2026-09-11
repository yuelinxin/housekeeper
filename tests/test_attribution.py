from copy import deepcopy
from dataclasses import replace
from itertools import permutations
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from housekeeper.attribution import attribute, candidate, check_binding, plan_binding, revalidate
from housekeeper.discovery import read_entry
from housekeeper.identity import classify, merge_records
from housekeeper.inventory import assign_actions
from housekeeper.models import (
    Action,
    AttributionState,
    ProviderCapabilities,
    RemovalPlan,
    Source,
    UpdateAction,
)
from housekeeper.providers.deb import DebIndex
from housekeeper.providers.rpm import RpmIndex
from housekeeper.services import collect


def backend(*values):
    return NS(candidates=lambda entry: values)


def test_rpm_command_never_preempts_deb_launcher(entry):
    desktop = entry()
    rpm = RpmIndex.__new__(RpmIndex)
    rpm.ts = object()
    rpm.owners = lambda path: (
        [] if path == desktop.path else [{"name": "command", "version": "1-1", "arch": "x86_64"}]
    )
    deb = DebIndex()
    deb._owners = {str(desktop.path): {"launcher"}}
    deb._packages = [{"name": "launcher", "version": "1", "arch": "amd64", "installed_size": "1"}]
    results = [attribute(classify(desktop), order) for order in permutations((rpm, deb))]
    assert results[0] == results[1]
    assert results[0].source == Source.DEB
    assert len(results[0].attribution.candidates) == 2


def test_conflicting_desktop_claims_disable_every_action(entry):
    claims = [
        candidate(source, "/", source.value, "1", source.value, "/app.desktop", verified=True)
        for source in (Source.RPM, Source.DEB, Source.PACMAN)
    ]
    results = []
    for order in permutations(tuple(backend(c) for c in claims)):
        original = classify(entry())
        before = deepcopy(original)
        app = attribute(original, order)
        assign_actions(
            app,
            {
                source.value: ProviderCapabilities(
                    execute=True, update_preview=True, update_execute=True
                )
                for source in Source
            },
        )
        assert app.attribution.state == AttributionState.CONFLICT
        assert app.action == Action.NONE and app.update_action == UpdateAction.INSTRUCTIONS
        assert app.target is None and original == before
        results.append(app)
    assert all(app == results[0] for app in results)


def test_failure_does_not_hide_evidence_or_authorize_operation(entry):
    def fail(entry):
        raise RuntimeError("database unavailable")

    claim = candidate(Source.RPM, "/", "app", "1", "app-1", "/app.desktop", verified=True)
    app = attribute(classify(entry()), (NS(candidates=fail), backend(claim)))
    assert app.source == Source.RPM
    assert app.attribution.state == AttributionState.UNAVAILABLE
    assert app.action == Action.NONE
    assert "database unavailable" in app.attribution.errors[0]


def test_components_share_target_without_merging_distinct_arguments(entry):
    claim = candidate(Source.RPM, "/", "suite", "1", "suite-1", "/app.desktop", verified=True)
    writer = attribute(classify(entry(("/suite", "--writer"))), (backend(claim),))
    calc = attribute(
        classify(entry(("/suite", "--calc"), desktop_id="calc.desktop")), (backend(claim),)
    )
    duplicate = replace(
        writer,
        entries=[replace(writer.entries[0], desktop_id="copy.desktop", path=Path("/copy.desktop"))],
    )
    before = deepcopy(writer)
    merged = merge_records([writer, calc, duplicate])
    assert len(merged) == 2
    assert writer.target == calc.target and writer.key != calc.key
    assert writer == before
    updated = replace(claim, instance=replace(claim.instance, version="2"), identity="suite-2")
    assert attribute(classify(writer.entries[0]), (backend(updated),)).key == writer.key


def test_new_conflicting_owner_invalidates_preview(desktop, monkeypatch):
    path = desktop()
    entry = read_entry(path, path.parent)
    claim = candidate(Source.RPM, "/", "app", "1", "app-1", str(path), verified=True)
    indexes = [backend(claim)]
    monkeypatch.setattr("housekeeper.inventory.discovery_indexes", lambda: tuple(indexes))
    app = attribute(classify(entry), tuple(indexes))
    plan = RemovalPlan(
        app.key, app.provider, "app", (app.name,), "preview", "fingerprint", **plan_binding(app)
    )
    revalidate(app)
    check_binding(app, plan)
    indexes.append(
        backend(candidate(Source.DEB, "/", "other", "1", "other", str(path), verified=True))
    )
    with pytest.raises(Exception, match="ownership changed"):
        revalidate(app)


def test_partial_scan_has_no_actions_and_is_not_mutated(desktop):
    path = desktop()
    partial = []
    claim = candidate(Source.RPM, "/", "app", "1", "app-1", str(path), verified=True)
    output, *_ = collect(
        partial=lambda apps: partial.extend(apps), roots=[path.parent], indexes=(backend(claim),)
    )
    assert partial[0].action == Action.NONE
    assert partial[0].update_action == UpdateAction.INSTRUCTIONS
    assert partial[0].source == Source.OTHER
    assert output[0].source == Source.RPM


def test_partial_callback_cannot_change_reused_scanner_entries(entry, monkeypatch):
    command = ("/usr/bin/chromium", "--app-id=" + "a" * 32)
    entries = [entry(command, desktop_id=f"app{i}.desktop") for i in range(2)]
    monkeypatch.setattr("housekeeper.services.scan_entries", lambda *_: (entries, []))

    def partial(apps):
        assert len(apps) == 1 and len(apps[0].entries) == 2
        for original, copied in zip(entries, apps[0].entries, strict=True):
            assert original == copied and original is not copied
        apps[0].entries[1] = replace(apps[0].entries[1], reason="Callback edit")
        apps[0].metadata["callback"] = "changed"

    output, *_ = collect(partial=partial, roots=[], indexes=())
    assert len(output) == 1 and len(output[0].entries) == 2
    assert all(not item.reason for item in output[0].entries)
    assert "callback" not in output[0].metadata


def test_synthetic_flatpak_hidden_overlay_does_not_authorize_entry(desktop):
    from housekeeper.models import AppRecord

    path = desktop(filename="org.example.App.desktop", Hidden="true")
    app = AppRecord(
        "installation",
        "Official",
        source=Source.FLATPAK,
        provider="flatpak",
        identity="app/org.example.App/x86_64/stable",
        metadata={"app_id": "org.example.App", "installation": "/user"},
    )
    db = backend()
    db.apps = [app]
    records, *_ = collect(roots=[path.parent], indexes=(db,))
    official = next(a for a in records if a.source == Source.FLATPAK)
    assert not official.visible and not official.entries
    assert app.visible


def test_hidden_overlay_lookup_matches_exact_desktop_ids(desktop):
    from housekeeper.models import AppRecord

    hidden = desktop(filename="org.example.App.desktop", Hidden="true")
    desktop(filename="org.example.App.Other.desktop", Hidden="true", root=hidden.parent)
    db = backend()
    db.apps = [
        AppRecord(
            app_id,
            app_id,
            source=Source.FLATPAK,
            provider="flatpak",
            identity=f"app/{app_id}/x86_64/stable",
            metadata={"app_id": app_id, "installation": "/user"},
        )
        for app_id in ("org.example.App", "org.example.App.Other", "org.example.Visible")
    ]
    records, *_ = collect(roots=[hidden.parent], indexes=(db,))
    visibility = {app.key: app.visible for app in records if app.source == Source.FLATPAK}
    assert visibility == {
        "org.example.App": False,
        "org.example.App.Other": False,
        "org.example.Visible": True,
    }
    assert all(app.visible for app in db.apps)


def test_dbus_components_with_identical_fallback_commands_remain_distinct(entry):
    claim = candidate(Source.FLATPAK, "/user", "app/ref", "1", "app/ref", "/export", verified=True)
    entries = [
        entry(("/usr/bin/true",), desktop_id=name, dbus_activatable=True)
        for name in ("org.example.App.desktop", "org.example.App.Editor.desktop")
    ]
    apps = [attribute(classify(e), (backend(claim),)) for e in entries]
    assert len(merge_records(apps)) == 2
    assert apps[0].target == apps[1].target
    assert apps[0].component.launch_signature != apps[1].component.launch_signature
