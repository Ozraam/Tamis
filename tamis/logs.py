"""The log file: warnings and errors that would otherwise be lost, since the built app's stderr goes nowhere.

Modules log through `logging.getLogger("tamis…")`; `setup()` sends that to a rotating file, and points
`faulthandler` at it so that a native crash (Qt, LibRaw) leaves a traceback a user can attach to a report.
"""
import faulthandler
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

LOG_FILENAME = "tamis.log"
MAX_BYTES = 1_000_000
BACKUPS = 2

log = logging.getLogger("tamis")

_fault_file = None  # kept open: faulthandler writes to its descriptor when the process dies
path = None  # the log file, once set up


def default_dir(app_name):
    """~/Library/Logs/<app> on macOS, a `logs` folder in the app's data folder elsewhere."""
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Logs", app_name)
    from PyQt6.QtCore import QStandardPaths

    return os.path.join(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation), "logs")


def setup(directory):
    """Log warnings and errors to `directory`/tamis.log; returns its path, None if it can't be written."""
    global _fault_file, path
    file = os.path.join(directory, LOG_FILENAME)
    try:
        os.makedirs(directory, exist_ok=True)
        handler = RotatingFileHandler(file, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    except OSError:
        faulthandler.enable()
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.WARNING)
    if sys.stderr is not None and sys.stderr.isatty():  # from a terminal, see them there too
        log.addHandler(logging.StreamHandler())
    try:
        _fault_file = open(file, "a", encoding="utf-8")  # noqa: SIM115 - must stay open
        faulthandler.enable(_fault_file)
    except OSError:
        faulthandler.enable()
    path = file
    return path


def log_exception(exc_type, exc, tb):
    """An excepthook: an uncaught exception goes to the log (and stderr) instead of aborting."""
    log.error("Uncaught exception", exc_info=(exc_type, exc, tb))
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in log.handlers):
        sys.__excepthook__(exc_type, exc, tb)
