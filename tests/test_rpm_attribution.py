"""Package-owned launchers identify sources; verified targets authorize management."""

import hashlib
from types import SimpleNamespace as NS

import pytest

from housekeeper.discovery import read_entry
from housekeeper.identity import classify
from housekeeper.models import Action, ManagementError, ProviderCapabilities, UpdateAction
from housekeeper.providers.rpm import RpmIndex, RpmProvider
from housekeeper.providers.rpm_attribution import RpmAttribution
from housekeeper.updates import assign_update_action, update_instructions


@pytest.fixture
def installation(tmp_path, monkeypatch):
    packages = []
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    monkeypatch.setattr("housekeeper.providers.rpm.host_support", lambda: (True, ""))
    monkeypatch.setattr(
        "housekeeper.providers.rpm_attribution.service_roots", lambda: [tmp_path / "services"]
    )

    def package(name):
        header = {
            "name": name,
            "arch": "x86_64",
            "epoch": 0,
            "version": "1",
            "release": "1",
            "filenames": [],
            "filemodes": [],
            "filelinktos": [],
            "filedigests": [],
            "filedigestalgo": 8,
            "requirename": [],
            "requireflags": [],
            "requireversion": [],
            "providename": [name],
            "provideflags": [8],
            "provideversion": ["1-1"],
        }
        packages.append(header)
        return header

    def file(header, name, contents="#!/bin/sh\nexit 0\n", link=None):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if link is not None:
            path.symlink_to(link)
        else:
            path.write_text(contents)
            path.chmod(0o755)
        header["filenames"].append(str(path))
        header["filemodes"].append(path.lstat().st_mode)
        header["filelinktos"].append(str(link) if link is not None else "")
        header["filedigests"].append(
            "" if link is not None else hashlib.sha256(path.read_bytes()).hexdigest()
        )
        return path

    def index():
        value = RpmIndex.__new__(RpmIndex)
        value.ts = NS(dbMatch=lambda _tag, path: [h for h in packages if path in h["filenames"]])
        value._cache = {}
        return value

    def record(path):
        app = classify(read_entry(path, path.parent))
        index().enrich(app)
        assign_update_action(
            app, {"rpm": ProviderCapabilities(update_preview=True, update_execute=True)}
        )
        return app

    def desktop(header, command, name="org.example.App", extra=""):
        return file(
            header,
            "applications/" + name + ".desktop",
            f"[Desktop Entry]\nType=Application\nName=Example\nExec={command}\n{extra}",
        )

    return NS(
        package=package, file=file, index=index, record=record, desktop=desktop, root=tmp_path
    )


def assert_blocked(app):
    assert app.provider == "rpm" and app.metadata["rpm_verified"] == "false"
    assert app.action == Action.NONE and app.update_action == UpdateAction.INSTRUCTIONS
    assert "verified" in app.update_reason
    assert "sudo dnf" not in update_instructions(app)


def test_unchanged_direct_application_is_manageable(installation):
    f = installation
    package = f.package("example")
    executable = f.file(package, "bin/example")
    app = f.record(f.desktop(package, executable))
    assert app.metadata["rpm_verified"] == "true"
    assert app.action == Action.UNINSTALL and app.update_action == UpdateAction.CHECK


@pytest.mark.parametrize(
    "mutation", ["desktop", "binary", "symlink", "missing_digest", "unknown_hash"]
)
def test_modified_or_unverifiable_files_keep_source_but_block_management(installation, mutation):
    f = installation
    package = f.package("example")
    executable = f.file(package, "bin/example")
    desktop = f.desktop(package, executable)
    if mutation == "desktop":
        desktop.write_text(desktop.read_text().replace("Name=Example", "Name=Unrelated"))
    elif mutation == "binary":
        executable.write_text("#!/bin/sh\necho different\n")
    elif mutation == "symlink":
        alternate = f.file(f.package("other"), "bin/other")
        executable.unlink()
        executable.symlink_to(alternate)
    elif mutation == "missing_digest":
        package["filedigests"][1] = ""
    else:
        package["filedigestalgo"] = 999
    assert_blocked(f.record(desktop))


def test_matching_packaged_symlink_is_verified_and_retargeting_is_blocked(installation):
    f = installation
    package = f.package("example")
    target = f.file(package, "libexec/example")
    executable = f.file(package, "bin/example", link=target)
    desktop = f.desktop(package, executable)
    assert f.record(desktop).metadata["rpm_verified"] == "true"
    executable.unlink()
    executable.symlink_to(f.file(package, "libexec/other"))
    assert_blocked(f.record(desktop))


@pytest.mark.parametrize("relationship", ["exact", "absent", "wrong_version", "wrong_arch"])
def test_shared_binary_requires_matching_installed_dependency(installation, relationship):
    pytest.importorskip("rpm")
    f = installation
    component, core = f.package("suite-writer"), f.package("suite-core")
    executable = f.file(core, "bin/suite")
    core["providename"] = ["suite-core(x86-64)"]
    if relationship != "absent":
        component["requirename"] = [
            "suite-core(i686)" if relationship == "wrong_arch" else "suite-core(x86-64)"
        ]
        component["requireflags"] = [8]
        component["requireversion"] = ["2-1" if relationship == "wrong_version" else "1-1"]
    app = f.record(f.desktop(component, f"{executable} --writer %U"))
    assert app.metadata["name"] == "suite-writer"
    if relationship == "exact":
        assert app.metadata["rpm_verified"] == "true" and app.action == Action.UNINSTALL
    else:
        assert_blocked(app)


