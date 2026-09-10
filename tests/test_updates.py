"""Update previews must describe exactly the changes the user can authorize."""

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from gi.repository import Gio, GLib

from housekeeper.models import (
    Action,
    AppRecord,
    ManagementError,
    OperationCancelled,
    Outcome,
    ProviderCapabilities,
    Source,
    UpdateAction,
    UpdateState,
)
from housekeeper.providers.flatpak import FlatpakProvider
from housekeeper.providers.rpm import RpmProvider
from housekeeper.updates import assign_update_action, update_instructions


class Package:
    def __init__(
        self, name="example", version="2-1", arch="x86_64", info="updating", source="updates"
    ):
        self.name, self.version, self.arch, self.info, self.source = (
            name,
            version,
            arch,
            info,
            source,
        )

    def get_name(self):
        return self.name

    def get_version(self):
        return self.version

    def get_arch(self):
        return self.arch

    def get_info(self):
        return self.info

    def get_id(self):
        return f"{self.name};{self.version};{self.arch};{self.source}"


class Results:
    def __init__(self, packages=(), exit_code=1, error=None):
        self.packages, self.exit_code, self.error = packages, exit_code, error

    def get_package_array(self):
        return self.packages

    def get_exit_code(self):
        return self.exit_code

    def get_error_code(self):
        return self.error

    def get_require_restart_array(self):
        return []


@pytest.fixture
def rpm_update(monkeypatch):
    provider = RpmProvider()
    # Launcher evidence is exercised separately; this fixture isolates PackageKit updates.
    monkeypatch.setattr(provider, "_validate_app", lambda _app: None)
    app = AppRecord(
        "rpm",
        "Example",
        provider="rpm",
        source=Source.RPM,
        version="1-1",
        metadata={"name": "example", "arch": "x86_64"},
    )
    backend = NS(
        installed=[Package(version="1-1", source="installed")],
        candidates=[Package()],
        preview=[Package()],
        calls=[],
        refreshed=0,
        queried=0,
        deployed=(),
        error=None,
        exit_code=1,
    )
    pk = NS(
        FilterEnum=NS(INSTALLED=1),
        ExitEnum=NS(SUCCESS=1, CANCELLED=2),
        TransactionFlagEnum=NS(SIMULATE=2, ONLY_TRUSTED=1),
        InfoEnum=NS(
            UPDATING="updating", INSTALLING="installing", REMOVING="removing", BLOCKED="blocked"
        ),
    )

    def refresh(*_args):
        backend.refreshed += 1
        return Results(error=backend.error)

    def update(flags, ids, *_args):
        backend.calls.append((flags, ids))
        if flags & (1 << pk.TransactionFlagEnum.SIMULATE):
            return Results(backend.preview)
        backend.deployed = tuple(
            f"{p.name}.{p.arch}" for p in backend.preview if p.info != "removing"
        )
        return Results(exit_code=backend.exit_code, error=backend.error)

    def get_updates(*_args):
        backend.queried += 1
        return Results(backend.candidates)

    client = NS(
        resolve=lambda _f, names, *_a: Results([p for p in backend.installed if p.name in names]),
        refresh_cache=refresh,
        get_updates=get_updates,
        update_packages=update,
    )
    monkeypatch.setattr(provider, "_client", lambda _kind: (client, pk, Gio, GLib))
    monkeypatch.setattr(provider, "_compare", lambda a, b: (a > b) - (a < b))
    monkeypatch.setattr(provider, "_verified_updates", lambda _plan: backend.deployed)
    monkeypatch.setattr(
        provider,
        "_local_versions",
        lambda names: {(p.name, p.arch): p.version for p in backend.installed if p.name in names},
    )
    return provider, app, backend


def test_rpm_check_preview_and_dependency_update(rpm_update):
    provider, app, backend = rpm_update
    backend.preview.append(Package("dependency", info="installing"))
    check = provider.prepare_update(app, [app], lambda *_: None)
    assert check.state == UpdateState.AVAILABLE
    assert backend.refreshed == 1 and not backend.deployed
    assert len(check.plan.changes) == 2
    result = provider.execute_update(app, check.plan, lambda *_: None)
    assert result.outcome == Outcome.SUCCESS
    assert backend.refreshed == 1
    assert backend.calls[-1] == (2, ["example;2-1;x86_64;updates"])


