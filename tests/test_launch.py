import os
from pathlib import Path

import pytest

from housekeeper.discovery import application_roots, read_entry, scan_entries
from housekeeper.identity import classify
from housekeeper.launch import effective_environment, parse_launch, resolve_executable
from housekeeper.models import AttributionState


@pytest.mark.parametrize(
    "argv,expected",
    [
        (("env", "-u", "FOO", "BAR=new", "tool"), {"BAR": "new", "PATH": "/bin"}),
        (("env", "--unset=FOO", "--", "tool"), {"BAR": "old", "PATH": "/bin"}),
        (("env", "-i", "FOO=new", "tool"), {"FOO": "new"}),
        (("env", "--ignore-environment", "--", "tool"), {}),
        (("env", "-u", "FOO", "FOO=new", "tool"), {"FOO": "new", "BAR": "old", "PATH": "/bin"}),
    ],
)
def test_environment_is_applied_without_mutating_process(argv, expected):
    environment = {"FOO": "old", "BAR": "old", "PATH": "/bin"}
    parsed = parse_launch(argv)
    assert parsed.argv == ("tool",) and not parsed.reason
    assert effective_environment(parsed, environment) == expected
    assert environment["FOO"] == "old"


def test_path_override_and_unset_are_used_for_resolution(desktop, tmp_path, monkeypatch):
    original, changed = tmp_path / "original", tmp_path / "changed"
    for root in (original, changed):
        root.mkdir()
        (root / "tool").write_text("#!/bin/sh\nexit 1\n")
        (root / "tool").chmod(0o755)
    monkeypatch.setenv("PATH", str(original))
    path = desktop(Exec=f"/usr/bin/env PATH={changed} tool")
    entry = read_entry(path, path.parent)
    assert entry.executable == str(changed / "tool")
    assert os.environ["PATH"] == str(original)
    assert resolve_executable(parse_launch(("env", "-u", "PATH", "tool"))) == ""


@pytest.mark.parametrize(
    "argv",
    [
        ("env", "-S", "tool --argument"),
        ("env", "-u"),
        ("env", "--unset="),
        ("env", "--unset", "BAD=NAME", "tool"),
        ("env", "FOO=1"),
        ("env", "HOME=/other", "flatpak", "run", "org.example.App"),
        ("env", "--unset=XDG_DATA_HOME", "flatpak", "run", "org.example.App"),
        ("env", "PATH=relative", "tool"),
        ("sh", "-c", "exec tool"),
    ],
)
def test_unsupported_launch_has_a_reason_and_no_management(entry, argv):
    spec = parse_launch(argv)
    assert spec.reason
    app = classify(entry(argv))
    assert app.attribution.state == AttributionState.UNSUPPORTED


def test_quoted_arguments_remain_literal_and_never_execute(desktop, tmp_path):
    marker = tmp_path / "must-not-exist"
    path = desktop(Exec=f'/usr/bin/true "a b" "$(touch {marker})" %U')
    entry = read_entry(path, path.parent)
    assert entry.argv[1:] == ("a b", f"$(touch {marker})", "%U")
    assert not marker.exists()


def test_missing_and_empty_xdg_are_equivalent_and_relative_paths_are_ignored():
    assert application_roots({}) == application_roots({"XDG_DATA_HOME": "", "XDG_DATA_DIRS": ""})
    assert application_roots(
        {"XDG_DATA_HOME": "relative", "XDG_DATA_DIRS": "/one::relative:/one:/two"}
    ) == [Path("/one/applications"), Path("/two/applications")]


def test_hidden_or_invalid_override_is_not_resurrected_by_extra_root(desktop, tmp_path):
    first, extra = tmp_path / "first", tmp_path / "extra"
    top = desktop(root=first, Hidden="true")
    desktop(root=extra)
    entries, _ = scan_entries([first, extra])
    assert len(entries) == 1 and not entries[0].visible
    top.write_text("invalid desktop")
    entries, warnings = scan_entries([first, extra])
    assert not entries and warnings
