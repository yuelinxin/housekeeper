from dataclasses import replace

import pytest

from housekeeper.discovery import read_entry
from housekeeper.identity import classify
from housekeeper.models import Action, AppRecord, ManagementError, Source
from housekeeper.providers.flatpak import FlatpakIndex, FlatpakProvider
from housekeeper.providers.flatpak_attribution import parse_launch

pytestmark = pytest.mark.usefixtures("flatpak_command")


def installed(path="/user", scope="User", branch="stable", **metadata):
    return AppRecord(
        path + branch,
        "Official name",
        source=Source.FLATPAK,
        provider="flatpak",
        scope=scope,
        identity="app/org.example.App/x86_64/" + branch,
        action=Action.UNINSTALL,
        metadata={
            "app_id": "org.example.App",
            "installation": path,
            "installation_id": "default",
            "current": "true",
            **metadata,
        },
    )


def index(*apps):
    value = FlatpakIndex.__new__(FlatpakIndex)
    value.apps = list(apps)
    return value


@pytest.mark.parametrize(
    "options,target",
    [
        ((), "/user"),
        (("--user",), "/user"),
        (("--system",), "/system"),
        (("--installation=extra",), "/extra"),
    ],
)
def test_installation_selection_does_not_follow_desktop_path(entry, options, target):
    db = index(
        installed(),
        installed("/system", "System"),
        installed("/extra", "System", installation_id="extra"),
    )
    result = db.associate(classify(entry(("flatpak", "run", *options, "org.example.App"))))
    assert result.metadata["installation"] == target
    assert all(not app.entries and app.name == "Official name" for app in db.apps)


def test_ref_arguments_are_not_flatpak_options(entry):
    launch = parse_launch(entry(("flatpak", "run", "org.example.App", "--branch=beta")))
    assert launch.branch == "" and launch.arguments == ("--branch=beta",)
    assert (
        index(installed()).associate(
            classify(entry(("flatpak", "run", "org.example.App", "--branch=beta")))
        )
        is None
    )


@pytest.mark.parametrize(
    "options",
    [
        ("--user", "--system"),
        ("--branch",),
        ("--branch=a", "--branch=b"),
        ("--unknown",),
        ("--command=sh",),
    ],
)
def test_unknown_or_custom_launch_is_not_authorized(entry, options):
    assert (
        index(installed()).associate(
            classify(entry(("flatpak", "run", *options, "org.example.App")))
        )
        is None
    )


def test_current_branch_and_ambiguous_installation(entry):
    db = index(installed(branch="beta"), installed(current="false"))
    assert db.associate(classify(entry(("flatpak", "run", "org.example.App")))).identity.endswith(
        "/beta"
    )
    db = index(installed("/one", "System"), installed("/two", "System", installation_id="other"))
    assert db.associate(classify(entry(("flatpak", "run", "org.example.App")))) is None


def test_exported_command_requires_matching_current_deployment(desktop, tmp_path):
    app = installed()
    app.location = str(tmp_path / "deploy")
    root = tmp_path / "deploy/export/share/applications"
    command = "flatpak run --command=writer org.example.App --writer %U"
    exported = desktop(root=root, Exec=command)
    custom = desktop(root=tmp_path / "custom", Exec=command)
    db = index(app)
    assert db.associate(classify(read_entry(custom, custom.parent))) is not None
    exported.write_text(exported.read_text().replace("--writer", "--calc"))
    assert db.associate(classify(read_entry(custom, custom.parent))) is None


@pytest.mark.parametrize("operation", ["_transaction", "_update_transaction"])
def test_changed_launcher_rejected_before_transaction(desktop, monkeypatch, operation):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    path = desktop(Exec="flatpak run org.example.App")
    db = index(installed())
    app = db.associate(classify(read_entry(path, path.parent)))
    monkeypatch.setattr(FlatpakIndex, "__init__", lambda self: setattr(self, "apps", db.apps))
    FlatpakIndex.validate(app)
    path.write_text(path.read_text().replace("flatpak run org.example.App", "/usr/bin/true"))
    with pytest.raises(ManagementError, match="launcher"):
        getattr(FlatpakProvider(), operation)(app)


def test_changed_installation_selector_rejected(desktop, monkeypatch):
    path = desktop(Exec="flatpak run org.example.App")
    db = index(installed("/system", "System"))
    app = db.associate(classify(read_entry(path, path.parent)))
    db.apps.append(installed())
    monkeypatch.setattr(FlatpakIndex, "__init__", lambda self: setattr(self, "apps", db.apps))
    with pytest.raises(ManagementError, match="changed"):
        FlatpakIndex.validate(app)


def test_hidden_label_does_not_hide_unrelated_installation(entry):
    db = index(installed())
    hidden = replace(
        entry(("/usr/bin/true",), flatpak_id="org.example.App"),
        visible=False,
        reason="Hidden by a desktop entry override",
    )
    assert db.associate(classify(hidden)) is None
    assert db.apps[0].visible