def test_rpm_batch_queries_updates_once_and_only_previews_matching_desktop_apps(rpm_update):
    from housekeeper.batch_updates import UpdateBatch

    provider, app, backend = rpm_update
    app = replace(app, update_action=UpdateAction.CHECK)
    current = [
        replace(app, key=str(i), metadata={"name": f"current{i}", "arch": "x86_64"})
        for i in range(100)
    ]
    backend.candidates.extend([Package("system-library"), Package("current0", arch="aarch64")])
    backend.preview.append(Package("dependency", info="installing"))
    report = UpdateBatch(lambda _: provider, [*current, app]).check(lambda *_: None)
    assert not report.errors and len(report.items) == 1
    assert backend.refreshed == 1 and backend.queried == 1 and len(backend.calls) == 1
    assert len(report.items[0].plan.changes) == 2
    # Even the same provider object must query again when executing; discovery is not authorization.
    result = provider.execute_update(app, report.items[0].plan, lambda *_: None)
    assert result.outcome == Outcome.SUCCESS and backend.queried == 2


def test_rpm_discovery_empty_and_failed_queries_are_distinct(rpm_update):
    provider, app, backend = rpm_update
    backend.candidates = []
    assert provider.discover_updates([app], lambda *_: None) == set()
    backend.error = NS(get_details=lambda: "Network unavailable")
    with pytest.raises(ManagementError, match="Network"):
        provider.discover_updates([app], lambda *_: None)
    assert provider._discovered_updates is None


def test_rpm_cancelled_discovery_does_not_refresh_sources(rpm_update):
    provider, app, backend = rpm_update
    provider.request_cancel()
    with pytest.raises(OperationCancelled):
        provider.discover_updates([app], lambda *_: None)
    assert backend.refreshed == backend.queried == 0


def test_rpm_current_requires_successful_query(rpm_update):
    provider, app, backend = rpm_update
    backend.candidates = []
    assert provider.prepare_update(app, [app], lambda *_: None).state == UpdateState.CURRENT
    backend.error = NS(get_details=lambda: "Network unavailable")
    with pytest.raises(ManagementError, match="Network"):
        provider.prepare_update(app, [app], lambda *_: None)


@pytest.mark.parametrize(
    "preview",
    [
        [],
        [Package(info="downgrading")],
        [Package(), Package("other", info="removing")],
        [Package("other")],
        [Package(), Package("housekeeper", info="installing")],
        [Package(), Package("dependency", version="0-1")],
    ],
)
def test_rpm_rejects_incomplete_or_unsafe_preview(rpm_update, preview):
    provider, app, backend = rpm_update
    backend.preview = preview
    backend.installed.append(Package("dependency", version="1-1"))
    with pytest.raises(ManagementError):
        provider.prepare_update(app, [app], lambda *_: None)
    assert not backend.deployed


def test_rpm_normal_old_version_replacement_is_allowed(rpm_update):
    provider, app, backend = rpm_update
    backend.preview.append(Package(version="1-1", info="removing", source="installed"))
    assert len(provider.prepare_update(app, [app], lambda *_: None).plan.changes) == 1


def test_rpm_candidates_are_exact_and_unambiguous(rpm_update):
    provider, app, backend = rpm_update
    backend.candidates.extend([Package(arch="aarch64"), Package("other")])
    plan = provider.prepare_update(app, [app], lambda *_: None).plan
    assert plan.target == "example;2-1;x86_64;updates"
    backend.candidates.append(Package(version="3-1"))
    with pytest.raises(ManagementError, match="ambiguous"):
        provider.prepare_update(app, [app], lambda *_: None)


def test_rpm_rechecks_dependency_versions_before_execution(rpm_update):
    provider, app, backend = rpm_update
    backend.preview.append(Package("dependency"))
    backend.installed.append(Package("dependency", version="1-1"))
    plan = provider.prepare_update(app, [app], lambda *_: None).plan
    backend.installed[-1].version = "1-2"
    with pytest.raises(ManagementError, match="plan changed"):
        provider.execute_update(app, plan, lambda *_: None)
    assert not backend.deployed


