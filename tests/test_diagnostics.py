"""Management failures remain diagnosable when the application starts without a terminal."""

import logging

import pytest

from housekeeper.diagnostics import configure_logging, log_path


@pytest.fixture
def logger(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    value = logging.getLogger("housekeeper")
    handlers, level, propagate = value.handlers[:], value.level, value.propagate
    value.handlers = []
    yield value
    for handler in value.handlers:
        handler.close()
    value.handlers = handlers
    value.setLevel(level)
    value.propagate = propagate


def test_failures_are_persisted_and_logging_setup_is_idempotent(logger):
    configure_logging()
    configure_logging()
    assert len(logger.handlers) == 2
    logging.getLogger("housekeeper.services").warning("Fixture update failed: authorization denied")
    assert log_path().read_text().count("Fixture update failed: authorization denied") == 1
    assert log_path().parent.stat().st_mode & 0o777 == 0o700


def test_unwritable_log_directory_keeps_terminal_diagnostics(logger, monkeypatch, tmp_path, capsys):
    blocked = tmp_path / "file"
    blocked.write_text("Not a directory")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocked))
    configure_logging()
    assert len(logger.handlers) == 1
    assert "Could not open the diagnostic log" in capsys.readouterr().err