def test_program_named_flatpak_is_not_the_system_manager(desktop, tmp_path):
    binary = tmp_path / "flatpak"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    path = desktop(Exec=f"{binary} run org.example.App")
    assert index(installed()).associate(classify(read_entry(path, path.parent))) is None


def test_dbus_activation_cannot_be_verified_from_exec(desktop):
    path = desktop(Exec="flatpak run org.example.App", DBusActivatable="true")
    assert index(installed()).associate(classify(read_entry(path, path.parent))) is None


@pytest.fixture
def dbus_export(desktop, tmp_path, monkeypatch, flatpak_command):
    app = installed(str(tmp_path / "installation"))
    app.location = str(tmp_path / "deploy")
    path = desktop(
        filename="org.example.App.desktop",
        root=tmp_path / "deploy/export/share/applications",
        Exec="flatpak run --branch=stable --arch=x86_64 --command=example "
        "--file-forwarding org.example.App @@u %U @@",
        DBusActivatable="true",
        **{"X-Flatpak": "org.example.App"},
    )
    db = index(app)
    db.installations = {app.metadata["installation"]: object()}
    services = tmp_path / "deploy/export/share/dbus-1/services"
    services.mkdir(parents=True)
    (services / "org.example.App.service").write_text(
        "[D-BUS Service]\nName=org.example.App\n"
        f"Exec={flatpak_command} run --branch=stable --arch=x86_64 "
        "--command=/app/bin/example org.example.App --gapplication-service\n"
    )
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(services.parent.parent))
    return db, path


def test_dbus_user_override_cannot_authorize_different_installation(dbus_export, tmp_path):
    db, exported = dbus_export
    db.apps.append(installed("/system", "System"))
    original = tmp_path / "deploy/export/share/dbus-1/services/org.example.App.service"
    override = tmp_path / "data/dbus-1/services/org.example.App.service"
    override.parent.mkdir(parents=True)
    override.write_text(original.read_text().replace(" run ", " run --system "))
    assert db.associate(classify(read_entry(exported, exported.parent))) is None


@pytest.mark.parametrize("change", ["target", "contents", "precedence", "symlink"])
@pytest.mark.parametrize("operation", ["revalidate", "_transaction", "_update_transaction"])
def test_effective_dbus_service_changes_invalidate_revalidation(
    dbus_export, tmp_path, monkeypatch, change, operation
):
    from housekeeper.attribution import attribute, revalidate

    db, exported = dbus_export
    service = tmp_path / "deploy/export/share/dbus-1/services/org.example.App.service"
    monkeypatch.setattr("housekeeper.inventory.discovery_indexes", lambda: (db,))
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    app = attribute(classify(read_entry(exported, exported.parent)), (db,))
    revalidate(app)
    if change == "target":
        service.write_text(service.read_text().replace(" run ", " run --system "))
    elif change == "contents":
        service.write_text(service.read_text() + "# changed after preview\n")
    elif change == "precedence":
        override = tmp_path / "data/dbus-1/services/org.example.App.service"
        override.parent.mkdir(parents=True)
        override.write_text(service.read_text())
    else:
        replacement = service.with_name("replacement")
        replacement.write_bytes(service.read_bytes())
        service.unlink()
        service.symlink_to(replacement)
    with pytest.raises(ManagementError, match="ownership changed"):
        if operation == "revalidate":
            revalidate(app)
        else:
            getattr(FlatpakProvider(), operation)(app)


@pytest.mark.parametrize("selector", ["--user", "--system", "--installation=extra"])
def test_service_selection_uses_full_installation_context(dbus_export, tmp_path, selector):
    db, exported = dbus_export
    if selector != "--user":
        db.apps[0].scope = "System"
        if selector == "--installation=extra":
            db.apps[0].metadata["installation_id"] = "extra"
        exported.write_text(exported.read_text().replace(" run ", f" run {selector} "))
    service = tmp_path / "deploy/export/share/dbus-1/services/org.example.App.service"
    override = tmp_path / "data/dbus-1/services/alternate-name.service"
    override.parent.mkdir(parents=True)
    override.write_text(service.read_text().replace(" run ", f" run {selector} "))
    assert db.associate(classify(read_entry(exported, exported.parent))) is not None


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "invalid",
        "duplicate",
        "wrong-name",
        "systemd",
        "command",
        "arguments",
        "wrapper",
        "branch",
        "architecture",
        "broken-link",
        "unsupported-alias",
    ],
)
def test_effective_service_uncertainty_never_falls_through(dbus_export, tmp_path, mutation):
    db, exported = dbus_export
    service = tmp_path / "deploy/export/share/dbus-1/services/org.example.App.service"
    override = tmp_path / "data/dbus-1/services/org.example.App.service"
    override.parent.mkdir(parents=True)
    contents = service.read_text()
    if mutation == "missing":
        service.unlink()
    elif mutation == "invalid":
        override.write_text("not a service file")
    elif mutation == "duplicate":
        override.write_text(contents)
        override.with_name("other.service").write_text(contents)
    elif mutation == "wrong-name":
        override.write_text(contents.replace("Name=org.example.App", "Name=org.example.Other"))
    elif mutation == "systemd":
        override.write_text(contents + "SystemdService=elsewhere.service\n")
    elif mutation == "command":
        override.write_text(contents.replace("--command=/app/bin/example", "--command=sh"))
    elif mutation == "arguments":
        override.write_text(contents.replace("--gapplication-service", "--guest-application"))
    elif mutation == "wrapper":
        override.write_text(contents.replace("Exec=", "Exec=/usr/bin/env "))
    elif mutation == "branch":
        db.apps.append(installed(branch="beta"))
        override.write_text(contents.replace("--branch=stable", "--branch=beta"))
    elif mutation == "architecture":
        db.apps.append(replace(db.apps[0], identity="app/org.example.App/aarch64/stable"))
        override.write_text(contents.replace("--arch=x86_64", "--arch=aarch64"))
    elif mutation == "unsupported-alias":
        override.with_name("alias.service").write_text(contents + "[Unsupported]\nKey=value\n")
    else:
        override.symlink_to(tmp_path / "absent")
    assert db.associate(classify(read_entry(exported, exported.parent))) is None