def test_rpm_cancellation_before_check(rpm_update):
    provider, app, _backend = rpm_update
    provider.request_cancel()
    with pytest.raises(OperationCancelled):
        provider.prepare_update(app, [app], lambda *_: None)


@pytest.mark.parametrize("candidate", [Package(version="0-1"), Package(info="blocked")])
def test_rpm_rejects_downgrade_and_blocked_candidates(rpm_update, candidate):
    provider, app, backend = rpm_update
    backend.candidates = [candidate]
    with pytest.raises(ManagementError):
        provider.prepare_update(app, [app], lambda *_: None)
    assert not backend.calls


def test_rpm_rechecks_repository_configuration(rpm_update, monkeypatch):
    provider, app, backend = rpm_update
    monkeypatch.setattr(provider, "_repository_state", lambda: "original source")
    plan = provider.prepare_update(app, [app], lambda *_: None).plan
    monkeypatch.setattr(provider, "_repository_state", lambda: "changed source")
    with pytest.raises(ManagementError, match="plan changed"):
        provider.execute_update(app, plan, lambda *_: None)
    assert not backend.deployed


def test_rpm_detects_stale_packagekit_installed_cache(rpm_update, monkeypatch):
    provider, app, _backend = rpm_update
    monkeypatch.setattr(provider, "_local_versions", lambda _names: {("example", "x86_64"): "2-1"})
    with pytest.raises(ManagementError, match="package data changed"):
        provider.prepare_update(app, [app], lambda *_: None)


def test_rpm_real_version_ordering():
    pytest.importorskip("rpm")
    assert RpmProvider._compare("1:1.0-1", "9.0-1") > 0
    assert RpmProvider._compare("1.10-1", "1.9-1") > 0
    assert RpmProvider._compare("0:2-1", "2-1") == 0


class Ref:
    def __init__(self, identity, commit="old", origin="fixture"):
        self.identity, self.commit, self.origin = identity, commit, origin

    def format_ref(self):
        return self.identity

    def get_commit(self):
        return self.commit

    def get_origin(self):
        return self.origin

    def get_kind(self):
        return "app" if self.identity.startswith("app/") else "runtime"


@pytest.fixture
def flatpak_discovery(monkeypatch):
    provider = FlatpakProvider()
    app = AppRecord(
        "app",
        "App",
        provider="flatpak",
        identity="app/example/x86_64/stable",
        metadata={"installation": "/user"},
    )
    backend = NS(candidates=[], calls=[], error=None)

    def query(cancel):
        backend.calls.append(cancel)
        if backend.error:
            raise backend.error
        return backend.candidates

    installation = NS(
        get_path=lambda: Gio.File.new_for_path("/user"),
        drop_caches=lambda _c: None,
        list_installed_refs_for_update=query,
    )
    monkeypatch.setattr(
        "housekeeper.providers.flatpak.load_flatpak", lambda: NS(RefKind=NS(APP="app"))
    )
    monkeypatch.setattr(
        "housekeeper.providers.flatpak.configured_installations", lambda _fp: ([installation], [])
    )
    return provider, app, backend


def test_flatpak_bulk_discovery_matches_full_ref_and_excludes_runtimes(flatpak_discovery):
    provider, app, backend = flatpak_discovery
    backend.candidates = [Ref(app.identity), Ref("runtime/platform/x86_64/stable")]
    others = [
        replace(app, key="branch", identity="app/example/x86_64/beta"),
        replace(app, key="runtime", identity="runtime/platform/x86_64/stable"),
    ]
    assert provider.discover_updates([app, *others], lambda *_: None) == {app.key}
    assert len(backend.calls) == 1


def test_flatpak_empty_and_failed_discovery_are_distinct(flatpak_discovery):
    provider, app, backend = flatpak_discovery
    assert provider.discover_updates([app], lambda *_: None) == set()
    backend.error = GLib.Error.new_literal(Gio.io_error_quark(), "Offline", Gio.IOErrorEnum.FAILED)
    with pytest.raises(ManagementError, match="Offline"):
        provider.discover_updates([app], lambda *_: None)


def test_flatpak_discovery_rejects_wrong_installation(flatpak_discovery):
    provider, app, backend = flatpak_discovery
    with pytest.raises(ManagementError, match="unavailable"):
        provider.discover_updates(
            [replace(app, metadata={"installation": "/system"})], lambda *_: None
        )
    assert not backend.calls


