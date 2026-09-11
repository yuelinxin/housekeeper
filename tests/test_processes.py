"""Only path comparison; a denied or exited process never becomes a silent negative."""

import pytest

from housekeeper import processes


@pytest.fixture
def procfs(monkeypatch, tmp_path):
    def add(pid, executable="", mapped=(), *, readable=True):
        directory = tmp_path / str(pid)
        directory.mkdir()
        # procfs denies both entries for another user's process, and drops them on exit.
        # Root would bypass a permission mode, so absence is the uid-independent fixture.
        if not readable:
            return
        if executable:
            (directory / "exe").symlink_to(executable)
        lines = [f"55f0f2a00000-55f0f2a01000 r-xp 00000000 fd:00 131 {path}\n" for path in mapped]
        # An anonymous mapping and a bare heap line must not be read as file paths.
        lines.append("7ffd0b1fe000-7ffd0b21f000 rw-p 00000000 00:00 0 [stack]\n")
        lines.append("7ffd0b2a0000-7ffd0b2a1000 rw-p 00000000 00:00 0\n")
        (directory / "maps").write_text("".join(lines))

    monkeypatch.setattr(processes, "PROC", tmp_path)
    return add


def test_executables_and_mapped_files_are_separated(procfs):
    procfs(101, "/usr/bin/example", ["/usr/bin/example", "/usr/lib/libexample.so.1"])
    running, in_use = processes.affected(
        {"/usr/bin/example", "/usr/lib/libexample.so.1", "/usr/share/example/icon.png"}
    )
    assert running == ("/usr/bin/example",)
    # The executable is reported once, as an executable, never also as another in-use file.
    assert in_use == ("/usr/lib/libexample.so.1",)


def test_replaced_binaries_still_running_are_matched_by_path(procfs, tmp_path):
    (tmp_path / "202").mkdir()
    (tmp_path / "202/exe").symlink_to("/usr/bin/example (deleted)")
    (tmp_path / "202/maps").write_text(
        "55f0f2a00000-55f0f2a01000 r-xp 00000000 fd:00 131 /usr/lib/libexample.so.1 (deleted)\n"
    )
    running, in_use = processes.affected({"/usr/bin/example", "/usr/lib/libexample.so.1"})
    assert running == ("/usr/bin/example",) and in_use == ("/usr/lib/libexample.so.1",)


def test_unreadable_processes_are_skipped_without_failing(procfs):
    procfs(303, "/usr/bin/example", ["/usr/lib/libexample.so.1"], readable=False)
    procfs(404, "/usr/bin/other")
    assert processes.affected({"/usr/bin/example", "/usr/lib/libexample.so.1"}) == ((), ())
    assert processes.affected({"/usr/bin/other"}) == (("/usr/bin/other",), ())


def test_unrelated_paths_and_an_empty_transaction_report_nothing(procfs):
    procfs(505, "/usr/bin/example", ["/usr/lib/libexample.so.1"])
    assert processes.affected(set()) == ((), ())
    assert processes.affected({"/usr/bin/absent"}) == ((), ())


def test_mapped_only_scan_skips_process_maps(procfs):
    procfs(606, "/usr/bin/example", ["/usr/lib/libexample.so.1"])
    assert processes.affected({"/usr/lib/libexample.so.1"}, mapped=False) == ((), ())


def test_process_count_counts_each_process_once(procfs):
    procfs(707, "/usr/bin/example", ["/usr/lib/libexample.so.1"])
    procfs(808, "/usr/bin/other", ["/usr/lib/libexample.so.1"])
    files = processes.running_files()
    assert processes.process_count(["/usr/bin/example", "/usr/lib/libexample.so.1"], files) == 2
    assert processes.process_count(["/usr/lib/libexample.so.1"], files) == 2
    assert processes.process_count(["/usr/bin/absent"], files) == 0


def test_a_missing_procfs_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(processes, "PROC", tmp_path / "absent")
    assert processes.affected({"/usr/bin/example"}) == ((), ())