def test_runtime_service_has_priority_and_requires_exact_filename(dbus_export, tmp_path):
    db, exported = dbus_export
    service = tmp_path / "deploy/export/share/dbus-1/services/org.example.App.service"
    user = tmp_path / "data/dbus-1/services/org.example.App.service"
    user.parent.mkdir(parents=True)
    user.write_text(service.read_text().replace(" run ", " run --system "))
    runtime = tmp_path / "runtime/dbus-1/services"
    runtime.mkdir(parents=True)
    wrong_name = runtime / "ignored.service"
    wrong_name.write_bytes(service.read_bytes())
    assert db.associate(classify(read_entry(exported, exported.parent))) is None
    wrong_name.rename(runtime / "org.example.App.service")
    assert db.associate(classify(read_entry(exported, exported.parent))) is not None


@pytest.mark.parametrize("override", [False, True])
def test_dbus_export_is_one_component_not_an_extra_installation(
    dbus_export, tmp_path, monkeypatch, override
):
    from housekeeper.models import AttributionState, ProviderCapabilities
    from housekeeper.services import collect

    db, exported = dbus_export
    root = tmp_path / "applications"
    root.mkdir()
    path = root / exported.name
    if override:
        path.write_text(exported.read_text() + "Icon=/custom/icon.png\n")
    else:
        path.symlink_to(exported)
    monkeypatch.setattr(
        FlatpakProvider, "capabilities", lambda self: ProviderCapabilities(execute=True)
    )
    apps, *_ = collect(roots=[root], indexes=(db,))
    assert len(apps) == 1
    app = apps[0]
    assert app.source == Source.FLATPAK and app.entries[0].path == path
    assert app.attribution.state == AttributionState.CONFIRMED
    assert app.action == Action.UNINSTALL and app.installation and app.component
    if override:
        assert app.icon == "/custom/icon.png"


@pytest.mark.parametrize(
    "change",
    ["renamed", "activation", "exec", "label", "action", "working-directory", "missing-export"],
)
def test_dbus_export_requires_same_id_and_complete_semantics(dbus_export, tmp_path, change):
    db, exported = dbus_export
    path = tmp_path / exported.name
    text = exported.read_text()
    if change == "renamed":
        path = tmp_path / "org.example.Other.desktop"
    elif change == "activation":
        text = text.replace("DBusActivatable=true", "DBusActivatable=false")
    elif change == "exec":
        text = text.replace("--command=example", "--command=other")
    elif change == "label":
        text = text.replace("X-Flatpak=org.example.App", "X-Flatpak=org.example.Other")
    elif change == "action":
        text += "Actions=Other;\n[Desktop Action Other]\nName=Other\nExec=/usr/bin/true\n"
    elif change == "working-directory":
        text += "Path=/tmp\n"
    elif change == "missing-export":
        exported.unlink()
    path.write_text(text)
    assert db.associate(classify(read_entry(path, path.parent))) is None


@pytest.mark.parametrize("operation", ["_transaction", "_update_transaction"])
def test_dbus_export_changes_invalidate_all_transactions(
    dbus_export, tmp_path, monkeypatch, operation
):
    from housekeeper.attribution import attribute, revalidate

    db, exported = dbus_export
    path = tmp_path / exported.name
    path.write_text(exported.read_text())
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    monkeypatch.setattr("housekeeper.inventory.discovery_indexes", lambda: (db,))
    app = attribute(classify(read_entry(path, path.parent)), (db,))
    revalidate(app)
    exported.write_text(exported.read_text() + "Path=/tmp\n")
    with pytest.raises(ManagementError, match="ownership changed"):
        getattr(FlatpakProvider(), operation)(app)