@pytest.mark.parametrize("during", [False, True])
def test_flatpak_discovery_acknowledges_cancellation(flatpak_discovery, during):
    provider, app, backend = flatpak_discovery
    if not during:
        provider.request_cancel()
    with pytest.raises(OperationCancelled):
        provider.discover_updates([app], lambda *_: provider.request_cancel())
    assert len(backend.calls) == int(during)


class FlatpakTransaction:
    def __init__(self, refs, operations, fp):
        self.refs, self.operations, self.fp = refs, operations, fp
        self.handlers = {}
        self.executed = False
        self.fail_after = None

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def get_operations(self):
        return self.operations

    def run(self, cancel):
        if cancel.is_cancelled():
            raise GLib.Error.new_literal(
                Gio.io_error_quark(), "Cancelled", Gio.IOErrorEnum.CANCELLED
            )
        signal = "ready-pre-auth" if "ready-pre-auth" in self.handlers else "ready"
        if not self.handlers[signal](self):
            raise GLib.Error.new_literal(self.fp.error_quark(), "Aborted", self.fp.Error.ABORTED)
        self.executed = True
        for index, op in enumerate(self.operations):
            if self.fail_after == index:
                raise RuntimeError("Download failed")
            if "new-operation" in self.handlers:
                progress = NS(
                    connect=lambda *_: None,
                    get_status=lambda: "Downloading extra data: 1 MB/10 MB",
                    get_is_estimating=lambda: False,
                    get_progress=lambda: 10,
                )
                self.handlers["new-operation"](self, op, progress)
                if cancel.is_cancelled():
                    raise GLib.Error.new_literal(
                        Gio.io_error_quark(), "Cancelled", Gio.IOErrorEnum.CANCELLED
                    )
            self.refs[op.get_ref()] = Ref(op.get_ref(), op.get_commit())
            self.handlers["operation-done"](self, op, op.get_commit(), 0)
        return True


def operation(identity, commit="new", kind=1, source="fixture"):
    return NS(
        get_ref=lambda: identity,
        get_commit=lambda: commit,
        get_remote=lambda: source,
        get_operation_type=lambda: kind,
        get_is_skipped=lambda: False,
        get_download_size=lambda: 123,
    )


@pytest.fixture
def flatpak_update(monkeypatch):
    provider = FlatpakProvider()
    identity = "app/org.example.App/x86_64/stable"
    app = AppRecord(
        "flatpak",
        "Example",
        provider="flatpak",
        source=Source.FLATPAK,
        identity=identity,
        origin="fixture",
        metadata={"installation": "/user", "commit": "old"},
    )
    refs = {identity: Ref(identity)}
    fp = NS(
        TransactionOperationType=NS(INSTALL=0, UPDATE=1, UNINSTALL=2),
        Error=NS(ABORTED=1),
        error_quark=lambda: GLib.quark_from_string("hk-test-flatpak"),
    )
    remote = NS(
        get_name=lambda: "fixture",
        get_url=lambda: "file:///fixture",
        get_gpg_verify=lambda: True,
        get_disabled=lambda: False,
    )
    installation = NS(
        list_installed_refs=lambda _c: list(refs.values()),
        list_remotes=lambda _c: [remote],
        drop_caches=lambda _c: None,
    )
    backend = NS(
        operations=[operation(identity)], transactions=[], targets=[], refs=refs, fail_after=None
    )

    def transaction(_app, target=None):
        provider.cancel = Gio.Cancellable()
        if provider.cancel_requested:
            provider.cancel.cancel()
        tx = FlatpakTransaction(refs, backend.operations, fp)
        tx.fail_after = backend.fail_after
        backend.transactions.append(tx)
        backend.targets.append(target)
        return fp, installation, tx

    monkeypatch.setattr(provider, "_update_transaction", transaction)
    return provider, app, backend


def test_flatpak_preview_pins_commit_and_preserves_inventory_identity(flatpak_update):
    provider, app, backend = flatpak_update
    plan = provider.prepare_update(app, [app], lambda *_: None).plan
    assert not backend.transactions[0].executed and backend.refs[app.identity].commit == "old"
    assert plan.target == "new" and plan.download_size == 123
    result = provider.execute_update(app, plan, lambda *_: None)
    assert backend.targets == [None, "new"]
    assert result.outcome == Outcome.SUCCESS and app.key == "flatpak"


