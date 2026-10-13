"""Housekeeper updates itself only through the source the running copy came from."""

from types import SimpleNamespace as NS

import pytest

from housekeeper import APP_ID
from housekeeper.batch_updates import UpdateBatch, UpdateItem, UpdateReport
from housekeeper.models import (
    AppRecord,
    FileOwnershipResult,
    FileOwnershipState,
    InstallationInstance,
    ManagementError,
    OperationResult,
    Outcome,
    ProviderCapabilities,
    Source,
    UpdateAction,
    UpdateChange,
    UpdateCheckResult,
    UpdatePlan,
    UpdateState,
)
from housekeeper.self_update import (
    InstallMethod,
    SelfInstallation,
    SelfUpdater,
    detect_installation,
    installation_record,
    restart_command,
    updated_self,
)
from housekeeper.update_cache import UpdateCache

INSTALLED = "/usr/share/housekeeper/housekeeper/__init__.py"
RPM = SelfInstallation(InstallMethod.RPM, "housekeeper", "noarch", updatable=True)


def owned(*owners):
    return lambda _path: FileOwnershipResult(FileOwnershipState.OWNED, owners)


def detect(tmp_path, module=INSTALLED, owners=None):
    return detect_installation(
        module,
        flatpak_info=tmp_path / "missing",
        owners=owners or owned("rpm:housekeeper:noarch"),
    )


@pytest.fixture
def rpm_capable(monkeypatch):
    def capabilities(supported=True):
        monkeypatch.setattr(
            "housekeeper.providers.rpm.RpmProvider.capabilities",
            lambda _self: ProviderCapabilities(
                update_preview=supported,
                update_execute=supported,
                update_reason="" if supported else "Not this distribution.",
            ),
        )

    capabilities()
    return capabilities


def test_rpm_installation_updates_in_the_app(tmp_path, rpm_capable):
    installation = detect(tmp_path)
    assert installation.method == InstallMethod.RPM
    assert (installation.package, installation.arch) == ("housekeeper", "noarch")
    assert installation.updatable and not installation.reason


def test_rpm_installation_on_an_unsupported_host_explains_why(tmp_path, rpm_capable):
    rpm_capable(False)
    installation = detect(tmp_path)
    assert installation.method == InstallMethod.RPM and not installation.updatable
    assert "Not this distribution." in installation.reason


def test_flatpak_is_recognized_by_its_sandbox_before_package_ownership(tmp_path):
    info = tmp_path / ".flatpak-info"
    info.write_text(
        f"[Application]\nname={APP_ID}\n\n[Instance]\narch=x86_64\n"
        "app-path=/var/lib/flatpak/app/x/current/files\n"
    )
    installation = detect_installation(
        "/app/share/housekeeper/housekeeper/__init__.py",
        flatpak_info=info,
        owners=lambda _path: pytest.fail("A sandbox has no host package to query"),
    )
    assert installation.method == InstallMethod.FLATPAK and not installation.updatable
    assert installation.command == f"flatpak update {APP_ID}"
    assert "Flatpak" in installation.reason


def test_snap_is_recognized_by_its_location(tmp_path):
    installation = detect(
        tmp_path,
        "/snap/housekeeper/12/usr/share/housekeeper/housekeeper/__init__.py",
        owners=lambda _path: pytest.fail("Snap files are not in a package database"),
    )
    assert installation.method == InstallMethod.SNAP
    assert installation.command == "snap refresh housekeeper"


def test_source_checkout_is_not_the_installed_package(tmp_path):
    (tmp_path / "meson.build").write_text("project('housekeeper')\n")
    (tmp_path / ".git").mkdir()
    module = tmp_path / "src/housekeeper/__init__.py"
    module.parent.mkdir(parents=True)
    module.touch()
    installation = detect(
        tmp_path, module, owners=lambda _path: pytest.fail("A checkout is never a package")
    )
    assert installation.method == InstallMethod.SOURCE and not installation.updatable
    assert installation.command == f"git -C {tmp_path} pull"


@pytest.mark.parametrize(
    "owner,method,command",
    [
        ("deb:housekeeper", InstallMethod.DEB, "sudo apt install --only-upgrade -- housekeeper"),
        ("pacman:local:housekeeper", InstallMethod.PACMAN, "sudo pacman -Syu"),
        ("apk:installed:housekeeper", InstallMethod.APK, "sudo apk upgrade -- housekeeper"),
    ],
)
def test_other_package_managers_give_their_command(tmp_path, owner, method, command):
    installation = detect(tmp_path, owners=owned(owner))
    assert installation.method == method and installation.package == "housekeeper"
    assert not installation.updatable and installation.command == command


