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
def dbus_export(desktop, tmp_path):
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
    return db, path


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
