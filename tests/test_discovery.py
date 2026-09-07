from housekeeper.discovery import application_roots, read_entry, scan_entries


def test_xdg_order_and_relative_paths(tmp_path):
    env = {
        "XDG_DATA_HOME": str(tmp_path / "user"),
        "XDG_DATA_DIRS": f"{tmp_path}/a:relative:{tmp_path}/a:{tmp_path}/b",
    }
    assert application_roots(env, tmp_path) == [
        tmp_path / "user/applications",
        tmp_path / "a/applications",
        tmp_path / "b/applications",
    ]


def test_hidden_override_suppresses_system_entry(desktop, tmp_path):
    desktop(root=tmp_path / "system")
    desktop(root=tmp_path / "user", Hidden="true")
    apps, errors = scan_entries([tmp_path / "user", tmp_path / "system"], {"GNOME"})
    assert not errors
    assert len(apps) == 1
    assert not apps[0].visible
    assert "override" in apps[0].reason


def test_nested_id_and_non_english_name(desktop, tmp_path):
    path = desktop(name="\u5fae\u4fe1", filename="vendor/example.desktop")
    app = read_entry(path, tmp_path, {"GNOME"})
    assert app.name == "\u5fae\u4fe1"
    assert app.desktop_id == "vendor-example.desktop"


def test_quoted_executable_and_field_codes(desktop, tmp_path):
    binary = tmp_path / "My App.AppImage"
    binary.write_text("not executed")
    binary.chmod(0o755)
    app = read_entry(desktop(Exec=f'"{binary}" --name="two words" %U'), tmp_path, {"GNOME"})
    assert app.argv == (str(binary), "--name=two words", "%U")
    assert app.executable == str(binary)


def test_desktop_visibility_and_tryexec(desktop, tmp_path):
    for keys, reason in [
        ({"NoDisplay": "true"}, "auxiliary"),
        ({"OnlyShowIn": "KDE;"}, "environment"),
        ({"NotShowIn": "GNOME;"}, "Excluded"),
        ({"TryExec": "housekeeper-definitely-not-installed"}, "unavailable"),
    ]:
        app = read_entry(desktop(**keys), tmp_path, {"GNOME"})
        assert not app.visible
        assert reason in app.reason


def test_dbus_activation_without_exec(desktop, tmp_path):
    app = read_entry(desktop(Exec="", DBusActivatable="true"), tmp_path, {"GNOME"})
    assert app.dbus_activatable
    assert "executable is unavailable" not in app.reason


def test_malformed_entry_does_not_abort_scan(desktop, tmp_path):
    desktop()
    (tmp_path / "broken.desktop").write_text("not a keyfile")
    apps, errors = scan_entries([tmp_path], {"GNOME"})
    assert len(apps) == 1
    assert len(errors) == 1


def test_scan_never_executes_launcher(desktop, tmp_path):
    sentinel = tmp_path / "must-not-exist"
    desktop(Exec=f"sh -c 'touch {sentinel}'")
    scan_entries([tmp_path], {"GNOME"})
    assert not sentinel.exists()


def test_missing_tryexec_does_not_fall_through_to_lower_priority(desktop, tmp_path):
    desktop(root=tmp_path / "first", TryExec="housekeeper-not-installed")
    desktop(root=tmp_path / "second")
    apps, _ = scan_entries([tmp_path / "first", tmp_path / "second"], {"GNOME"})
    assert len(apps) == 1 and not apps[0].visible