@pytest.mark.parametrize(
    "result",
    [
        FileOwnershipResult(FileOwnershipState.UNOWNED),
        FileOwnershipResult(FileOwnershipState.ERROR, reason="rpm: database locked"),
        FileOwnershipResult(FileOwnershipState.OWNED, ("rpm:a:noarch", "rpm:b:noarch")),
    ],
)
def test_unidentified_installations_are_never_updated(tmp_path, result):
    installation = detect(tmp_path, owners=lambda _path: result)
    assert installation.method == InstallMethod.UNKNOWN and not installation.updatable
    assert installation.reason
    if result.reason:
        assert result.reason in installation.reason


def record(key="housekeeper", name="housekeeper", arch="noarch", visible=True, version="1-1"):
    return AppRecord(
        key,
        "Housekeeper",
        source=Source.RPM,
        provider="rpm",
        visible=visible,
        version=version,
        identity=f"{name}-{version}.{arch}",
        metadata={"name": name, "arch": arch},
        installation=InstallationInstance(key, "rpm", "/:/var/lib/rpm", f"{name}:{arch}", version),
    )


def plan(app, target="housekeeper;2-1;noarch;updates"):
    return UpdatePlan(
        app.key,
        "rpm",
        target,
        "System",
        "1-1",
        (UpdateChange("housekeeper.noarch", target, "update", "updates", "1-1", "2-1"),),
        "fingerprint",
        "Update",
    )


def test_record_is_the_host_package_running_now_preferring_its_visible_launcher():
    hidden, visible = record("hidden", visible=False), record("visible")
    other = record("other", name="other")
    elsewhere = record("elsewhere")
    elsewhere.installation = InstallationInstance("x", "rpm", "/:/srv/root/rpm", "h:noarch", "1")
    assert installation_record(RPM, [other, elsewhere, hidden, visible]) is visible
    assert installation_record(RPM, [hidden]) is hidden
    assert installation_record(RPM, [other, elsewhere]) is None
    unavailable = SelfInstallation(InstallMethod.RPM, "housekeeper", "noarch")
    assert installation_record(unavailable, [visible]) is None


def provider_for(app, state=UpdateState.AVAILABLE, calls=None):
    calls = [] if calls is None else calls

    def prepare(subject, _inventory, _progress, **kwargs):
        calls.append((subject.key, kwargs))
        return UpdateCheckResult(state, plan(subject) if state == UpdateState.AVAILABLE else None)

    return NS(
        prepare_update=prepare,
        execute_update=lambda *args, **kwargs: (
            calls.append(("execute", kwargs)) or OperationResult(Outcome.SUCCESS, "done")
        ),
        request_cancel=lambda: None,
    )


def test_updater_refuses_installations_it_cannot_update():
    unavailable = SelfInstallation(InstallMethod.SOURCE, reason="Pull.", command="git pull")
    updater = SelfUpdater(lambda _app: pytest.fail("No provider"), [record()], unavailable)
    with pytest.raises(ManagementError, match="Pull."):
        updater.check(lambda *_: None)


def test_updater_checks_and_installs_with_housekeeper_allowed_as_the_subject():
    app, calls = record(), []
    provider = provider_for(app, calls=calls)
    updater = SelfUpdater(lambda _app: provider, [app], RPM)
    item = updater.check(lambda *_: None)
    assert item.app is app and item.names == ("Housekeeper",)
    assert calls == [("housekeeper", {"allow_self": True})]
    assert updater.execute(item, lambda *_: None).outcome == Outcome.SUCCESS
    assert calls[-1] == ("execute", {"allow_self": True})
    current = SelfUpdater(lambda _app: provider_for(app, UpdateState.CURRENT), [app], RPM)
    assert current.check(lambda *_: None) is None


def test_updater_requires_the_package_in_the_inventory():
    with pytest.raises(Exception, match="app list"):
        SelfUpdater(lambda _app: pytest.fail("No provider"), [], RPM).check(lambda *_: None)


