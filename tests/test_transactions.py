from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from housekeeper.models import AppRecord, ManagementError, Outcome, Source
from housekeeper.providers.flatpak import FlatpakProvider
from housekeeper.providers.rpm import RpmProvider, host_support


def test_immutable_and_unvalidated_hosts_are_read_only():
    assert host_support({"ID": "fedora", "VERSION_ID": "44"}, False)[0]
    assert not host_support({"ID": "fedora", "VERSION_ID": "44"}, True)[0]
    assert not host_support({"ID": "arch"}, False)[0]
    assert not host_support({"ID": "fedora", "VERSION_ID": "45"}, False)[0]


class Package:
    def __init__(self, name="example", version="1-1", arch="x86_64", info="removing"):
        self.name, self.version, self.arch = name, version, arch
        self.info = info

    def get_name(self):
        return self.name

    def get_version(self):
        return self.version

    def get_arch(self):
        return self.arch

    def get_id(self):
        return f"{self.name};{self.version};{self.arch};installed"

    def get_info(self):
        return self.info


class Results:
    def __init__(self, packages, error=None, exit_code=1):
        self.packages, self.error = packages, error
        self.exit_code = exit_code

    def get_error_code(self):
        return self.error

    def get_package_array(self):
        return self.packages

    def get_exit_code(self):
        return self.exit_code


@pytest.fixture
def rpm_backend(monkeypatch):
    package = Package()
    calls = []
    client = NS(resolve=lambda *_: Results([package]))

    def remove(flags, packages, deps, autoremove, *_):
        calls.append((flags, packages, deps, autoremove))
        return Results([package])

    client.remove_packages = remove
    pk = NS(
        FilterEnum=NS(INSTALLED=1),
        TransactionFlagEnum=NS(SIMULATE=2),
        ExitEnum=NS(SUCCESS=1, CANCELLED=2),
        InfoEnum=NS(REMOVING="removing"),
    )
    gio = NS(Cancellable=lambda: NS(cancel=lambda: None))
    manager = RpmProvider()
    # Launcher evidence is exercised separately; this fixture isolates PackageKit transactions.
    monkeypatch.setattr(manager, "_validate_app", lambda _app: None)
    monkeypatch.setattr(manager, "_client", lambda: (client, pk, gio, None))
    app = AppRecord(
        "test",
        "Example",
        provider="rpm",
        source=Source.RPM,
        identity="example-1-1.x86_64",
        version="1-1",
        metadata={"name": "example", "arch": "x86_64"},
    )
    return manager, client, app, calls


def test_rpm_no_cascade_or_autoremove(rpm_backend):
    manager, _client, app, calls = rpm_backend
    plan = manager.prepare(app, [app, replace(app, name="Example Editor")])
    assert plan.affected == ("Example", "Example Editor")
    result = manager.execute(app, plan, lambda *_: None)
    assert result.outcome == Outcome.SUCCESS
    assert all(not deps and not auto for _, _, deps, auto in calls)
    assert calls[-1][0] == 0


def test_rpm_cascade_preview_rejected(rpm_backend):
    manager, client, app, _calls = rpm_backend
    client.remove_packages = lambda *_: Results([Package(), Package("other")])
    with pytest.raises(ManagementError, match="single-package"):
        manager.prepare(app, [app])


def test_rpm_empty_simulation_rejected(rpm_backend):
    manager, client, app, _calls = rpm_backend
    client.remove_packages = lambda *_: Results([])
    with pytest.raises(ManagementError):
        manager.prepare(app, [app])


def test_rpm_external_upgrade_rejected(rpm_backend):
    manager, client, app, _calls = rpm_backend
    plan = manager.prepare(app, [app])
    client.resolve = lambda *_: Results([Package(version="2-1")])
    with pytest.raises(ManagementError, match="changed"):
        manager.execute(app, plan, lambda *_: None)


@pytest.mark.parametrize(
    "preview",
    [Results([Package(info="installed")]), Results([Package()], exit_code=2)],
)
def test_rpm_preview_requires_successful_explicit_removal(rpm_backend, preview):
    manager, client, app, _calls = rpm_backend
    client.remove_packages = lambda *_: preview
    with pytest.raises(ManagementError, match="single-package"):
        manager.prepare(app, [app])


