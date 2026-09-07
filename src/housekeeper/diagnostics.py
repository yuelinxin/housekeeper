"""Bounded local diagnostics for failures reported by management providers."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def log_path():
    state = Path(os.environ.get("XDG_STATE_HOME", ""))
    if not state.is_absolute():
        state = Path.home() / ".local/state"
    return state / "housekeeper/housekeeper.log"


def configure_logging():
    logger = logging.getLogger("housekeeper")
    logger.setLevel(logging.DEBUG if os.environ.get("HOUSEKEEPER_DEBUG") else logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)
    try:
        path = log_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except OSError as error:
        logger.warning("Could not open the diagnostic log: %s", error)