@pytest.mark.parametrize("changed", ["installed", "dependency", "target", "source"])
def test_flatpak_rejects_changed_plan(flatpak_update, changed):
    provider, app, backend = flatpak_update
    plan = provider.prepare_update(app, [app], lambda *_: None).plan
    if changed == "installed":
        backend.refs[app.identity].commit = "external"
    elif changed == "dependency":
        backend.operations.append(operation("runtime/org.example.Runtime/x86_64/stable"))
    elif changed == "target":
        backend.operations[0] = operation(app.identity, "newer")
    else:
        backend.operations[0] = operation(app.identity, source="another")
    result = provider.execute_update(app, plan, lambda *_: None)
    assert result.outcome == Outcome.FAILED
    assert not backend.transactions[-1].executed


def test_flatpak_current_and_dependency_only_updates(flatpak_update):
    provider, app, backend = flatpak_update
    backend.operations = []
    assert provider.prepare_update(app, [app], lambda *_: None).state == UpdateState.CURRENT
    backend.operations = [operation("runtime/org.example.Runtime/x86_64/stable")]
    result = provider.prepare_update(app, [app], lambda *_: None)
    assert result.state == UpdateState.CURRENT and result.plan is None
    assert not any(tx.executed for tx in backend.transactions)


def test_flatpak_same_commit_operation_with_new_runtime_is_not_an_app_update(flatpak_update):
    provider, app, backend = flatpak_update
    backend.operations = [
        operation(app.identity, "old"),
        operation("runtime/platform/x86_64/stable"),
    ]
    assert provider.prepare_update(app, [app], lambda *_: None).state == UpdateState.CURRENT


def test_flatpak_partial_completion(flatpak_update):
    provider, app, backend = flatpak_update
    backend.operations.append(operation("runtime/org.example.Runtime/x86_64/stable"))
    plan = provider.prepare_update(app, [app], lambda *_: None).plan
    backend.fail_after = 1
    result = provider.execute_update(app, plan, lambda *_: None)
    assert result.outcome == Outcome.PARTIAL
    assert result.completed == (app.identity,)
    assert "Download failed" in result.errors[0]


def test_flatpak_cancellation_is_not_current(flatpak_update):
    provider, app, _backend = flatpak_update
    provider.request_cancel()
    with pytest.raises(OperationCancelled):
        provider.prepare_update(app, [app], lambda *_: None)


def test_flatpak_wrong_installation_rejected(flatpak_update):
    provider, app, _backend = flatpak_update
    plan = provider.prepare_update(app, [app], lambda *_: None).plan
    with pytest.raises(ManagementError, match="another installation"):
        provider.execute_update(app, replace(plan, installation="/system"), lambda *_: None)


def test_flatpak_cancel_during_transaction_creation(flatpak_update, monkeypatch):
    provider, app, _backend = flatpak_update

    def cancelled(*_args):
        raise GLib.Error.new_literal(Gio.io_error_quark(), "Cancelled", Gio.IOErrorEnum.CANCELLED)

    monkeypatch.setattr(provider, "_update_transaction", cancelled)
    provider.request_cancel()
    with pytest.raises(OperationCancelled):
        provider.prepare_update(app, [app], lambda *_: None)


@pytest.mark.parametrize(
    "event",
    [
        "add-new-remote",
        "basic-auth-start",
        "webflow-start",
        "end-of-lifed-with-rebase",
        "install-authenticator",
    ],
)
def test_flatpak_external_interactions_are_refused(flatpak_update, event):
    provider, app, backend = flatpak_update
    provider.prepare_update(app, [app], lambda *_: None)
    tx = backend.transactions[0]
    assert tx.handlers[event](tx) is False
    assert not tx.executed


def test_flatpak_ambiguous_dependency_origin_is_not_chosen(flatpak_update):
    provider, app, backend = flatpak_update
    provider.prepare_update(app, [app], lambda *_: None)
    tx = backend.transactions[0]
    assert tx.handlers["choose-remote-for-ref"](tx, "runtime/example", ["fixture"]) == 0
    assert tx.handlers["choose-remote-for-ref"](tx, "runtime/example", ["fixture", "other"]) == -1