def test_batch_lists_housekeeper_apart_and_reuses_the_rpm_discovery():
    own = record()
    own.update_action = UpdateAction.INSTRUCTIONS
    other = record("example", name="example")
    other.update_action = UpdateAction.CHECK
    calls, discovered = [], []

    def prepare(subject, _inventory, _progress, **kwargs):
        calls.append((subject.key, kwargs))
        return UpdateCheckResult(UpdateState.CURRENT)

    provider = NS(
        discover_updates=lambda apps, _progress: discovered.append(apps) or {own.key},
        prepare_update=lambda subject, inventory, progress, **kwargs: (
            provider_for(subject, calls=calls).prepare_update(
                subject, inventory, progress, **kwargs
            )
            if subject is own
            else prepare(subject, inventory, progress, **kwargs)
        ),
        request_cancel=lambda: None,
    )
    report = UpdateBatch(lambda _app: provider, [own, other]).check(
        lambda *_: None, ("rpm",), installation=RPM
    )
    # Housekeeper is neither a row nor an app needing its own updater.
    assert report.items == () and report.unsupported == 0
    assert discovered == [[other]]
    assert report.self_checked and report.self_update.app is own
    assert calls == [("housekeeper", {"refresh": False, "use_discovery": True, "allow_self": True})]


def test_batch_without_rpm_apps_checks_housekeeper_with_a_fresh_query():
    own, calls = record(), []
    report = UpdateBatch(lambda _app: provider_for(own, calls=calls), [own]).check(
        lambda *_: None, ("flatpak",), installation=RPM
    )
    assert report.self_update.app is own
    assert calls == [("housekeeper", {"refresh": True, "allow_self": True})]


def test_batch_keeps_housekeeper_failure_out_of_the_applications_check():
    own = record()

    def failing(*_args, **_kwargs):
        raise RuntimeError("PackageKit unavailable")

    provider = NS(prepare_update=failing, request_cancel=lambda: None)
    report = UpdateBatch(lambda _app: provider, [own]).check(
        lambda *_: None, ("rpm",), installation=RPM
    )
    assert report.errors == () and not report.self_checked and report.self_update is None


def test_batch_explains_how_to_update_an_installation_it_cannot_update():
    own = record()
    flatpak = SelfInstallation(InstallMethod.FLATPAK, reason="Use Flatpak.", command="flatpak up")
    report = UpdateBatch(lambda _app: pytest.fail("No provider"), [own]).check(
        lambda *_: None, ("rpm",), installation=flatpak
    )
    assert not report.self_checked and report.unsupported == 0
    assert report.self_note == "Use Flatpak.\n\nflatpak up"
    # An installation it can update needs no note, found or not.
    assert (
        not UpdateBatch(lambda _app: provider_for(own), [own])
        .check(lambda *_: None, ("rpm",), installation=RPM)
        .self_note
    )


def test_cache_keeps_housekeeper_update_until_its_package_changes(tmp_path):
    own = record()
    item = UpdateItem(own, plan(own), (own.name,))
    cache = UpdateCache(tmp_path / "updates.json")
    cache.save(UpdateReport((), self_update=item, self_checked=True), [own], 1_700_000_000)
    loaded = cache.load([own]).report
    assert loaded.self_checked and loaded.self_update == item
    changed = record(version="2-1")
    reloaded = cache.load([changed]).report
    assert reloaded.self_update is None and not reloaded.self_checked
    cache.save(UpdateReport((), self_checked=True), [own], 1_700_000_000)
    current = cache.load([own]).report
    assert current.self_checked and current.self_update is None
    cache.save(UpdateReport(()), [own], 1_700_000_000)
    assert not cache.load([own]).report.self_checked
    cache.save(UpdateReport((), self_note="Use Flatpak."), [own], 1_700_000_000)
    assert cache.load([own]).report.self_note == "Use Flatpak."


def test_restart_follows_only_a_change_to_the_running_package():
    own = record()
    item = UpdateItem(own, plan(own), (own.name,))
    assert updated_self(item, OperationResult(Outcome.PARTIAL, "", ("housekeeper.noarch",)))
    assert not updated_self(item, OperationResult(Outcome.PARTIAL, "", ("python3-gobject",)))
    assert not updated_self(item, OperationResult(Outcome.FAILED, ""))


def test_restart_runs_the_launcher_as_a_normal_instance():
    assert restart_command(
        ["/usr/bin/python3", "/usr/bin/housekeeper", "--gapplication-service"], "/usr/bin/python3"
    ) == ("/usr/bin/python3", ["/usr/bin/python3", "/usr/bin/housekeeper"])