@pytest.mark.parametrize(
    "message", ["Authorization cancelled", "Package manager is locked", "Simulation unsupported"]
)
def test_rpm_backend_errors_are_not_success(rpm_backend, message):
    manager, client, app, _calls = rpm_backend
    client.remove_packages = lambda *_: Results([], NS(get_details=lambda: message))
    with pytest.raises(ManagementError, match=message):
        manager.prepare(app, [app])


class Transaction:
    def __init__(self, identity):
        self.identity = identity
        self.handlers = {}
        self.executed = False

    def connect(self, name, callback):
        self.handlers[name] = callback

    def get_operations(self):
        return [
            NS(
                get_ref=lambda: self.identity,
                get_operation_type=lambda: 2,
                get_is_skipped=lambda: False,
            )
        ]

    def run(self, _cancel):
        if not self.handlers["ready"](self):
            raise RuntimeError("Aborted at ready")
        self.executed = True


def test_user_flatpak_removal_when_no_system_installation_exists(monkeypatch):
    from gi.repository import Gio, GLib

    import housekeeper.providers.flatpak as module

    app = AppRecord(
        "f",
        "Example",
        provider="flatpak",
        identity="app/org.example.App/x86_64/stable",
        metadata={"installation": "/example/user-flatpak"},
    )
    tx = Transaction(app.identity)
    tx.set_include_unused_uninstall_ops = lambda _value: None
    tx.set_disable_related = lambda _value: None
    tx.add_uninstall = lambda _ref: None
    installation = NS(
        get_path=lambda: NS(get_path=lambda: "/example/user-flatpak"),
        list_installed_refs=lambda _c: [
            NS(format_ref=lambda: app.identity, get_commit=lambda: "commit")
        ],
    )

    def no_system(_cancel):
        raise GLib.Error.new_literal(
            Gio.io_error_quark(), "No system installations found", Gio.IOErrorEnum.NOT_FOUND
        )

    fp = NS(
        Installation=NS(new_user=lambda _c: installation),
        get_system_installations=no_system,
        Transaction=NS(new_for_installation=lambda _i, _c: tx),
        TransactionOperationType=NS(UNINSTALL=2),
    )
    monkeypatch.setattr(module, "load_flatpak", lambda: fp)
    manager = FlatpakProvider()
    plan = manager.prepare(app, [app])
    assert not tx.executed
    result = manager.execute(app, plan, lambda *_: None)
    assert result.outcome == Outcome.SUCCESS
    assert tx.executed


def test_flatpak_preview_aborts_before_execution_and_rechecks_commit(monkeypatch):
    manager = FlatpakProvider()
    app = AppRecord(
        "f",
        "Example",
        provider="flatpak",
        identity="app/org.example.App/x86_64/stable",
        metadata={"installation": "/example/user-flatpak"},
    )
    tx = Transaction(app.identity)
    monkeypatch.setattr(
        manager,
        "_transaction",
        lambda _a: (NS(TransactionOperationType=NS(UNINSTALL=2)), tx, "old-commit"),
    )
    plan = manager.prepare(app, [app])
    assert not tx.executed
    changed = Transaction(app.identity)
    monkeypatch.setattr(manager, "_transaction", lambda _a: (None, changed, "new-commit"))
    with pytest.raises(ManagementError, match="changed"):
        manager.execute(app, plan, lambda *_: None)
    assert not changed.executed


def test_flatpak_matching_plan_executes(monkeypatch):
    manager = FlatpakProvider()
    app = AppRecord(
        "f",
        "Example",
        provider="flatpak",
        identity="app/org.example.App/x86_64/stable",
        metadata={"installation": "/example/user-flatpak"},
    )
    transactions = []

    def transaction(_app):
        tx = Transaction(app.identity)
        transactions.append(tx)
        return NS(TransactionOperationType=NS(UNINSTALL=2)), tx, "commit"

    monkeypatch.setattr(manager, "_transaction", transaction)
    plan = manager.prepare(app, [app])
    result = manager.execute(app, plan, lambda *_: None)
    assert result.outcome == Outcome.SUCCESS
    assert transactions[-1].executed