def test_update_capability_is_independent_and_local(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    app = AppRecord(
        "rpm", "Example", provider="rpm", action=Action.NONE, metadata={"rpm_verified": "true"}
    )
    assign_update_action(
        app, {"rpm": ProviderCapabilities(update_preview=True, update_execute=True)}
    )
    assert app.update_action == UpdateAction.CHECK and app.action == Action.NONE
    app.metadata["name"] = "housekeeper"
    assign_update_action(
        app, {"rpm": ProviderCapabilities(update_preview=True, update_execute=True)}
    )
    assert app.update_action == UpdateAction.INSTRUCTIONS
    assert "Housekeeper" in app.update_reason


@pytest.mark.parametrize("source", list(Source))
def test_external_update_guidance_is_not_removal_guidance(source):
    app = AppRecord("external", "Example", source=source)
    assert "uninstall" not in update_instructions(app).lower()


def test_rpm_waiting_for_auth_explains_system_prompt():
    messages = []
    status = NS(
        get_status=lambda: NS(value_nick="waiting-for-auth"),
        get_percentage=lambda: 101,
        get_allow_cancel=lambda: True,
    )
    RpmProvider._update_progress(status, lambda *args: messages.append(args), "Updating")
    assert "password or fingerprint" in messages[0][0]
    assert messages[0][1:] == (None, True)


def test_authorization_notice_is_scoped_to_system_updates():
    from housekeeper.updates import authorization_notice

    assert "system authentication dialog" in authorization_notice(
        AppRecord("a", "A", provider="rpm")
    )
    assert "fingerprint" in authorization_notice(
        AppRecord("b", "B", provider="flatpak", scope="System")
    )
    assert not authorization_notice(AppRecord("c", "C", provider="flatpak", scope="User"))


@pytest.mark.parametrize("is_user", [False, True])
def test_flatpak_system_updates_use_helper_without_explicit_commit(monkeypatch, is_user):
    import housekeeper.providers.flatpak as module

    app = AppRecord(
        "a",
        "A",
        provider="flatpak",
        identity="app/org.example.App/x86_64/stable",
        origin="fixture",
        metadata={"installation": "/fixture", "commit": "old"},
    )
    installed = NS(
        format_ref=lambda: app.identity, get_origin=lambda: "fixture", get_commit=lambda: "old"
    )
    installation = NS(
        get_path=lambda: Gio.File.new_for_path("/fixture"),
        get_is_user=lambda: is_user,
        list_installed_refs=lambda _: [installed],
        get_remote_by_name=lambda *_: NS(get_disabled=lambda: False),
    )
    targets, interaction = [], []
    tx = NS(
        set_no_interaction=interaction.append,
        set_include_unused_uninstall_ops=lambda _: None,
        set_disable_dependencies=lambda _: None,
        set_disable_related=lambda _: None,
        set_disable_prune=lambda _: None,
        add_update=lambda *args: targets.append(args),
    )
    monkeypatch.setattr(
        module, "load_flatpak", lambda: NS(Transaction=NS(new_for_installation=lambda *_: tx))
    )
    monkeypatch.setattr(module, "configured_installations", lambda _: ([installation], []))
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    FlatpakProvider()._update_transaction(app, "confirmed")
    assert targets == [(app.identity, None, "confirmed" if is_user else None)]
    assert interaction == [False]


@pytest.mark.parametrize("cancel_at", [0, 1])
def test_flatpak_download_cancel_preserves_completed_components(flatpak_update, cancel_at):
    provider, app, backend = flatpak_update
    runtime = "runtime/org.example.Runtime/x86_64/stable"
    backend.operations = [operation(runtime), operation(app.identity)]
    plan = provider.prepare_update(app, [app], lambda *_: None).plan
    downloads = []

    def progress(message, _fraction, can_cancel):
        if message.startswith("Downloading extra data:"):
            assert can_cancel
            downloads.append(message)
            if len(downloads) > cancel_at:
                provider.request_cancel()

    result = provider.execute_update(app, plan, progress)
    assert backend.refs[app.identity].commit == "old"
    assert result.outcome == (Outcome.CANCELLED if cancel_at == 0 else Outcome.PARTIAL)
    assert result.completed == (() if cancel_at == 0 else (runtime,))