def test_path_shadowing_cannot_substitute_an_unowned_command(installation, monkeypatch):
    f = installation
    package = f.package("example")
    f.file(package, "system-bin/example")
    shadow = f.root / "user-bin/example"
    shadow.parent.mkdir()
    shadow.write_text("#!/bin/sh\nexit 0\n")
    shadow.chmod(0o755)
    monkeypatch.setenv("PATH", str(shadow.parent))
    assert_blocked(f.record(f.desktop(package, "example")))


@pytest.mark.parametrize(
    "mutation", [None, "service", "wrong_target", "missing", "shadow", "systemd", "wrong_helper"]
)
def test_dbus_service_must_resolve_to_unchanged_app_owned_entry_point(
    installation, monkeypatch, mutation
):
    f = installation
    package, runtime = f.package("maps"), f.package("glib2")
    if mutation == "wrong_helper":
        runtime["name"] = "unrelated-wrapper"
    helper = f.file(runtime, "bin/gapplication")
    target = f.file(package, "libexec/maps")
    body = f"[D-BUS Service]\nName=org.example.App\nExec={target} --gapplication-service\n"
    if mutation == "wrong_target":
        other = f.file(runtime, "libexec/other")
        body = body.replace(str(target), str(other))
    if mutation == "systemd":
        body += "SystemdService=example.service\n"
    service = f.file(package, "services/org.example.App.service", body)
    desktop = f.desktop(
        package, f"{helper} launch org.example.App %U", extra="DBusActivatable=true\n"
    )
    if mutation == "service":
        service.write_text(body + "# locally changed\n")
    elif mutation == "missing":
        service.unlink()
    elif mutation == "shadow":
        shadow = f.root / "user-services"
        shadow.mkdir()
        (shadow / "different-filename.service").write_text(body)
        monkeypatch.setattr(
            "housekeeper.providers.rpm_attribution.service_roots", lambda: [shadow, service.parent]
        )
    app = f.record(desktop)
    if mutation is None:
        assert app.metadata["name"] == "maps" and app.metadata["rpm_verified"] == "true"
    else:
        assert_blocked(app)


def test_provider_revalidates_evidence_after_scan_before_any_packagekit_call(
    installation, monkeypatch
):
    f = installation
    package = f.package("example")
    executable = f.file(package, "bin/example")
    desktop = f.desktop(package, executable)
    app = f.record(desktop)
    monkeypatch.setattr("housekeeper.providers.rpm.RpmIndex", f.index)
    RpmProvider._validate_app(app)
    desktop.write_text(desktop.read_text().replace("Name=Example", "Name=Changed"))
    provider = RpmProvider()
    monkeypatch.setattr(
        provider, "_client", lambda *_: pytest.fail("Must stop before contacting PackageKit")
    )
    for operation in (
        lambda: provider.prepare(app, [app]),
        lambda: provider.execute(app, None, lambda *_: None),
        lambda: provider.prepare_update(app, [app], lambda *_: None),
        lambda: provider.execute_update(app, None, lambda *_: None),
    ):
        with pytest.raises(ManagementError, match="changed"):
            operation()


def test_unowned_launcher_is_display_only_even_with_an_owned_binary(installation):
    f = installation
    package = f.package("example")
    binary = f.file(package, "bin/example")
    desktop = f.root / "custom.desktop"
    desktop.write_text(f"[Desktop Entry]\nType=Application\nName=Custom\nExec={binary}\n")
    assert_blocked(f.record(desktop))


def test_file_verification_cache_does_not_hide_subsequent_changes(installation):
    f = installation
    package = f.package("example")
    path = f.file(package, "bin/example")
    verifier = RpmAttribution(f.index())
    assert verifier.verified_file(path, package)
    path.write_text("modified")
    assert not verifier.verified_file(path, package)


def test_unverified_app_cannot_enter_update_batch_or_bypass_provider_guard(installation):
    from housekeeper.batch_updates import UpdateBatch

    f = installation
    package = f.package("example")
    binary = f.file(package, "bin/example")
    desktop = f.desktop(package, binary)
    desktop.write_text(desktop.read_text() + "# changed\n")
    app = f.record(desktop)
    worker = UpdateBatch(lambda _: pytest.fail("Unverified apps must not reach a provider"), [app])
    report = worker.check(lambda *_: None)
    assert not report.items and report.unsupported == 1
    with pytest.raises(ManagementError, match="not been verified"):
        RpmProvider._validate_app(app)


@pytest.mark.parametrize("separate_service_package", [False, True])
def test_direct_dbus_app_with_hyphenated_name_and_related_service_package(
    installation, separate_service_package
):
    f = installation
    application = f.package("viewer")
    service_package = application
    if separate_service_package:
        pytest.importorskip("rpm")
        service_package = f.package("viewer-common")
        application["requirename"] = ["viewer-common"]
        application["requireflags"] = [8]
        application["requireversion"] = ["1-1"]
    executable = f.file(application, "bin/viewer")
    name = "org.example.font-viewer"
    f.file(
        service_package,
        "services/" + name + ".service",
        f"[D-BUS Service]\nName={name}\nExec={executable}\n",
    )
    app = f.record(f.desktop(application, executable, name=name, extra="DBusActivatable=true\n"))
    assert app.metadata["rpm_verified"] == "true"
