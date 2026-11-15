import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QSettings, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from . import __version__, logs
from .app import TamisApp
from .constants import APP_NAME
from .migration import migrate_from_cullspeed
from .theme import apply_theme

# Set to a folder, the app opens it with settings and caches of its own (the user's are left alone),
# prints the time its first preview is shown and quits: what scripts/startup_time.py measures.
STARTUP_TIME_VARIABLE = "TAMIS_STARTUP_TIME"


def main():
    timed_folder = os.environ.get(STARTUP_TIME_VARIABLE)
    # Warnings, errors and native crashes (Qt, LibRaw, with a Python traceback) go to a log file.
    logs.setup(logs.default_dir(APP_NAME))

    app = QApplication(sys.argv)
    app.setOrganizationName(APP_NAME)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "resources" / "icon.svg")))
    apply_theme(app)

    if timed_folder:
        window = _timed_window(app, timed_folder)
    else:
        migrate_from_cullspeed()
        window = TamisApp()
    if window.plugins.active:
        # A plugin's own widgets and timers run outside the plugin manager's guard, and
        # PyQt aborts on an uncaught exception in a slot unless a hook is installed.
        sys.excepthook = logs.log_exception
    app.aboutToQuit.connect(window.loader.shutdown)
    app.aboutToQuit.connect(window.editor.shutdown)
    window.show()
    return app.exec()


def _timed_window(app, folder):
    # One folder, emptied at each run: the caches start cold, and only the last run's is left behind.
    scratch = os.path.join(tempfile.gettempdir(), "tamis-startup")
    shutil.rmtree(scratch, ignore_errors=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, os.path.join(scratch, "settings"))
    QSettings().setValue("last_folder", os.path.abspath(folder))
    window = TamisApp(
        plugins_dir=os.path.join(scratch, "plugins"),
        cache_dir=os.path.join(scratch, "cache"),
        presets_file=os.path.join(scratch, "presets.json"),
    )

    def shown(path):
        if window._is_shown(path):
            print(f"first preview at {time.time():.3f}", flush=True)
            QTimer.singleShot(0, app.quit)

    window.loader.preview_loaded.connect(shown)
    return window
