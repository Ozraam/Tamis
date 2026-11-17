import math
import os
import platform
import sys
import time
from html import escape

import rawpy
from PyQt6.QtCore import (
    PYQT_VERSION_STR,
    QT_VERSION_STR,
    QFileSystemWatcher,
    QItemSelectionModel,
    QPointF,
    QSettings,
    QStandardPaths,
    Qt,
    QTimer,
    QUrl,
)
from PyQt6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__, bursts, develop, exportsettings, faces, logs, metadata, shortcuts, updates, xmp
from .advancedui import AdvancedEditingMixin
from .analysis import PhotoAnalyzer
from .constants import (
    APP_NAME,
    CULL_TAB,
    FALLBACK_SESSIONS_DIR,
    GALLERY_TAB,
    KEEP,
    PLUGINS_DIR,
    PRESETS_FILENAME,
    REJECT,
    UNDO_LIMIT,
)
from .editing import EditRenderer, EditThumbnails, read_hidden_mark
from .editpanel import EditPanel
from .editui import EditingMixin
from .exif import summary
from .folders import FolderMixin
from .loader import ImageLoader
from .models import SORT_KEYS, PhotoModel, StatusFilterProxy, relative_key
from .navigation import NavigationMixin
from .peaking import FocusPeaker
from .plugins import PluginManager
from .presets import PresetStore
from .processing import ProcessingMixin
from .scanning import FolderScanner
from .shortcutsdialog import ShortcutsDialog, ShortcutsEditor
from .thumbcache import ThumbnailCache
from .tones import ToneReader
from .views import CompareView, ImageView
from .widgets import (
    AboutDialog,
    GalleryToolbar,
    HeaderBar,
    KeepsListDialog,
    MetadataDialog,
    StatsBar,
    ThumbnailDelegate,
    Toast,
    inform,
    make_thumbnail_view,
)

FILMSTRIP_THUMB_SIZE = 64

# How keep and reject reach other editors, as no flag is common to them (see xmp.py).
XMP_MARKS = (
    ("off", "Not at all"),
    ("rating", "Rejected as rating −1 (darktable, Bridge)"),
    ("labels", "Keep green, reject red (Lightroom, Capture One…)"),
)
MARK_LABELS = {KEEP: "Green", REJECT: "Red"}


class TamisApp(FolderMixin, NavigationMixin, EditingMixin, AdvancedEditingMixin, ProcessingMixin, QMainWindow):
    def __init__(self, plugins_dir=None, cache_dir=None, presets_file=None, check_updates=True):
        super().__init__()
        self.setWindowTitle("Tamis")
        self.setMinimumSize(900, 600)
        self.setAcceptDrops(True)

        self.settings = QSettings()
        # Where marks go when a photo folder is read-only.
        data_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        self.session_fallback_dir = os.path.join(data_dir, FALLBACK_SESSIONS_DIR)
        self.session_warning = None  # last session problem shown, so it is shown once
        self.xmp_warned = False
        self.xmp_dirty = set()  # photos whose rating/label still has to be written to XMP
        self.xmp_metadata_dirty = set()  # those of them whose title, caption or keywords changed too
        self.moved_keys = set()  # session keys of moved photos, until a save has dropped them
        self.damaged_sessions = set()  # session files the user was told about
        self.folder = ""
        self.current_row = -1
        self.compare_rows = []  # rows shown side by side; empty outside Compare
        self.live_edit = None  # (path, edit) while a slider is being dragged, before it is kept
        self.edit_clipboard = None
        self.cropping = False  # the crop tool is open on the current photo
        self.crop_cache = {}  # (pixmap key, framing) -> framed pixmap, for what the renderer doesn't frame
        self.before = False  # the photo is being shown without its edit
        self.show_clipping = False
        self.presets = PresetStore(presets_file or os.path.join(data_dir, PRESETS_FILENAME))
        self.keeps_only = False  # the Cull view only shows and steps through keeps
        self.lut_folder = self.settings.value("lut_folder", "")
        self.luts = {}  # LUT id -> path, from lut_folder
        self.show_info = self.settings.value("show_info", True, type=bool)
        self.undo_stack = []
        self.moving = False
        self.sort_order = self.settings.value("sort_order", "name")
        if self.sort_order not in SORT_KEYS:
            self.sort_order = "name"
        self.show_bursts = self.settings.value("show_bursts", False, type=bool)
        # Closed eyes and soft faces, looked for in the background (macOS, with Vision) when asked.
        self.detect_faces = faces.available() and self.settings.value("detect_faces", False, type=bool)
        self.face_zoom_pending = False  # Shift+Z waits for the faces of the photos shown
        self.peaking = False
        self.xmp_marks = self.settings.value("xmp_marks", "off")
        if self.xmp_marks not in dict(XMP_MARKS):
            self.xmp_marks = "off"
        self.export_settings = exportsettings.load_settings(self.settings.value("export_settings", ""))
        self.export_profiles = exportsettings.load_profiles(self.settings.value("export_profiles", ""))
        # What Process Files exports: "all" but rejects (the default, as before there was a choice) or "keeps".
        self.export_scope = self.settings.value("export_scope", "all")
        if self.export_scope not in dict(exportsettings.SCOPES):
            self.export_scope = "all"

        cache_dir = cache_dir or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
        self.cache_dir = cache_dir
        thumbnail_cache = ThumbnailCache(os.path.join(cache_dir, "thumbnails"))
        self.loader = ImageLoader(self, thumbnail_cache)
        self.loader.preview_loaded.connect(self._on_image_ready)
        self.loader.preview_failed.connect(lambda path, _error: self._on_image_ready(path))
        self.loader.full_loaded.connect(self._on_image_ready)
        self.loader.full_failed.connect(self._on_full_failed)

        self.editor = EditRenderer(self)
        self.editor.rendered.connect(self._on_image_ready)
        self.editor.failed.connect(self._on_edit_failed)
        self.edit_thumbnails = EditThumbnails(self.editor, thumbnail_cache, self)

        self.peaker = FocusPeaker(self)
        self.peaker.ready.connect(self._on_peaking_ready)

        self.tones = ToneReader(self)
        self.tones.ready.connect(self._refresh_view)

        self.analyzer = PhotoAnalyzer(os.path.join(cache_dir, "analysis"), self)
        self.analyzer.analyzed.connect(self._on_analyzed)
        self.analyzer.finished.connect(self._on_analysis_finished)
        # What the analysis finds shows up a few times a second, not once per photo.
        self.analysis_timer = QTimer(self)
        self.analysis_timer.setSingleShot(True)
        self.analysis_timer.setInterval(500)
        self.analysis_timer.timeout.connect(self._show_analysis)

        self.model = PhotoModel(self.loader, self)
        self.model.set_edit_thumbnails(self.edit_thumbnails, self._lut_path)
        self.model.statuses_changed.connect(self._on_statuses_changed)
        self.model.ratings_changed.connect(lambda paths: self.xmp_dirty.update(paths))
        self.model.metadata_changed.connect(lambda paths: self.xmp_metadata_dirty.update(paths))
        self.model.marks_changed.connect(self._on_marks_changed)
        self.proxy = StatusFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.strip_proxy = StatusFilterProxy(self)  # the filmstrip's own: filtered by "Keeps only"
        self.strip_proxy.setSourceModel(self.model)

        self.session_timer = QTimer(self)
        self.session_timer.setSingleShot(True)
        self.session_timer.setInterval(400)
        self.session_timer.timeout.connect(self.save_session)

        self.scanner = FolderScanner(self)
        self.scanner.scanned.connect(self._on_scanned)
        self.scanner.progressed.connect(self._on_scan_progress)

        # New files (an import or a tethered shot landing in the folder) show up by themselves.
        self.watcher = QFileSystemWatcher(self)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(lambda: self.refresh(auto=True))
        self.watcher.directoryChanged.connect(self._on_directory_changed)
        self.saved_at = -math.inf  # time.monotonic() of the last session written in the folder
        self.scan_started = -math.inf

        self._build_ui()
        self._init_advanced(cache_dir)
        self.analyzer.progressed.connect(self.stats.set_analysis_progress)
        self.shortcut_keys = shortcuts.load(self.settings)
        self._build_shortcuts()
        self._load_luts(quiet=True)
        self._restore_window()
        self.plugins = PluginManager(self, plugins_dir or os.path.join(data_dir, PLUGINS_DIR))
        self.plugins_button.setMenu(self.plugins.menu)
        self.plugins.load()
        QTimer.singleShot(0, self._restore_last_folder)
        self.updates = updates.UpdateChecker(__version__, self)
        self.updates.checked.connect(self._on_update_checked)
        self.available_update = None  # an updates.Release newer than this version, once known
        self.about_dialog = None  # while About is open, to show what a check finds
        self.update_check_manual = False
        # Off in the tests, which never ask GitHub (and a few seconds late: the photos come first).
        if check_updates and self._update_check_due():
            QTimer.singleShot(3000, self.check_for_updates)
        if self.presets.problem:
            QTimer.singleShot(0, lambda: self.toast.show_message(self.presets.problem, 6000))

    # -- UI ----------------------------------------------------------------

    def _build_ui(self):
        self.header = HeaderBar()
        self.header.open_folder_requested.connect(self.choose_folder)
        self.header.import_requested.connect(self.import_photos)
        self.header.trash_requested.connect(self.trash_rejects)
        self.header.refresh_requested.connect(self.refresh)
        self.header.process_requested.connect(self.process_files)

        # Cull tab: large image (or Compare) + filmstrip
        self.image_view = ImageView()
        self.image_view.zoom_changed.connect(self._on_zoom_changed)
        self.image_view.crop_confirmed.connect(self.toggle_crop)
        self.image_view.set_image(None, "Open a folder of photos to start\n(or drop one onto this window)")
        self.compare_view = CompareView()
        self.compare_view.zoom_changed.connect(self._on_zoom_changed)
        self.compare_view.activated.connect(lambda index: self.go_to(self.compare_rows[index]))
        self.cull_stack = QStackedWidget()
        self.cull_stack.addWidget(self.image_view)
        self.cull_stack.addWidget(self.compare_view)

        self.filmstrip = make_thumbnail_view(
            self.strip_proxy, ThumbnailDelegate(FILMSTRIP_THUMB_SIZE, show_names=False, parent=self), horizontal=True
        )
        self.filmstrip.setObjectName("Filmstrip")
        self.filmstrip.setFixedHeight(FILMSTRIP_THUMB_SIZE + ThumbnailDelegate.PADDING * 2 + 16)
        self.filmstrip.clicked.connect(lambda index: self.go_to(self.strip_proxy.mapToSource(index).row()))

        self.edit_panel = EditPanel()
        self.edit_panel.changed.connect(self._on_edit_changed)
        self.edit_panel.copy_requested.connect(self.copy_edit)
        self.edit_panel.paste_requested.connect(self.paste_edit)
        self.edit_panel.reset_requested.connect(self.reset_edit)
        self.edit_panel.export_requested.connect(self.export_photos)
        self.edit_panel.lut_folder_requested.connect(self.choose_lut_folder)
        self.edit_panel.crop_requested.connect(self.toggle_crop)
        self.edit_panel.crop_cleared.connect(self.clear_crop)
        self.edit_panel.crop_ratio_changed.connect(self._on_crop_ratio_changed)
        self.edit_panel.rotate_requested.connect(self.rotate_photos)
        self.edit_panel.clipping_toggled.connect(self.set_clipping)
        self.edit_panel.before_held.connect(self._set_before)
        self.edit_panel.preset_chosen.connect(self.apply_preset)
        self.edit_panel.preset_save_requested.connect(self.save_preset)
        self.edit_panel.preset_delete_requested.connect(self.delete_preset)
        self.edit_panel.process_update_requested.connect(self.update_process)
        self.edit_panel.set_presets(self.presets.names())
        self.edit_panel.setVisible(self.settings.value("edit_panel", False, type=bool))

        viewer = QHBoxLayout()
        viewer.setContentsMargins(0, 0, 0, 0)
        viewer.setSpacing(0)
        viewer.addWidget(self.cull_stack, 1)
        viewer.addWidget(self.edit_panel)

        cull_page = QWidget()
        cull_layout = QVBoxLayout(cull_page)
        cull_layout.setContentsMargins(0, 0, 0, 0)
        cull_layout.setSpacing(0)
        cull_layout.addLayout(viewer, 1)
        cull_layout.addWidget(self.filmstrip)

        # Gallery tab: filter chips + grid
        grid_size = int(self.settings.value("grid_thumb_size", 150))
        self.gallery_toolbar = GalleryToolbar(grid_size, self.sort_order, self.show_bursts)
        self.gallery_toolbar.filter_changed.connect(self.proxy.set_filter)
        self.gallery_toolbar.rating_changed.connect(self.proxy.set_min_rating)
        self.gallery_toolbar.labels_changed.connect(self.proxy.set_labels)
        self.gallery_toolbar.focus_changed.connect(self._on_focus_filter_changed)
        self.gallery_toolbar.sort_changed.connect(self.set_sort_order)
        self.gallery_toolbar.bursts_toggled.connect(self.set_bursts)
        self.gallery_toolbar.size_changed.connect(self._on_grid_size_changed)
        self.gallery_toolbar.set_faces(self.detect_faces)
        self.grid_delegate = ThumbnailDelegate(grid_size, show_names=True, parent=self)
        self.grid = make_thumbnail_view(self.proxy, self.grid_delegate, horizontal=False)
        self.grid.selectionModel().currentChanged.connect(self._on_grid_current_changed)
        self.grid.activated.connect(self._on_grid_activated)  # double-click / Enter

        gallery_page = QWidget()
        gallery_layout = QVBoxLayout(gallery_page)
        gallery_layout.setContentsMargins(0, 0, 0, 0)
        gallery_layout.setSpacing(0)
        gallery_layout.addWidget(self.gallery_toolbar)
        gallery_layout.addWidget(self.grid, 1)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabs.addTab(cull_page, "Cull")
        self.tabs.addTab(gallery_page, "Gallery")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # In the tab bar's corner, so it costs the photo no height.
        self.keeps_button = QPushButton("Keeps only")
        self.keeps_button.setObjectName("Chip")
        self.keeps_button.setCheckable(True)
        self.keeps_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.keeps_button.setToolTip("Only show and step through the photos you keep (K)")
        self.keeps_button.clicked.connect(lambda _checked: self.toggle_keeps_only())
        self.keeps_button.setFixedHeight(24)  # tall enough for the chip's rounded ends, which Qt drops otherwise
        self.plugins_button = QPushButton("Plugins ▾")
        self.plugins_button.setObjectName("Chip")
        self.plugins_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.plugins_button.setFixedHeight(24)
        self.options_button = QPushButton("Options ▾")
        self.options_button.setObjectName("Chip")
        self.options_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.options_button.setFixedHeight(24)
        self.options_button.setMenu(self._build_options_menu())
        corner = QWidget()
        corner.setObjectName("TabCorner")
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 0, 10, 0)
        corner_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        corner_layout.addWidget(self.keeps_button)
        corner_layout.addWidget(self.options_button)
        corner_layout.addWidget(self.plugins_button)
        self.tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
        self.tabs.tabBar().setDrawBase(False)

        self.stats = StatsBar()

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.stats)
        self.setCentralWidget(central)

        self.toast = Toast(central)

    def _build_options_menu(self):
        menu = QMenu(self)
        marks = menu.addMenu("Show Keep and Reject in Other Editors")
        marks.setToolTipsVisible(True)
        group = QActionGroup(self)
        self.xmp_mark_actions = {}
        for value, title in XMP_MARKS:
            action = QAction(title, self, checkable=True, checked=value == self.xmp_marks)
            action.triggered.connect(lambda _checked, value=value: self.set_xmp_marks(value))
            group.addAction(action)
            marks.addAction(action)
            self.xmp_mark_actions[value] = action
        if faces.available():
            self.faces_action = QAction("Look for Closed Eyes and Soft Faces", self, checkable=True, checked=self.detect_faces)
            self.faces_action.toggled.connect(self.set_detect_faces)
            menu.addAction(self.faces_action)
        menu.addAction("Export Settings…", self.edit_export_settings)
        menu.addAction("List of Keeps for Other Editors…", self.show_keeps_list)
        menu.addAction("Read a Hidden Mark…", self.read_hidden_mark)
        menu.addAction("Keyboard Shortcuts…", self.edit_shortcuts)
        menu.addSeparator()
        self.update_check_action = QAction(
            "Check for Updates Automatically", self, checkable=True, checked=self.settings.value("check_updates", True, type=bool)
        )
        self.update_check_action.toggled.connect(lambda on: self.settings.setValue("check_updates", on))
        menu.addAction(self.update_check_action)
        menu.addAction("Show Log File", self.show_log_file)
        menu.addAction(f"About {APP_NAME}…", self.show_about)
        return menu

    def show_keeps_list(self):
        """Copy or save the names of the keeps, or of what the Gallery shows, for another editor."""
        if not self.folder:
            return
        keeps = [photo for photo in self.model.photos if photo.status == KEEP]
        shown = [self.model.photo(self.proxy.mapToSource(self.proxy.index(row, 0)).row()) for row in range(self.proxy.rowCount())]
        choices = [("keeps", "Keeps", keeps), ("shown", "Photos the Gallery shows", shown)]
        dialog = KeepsListDialog(self, choices, self.folder)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if accepted:
            self.toast.show_message(f"Copied the names of {len(dialog.which[1])} photo(s)")
        elif dialog.saved:
            self.toast.show_message(f"Saved {os.path.basename(dialog.saved)}")

    def read_hidden_mark(self, path=None):
        """Say what hidden mark (see the export settings) an image carries."""
        if path is None:
            start = self.export_settings.dest_dir(self.folder) if self.folder else os.path.expanduser("~")
            path, _filter = QFileDialog.getOpenFileName(self, "Read a hidden mark", start)
            if not path:
                return
        text = read_hidden_mark(path)
        name = escape(os.path.basename(path))
        if text is None:
            inform(self, "No hidden mark", f"No hidden mark was found in {name}. A mark is lost when a picture "
                   "is resized, cropped or rotated, or saved again at a low JPEG quality.")
        else:
            inform(self, "Hidden mark", f"{name} carries: <b>{escape(text)}</b>")

    def show_log_file(self):
        """Where warnings, errors and crashes are written, to attach to a bug report."""
        if logs.path is None or not os.path.exists(logs.path):
            inform(self, "No log file", "Nothing has been logged since launch, or the log folder can't be written.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(logs.path))

    def show_about(self):
        """The app's version, and those of what it is built on, for bug reports, and whether a newer one is out."""
        libraw = ".".join(str(part) for part in rawpy.libraw_version)
        text = (
            f"Version <b>{escape(__version__)}</b><br><br>"
            f"Python {platform.python_version()} · Qt {QT_VERSION_STR} · PyQt {PYQT_VERSION_STR} · LibRaw {libraw}<br>"
            f"{escape(platform.platform(terse=True))}"
        )
        update = self._update_text(self.available_update) if self.available_update else ""
        check = self._check_now if self.updates.can_check() else None
        self.about_dialog = AboutDialog(self, APP_NAME, text, update, check)
        try:
            self.about_dialog.exec()
        finally:
            self.about_dialog = None

    # -- updates -----------------------------------------------------------

    def _update_check_due(self):
        if not self.settings.value("check_updates", True, type=bool) or not self.updates.can_check():
            return False
        last = self.settings.value("update_checked_at", 0.0, type=float)
        return not 0 <= time.time() - last < updates.CHECK_INTERVAL

    def check_for_updates(self, manual=False):
        """Ask GitHub whether a newer version is out; automatic checks keep quiet about failures."""
        self.update_check_manual = self.update_check_manual or manual
        self.updates.check()

    def _check_now(self):
        self.check_for_updates(manual=True)

    @staticmethod
    def _update_text(release):
        how = "Download it" if getattr(sys, "frozen", False) else "Download it, or update your copy (git pull)"
        return (
            f"<b>{APP_NAME} {escape(release.version)} is available.</b> "
            f'{how} from <a href="{escape(release.url)}">its release page</a>.'
        )

    def _on_update_checked(self, result):
        manual, self.update_check_manual = self.update_check_manual, False
        if result.error is None:
            self.settings.setValue("update_checked_at", time.time())
        self.available_update = result.newer
        if self.about_dialog is not None:
            if result.newer:
                self.about_dialog.set_update(self._update_text(result.newer))
            elif result.error:
                self.about_dialog.set_update(escape(result.error))
            else:
                self.about_dialog.set_update(f"{APP_NAME} {escape(__version__)} is the latest version.")
            return
        if result.newer and (manual or self.settings.value("update_dismissed", "") != result.newer.version):
            # Shown once per version: About keeps saying it.
            self.settings.setValue("update_dismissed", result.newer.version)
            url = result.newer.url
            self.toast.show_message(
                f"{APP_NAME} {result.newer.version} is available · click to see it",
                8000,
                on_click=lambda: QDesktopServices.openUrl(QUrl(url)),
            )
        elif manual:
            self.toast.show_message(result.error or f"{APP_NAME} {__version__} is the latest version", 4000)

    def _shortcut_slots(self):
        """What each command of `shortcuts.COMMANDS` does."""
        slots = {
            "keep": lambda: self.mark(KEEP),
            "reject": lambda: self.mark(REJECT),
            "unmark": lambda: self.mark(None),
            "keep_cull": lambda: self.mark(KEEP),
            "reject_cull": lambda: self.mark(REJECT),
            "keep_one_of_burst": self.keep_one_of_burst,
            "undo": self.undo,
            "next": lambda: self.step(1),
            "previous": lambda: self.step(-1),
            "first": lambda: self.go_to_end(first=True),
            "last": lambda: self.go_to_end(first=False),
            "next_unmarked": self.next_unmarked,
            "toggle_tab": self.toggle_tab,
            "keeps_only": self.toggle_keeps_only,
            "zoom": self.toggle_zoom,
            "zoom_face": self.zoom_to_faces,
            "escape": self.escape,
            "compare": self.toggle_compare,
            "info": self.toggle_info,
            "bursts": lambda: self.set_bursts(not self.show_bursts),
            "peaking": self.toggle_peaking,
            "clipping": lambda: self.set_clipping(not self.show_clipping),
            "before": self._hold_before,
            "shortcuts": self.show_shortcuts,
            "edit_panel": self.toggle_edit_panel,
            "crop": self.toggle_crop,
            "confirm_crop": self._confirm_crop,
            "rotate_right": lambda: self.rotate_photos(90),
            "rotate_left": lambda: self.rotate_photos(-90),
            "copy_edit": self.copy_edit,
            "paste_edit": self.paste_edit,
            "metadata": self.edit_metadata,
            "open": self.choose_folder,
            "import": self.import_photos,
            "refresh": self.refresh,
            "export": self.export_photos,
            "spots": lambda: self.set_edit_tool("" if self.edit_tool == "spots" else "spots"),
            "virtual_copy": self.create_virtual_copy,
            "remove_copy": self.remove_virtual_copies,
        }
        for stars in range(6):
            slots[f"rate_{stars}"] = lambda stars=stars: self.rate(stars)
        for label in xmp.LABELS:
            slots[f"label_{label.lower()}"] = lambda label=label: self.set_label(label)
        return slots

    def _build_shortcuts(self):
        """Bind the keys of `self.shortcut_keys`, replacing any bound before.

        Must run before plugins load, so that the plugins' shortcuts that are taken are refused.
        """
        for shortcut in getattr(self, "app_shortcuts", ()):
            shortcut.setParent(None)  # at once: plugins look for the taken keys among the children
            shortcut.deleteLater()
        self.app_shortcuts = []
        self.cull_shortcuts = []
        slots = self._shortcut_slots()
        in_cull = self.tabs.currentIndex() == CULL_TAB
        for command in shortcuts.COMMANDS:
            for key in self.shortcut_keys[command.id]:
                shortcut = QShortcut(QKeySequence(key), self)
                shortcut.setAutoRepeat(command.repeat)
                shortcut.activated.connect(slots[command.id])
                self.app_shortcuts.append(shortcut)
                if command.cull_only:  # in the gallery the arrow keys move through the grid instead
                    shortcut.setEnabled(in_cull)
                    self.cull_shortcuts.append(shortcut)
        self.stats.set_hint(shortcuts.hint(self.shortcut_keys))

    def _plugin_shortcuts(self):
        """{key: "Plugins > title"} for the plugins' actions that have a shortcut."""
        return {
            action.shortcut().toString(): f"{plugin.name} > {action.text()}"
            for plugin in self.plugins.plugins
            for action in plugin.actions
            if not action.shortcut().isEmpty()
        }

    def show_shortcuts(self):
        """The overview of every shortcut, from which they can be changed."""
        dialog = ShortcutsDialog(self, self.shortcut_keys, self._plugin_shortcuts())
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.customize:
            self.edit_shortcuts()

    def edit_shortcuts(self):
        """Change the keys; returns whether they were changed."""
        dialog = ShortcutsEditor(self, self.shortcut_keys, self._plugin_shortcuts())
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.bindings == self.shortcut_keys:
            return False
        self.shortcut_keys = dialog.bindings
        shortcuts.save(self.settings, self.shortcut_keys)
        self._build_shortcuts()
        self.toast.show_message("Keyboard shortcuts saved")
        return True

    # -- showing photos ----------------------------------------------------

    def _shown_rows(self):
        if self.compare_rows:
            return list(self.compare_rows)
        return [self.current_row] if self.current_row >= 0 else []

    def _zoomed(self):
        return self.compare_view.zoomed if self.compare_rows else self.image_view.zoomed

    def _refresh_view(self):
        """Show the current photo, or the compared ones, with their marks, info and zoom."""
        if self.current_row < 0:
            return
        if self.compare_rows:
            shown = list(zip(self.compare_view.views, self.compare_rows, strict=True))
        else:
            shown = [(self.image_view, self.current_row)]
        zoomed = self._zoomed()
        self.peaker.begin()
        self.tones.begin()
        # Previews are downscaled, so while zoomed the photos load at full resolution.
        self.loader.ensure_full([self.model.photo(row).path for _, row in shown], load=zoomed)
        self.editor.keep_requests({self.model.photo(row).path for _, row in shown})
        self.editor.keep_full_requests({self.model.photo(row).path for _, row in shown} if zoomed else set())
        for view, row in shown:
            self._show_in(view, row, zoomed)

    def _show_in(self, view, row, zoomed):
        photo = self.model.photo(row)
        # The crop tool shows the whole photo. Otherwise what the renderer didn't
        # make is framed here, so a photo keeps its frame whatever is shown of it.
        framing = develop.framing(self._shown_edit(photo))
        if self.cropping:
            framing.pop("crop", None)
        pixmap = self._edited_pixmap(photo)
        if pixmap is None:
            pixmap = self._framed(self.loader.preview(photo.path), framing)
        if pixmap is not None:
            view.set_image(pixmap, native_size=self._framed_size(self.loader.native_size(photo.path), framing), keep_zoom=True)
        else:
            error = self.loader.preview_error(photo.path)
            view.set_image(None, f"Could not load {photo.name}\n\n{error}" if error else "Loading…", keep_zoom=True)
        view.set_status(photo.status)
        view.set_rating(photo.rating, photo.label)
        view.set_info(self._info_lines(photo) if self.show_info else [])
        view.set_active(bool(self.compare_rows) and row == self.current_row)
        shown = pixmap
        if zoomed and pixmap is not None:
            # The edit itself at full resolution, rendered in the background; the original in its frame until then.
            edited, current = self._edited_full(photo)
            full = self.loader.full_pixmap(photo.path)
            if edited is not None:
                shown = edited
                view.set_full_image(shown)
            elif full is not None:
                shown = self._framed(full, framing)
                view.set_full_image(shown)
            else:
                view.set_full_loading(self.loader.full_error(photo.path) is None)
            if not current:
                view.set_full_loading(True, "Rendering the edit at full resolution…")
        peaks = self.peaking and shown is not None and not self.cropping
        view.set_overlay(self.peaker.overlay(shown) if peaks else None)
        clipping = self.show_clipping and shown is not None and not self.cropping
        view.set_clipping(self.tones.clipping(shown) if clipping else None, photo.path if clipping else None)
        view.set_note("Before" if self._showing_before(photo) else "")
        if view is self.image_view:
            self._show_mask_overlay(photo)
        if row == self.current_row and self.edit_panel.isVisible():
            if pixmap is None:
                self.edit_panel.histogram.set_tone(None)
            else:
                # Until the new one is known the last one stays up, so that a slider doesn't make it blink.
                tone = self.tones.tone(pixmap)
                if tone is not None:
                    self.edit_panel.histogram.set_tone(tone)
                    self.edit_panel.set_histogram_counts(tone.counts)

    def _info_lines(self, photo):
        lines = summary(self.loader.info(photo.path))
        if photo.metadata.get("title"):
            lines.insert(0, photo.metadata["title"])
        median = self.model.median_sharpness()
        if photo.sharpness is not None and median is not None:
            soft = " · looks soft" if self.model.is_soft(self.model.row_of(photo.path)) else ""
            lines.append(f"Sharpness {photo.sharpness:.0f} · folder median {median:.0f}{soft}")
        if photo.faces:
            issue = self.model.face_issue(self.model.row_of(photo.path))
            count = len(faces.main_faces(photo.faces))
            lines.append(f"{count} face{'s' * (count != 1)}" + (f" · {issue}" if issue else ""))
        return lines

    def _is_shown(self, path):
        """Whether the photo `path` is shown, or one on that file (the loader speaks of files, not virtual copies)."""
        return any(path in (self.model.photo(row).path, self.model.photo(row).file) for row in self._shown_rows())

    def _on_image_ready(self, path):
        if self._is_shown(path):
            self._refresh_view()

    def _on_full_failed(self, path, error):
        if self._is_shown(path):
            self._refresh_view()
            self.toast.show_message(f"Full resolution unavailable: {error}", 4000)

    def _on_zoom_changed(self, _zoomed):
        self._refresh_view()

    def zoom_to_faces(self):
        """Zoom 1:1 on each photo's own main face, at the same scale: a subject moves between the frames of a burst.

        A photo without a face (or whose faces aren't known yet) keeps the shared zoom.
        """
        if self.cropping or self.current_row < 0:
            return
        if not faces.available():
            self.toast.show_message("Finding faces needs macOS (Apple's Vision)", 3000)
            return
        photos = [self.model.photo(row) for row in self._shown_rows()]
        unknown = [photo for photo in photos if photo.faces is None]
        if unknown:
            self.face_zoom_pending = True
            self.analyzer.request(faces=unknown)
            self.toast.show_message("Looking for faces…")
            return
        self.face_zoom_pending = False
        centers = []
        for photo in photos:
            face = faces.main_face(photo.faces)
            native = self.loader.native_size(photo.path)
            size = (native.width(), native.height()) if native is not None and native.isValid() else (3, 2)
            point = faces.framed_point(*face.center, self._shown_edit(photo), size) if face else None
            centers.append(QPointF(*point) if point else None)
        if not any(centers):
            self.toast.show_message("No face found" if len(photos) == 1 else "No face found in these photos", 3000)
            return
        if self.compare_rows:
            self.compare_view.zoom_to(centers)
        else:
            self.image_view.zoom_to(centers[0])

    def set_detect_faces(self, on):
        self.detect_faces = on
        self.settings.setValue("detect_faces", on)
        self.gallery_toolbar.set_faces(on)
        self.toast.show_message("Looking for closed eyes and soft faces" if on else "Not looking for faces")
        self._request_analysis()
        self._show_analysis()

    def toggle_zoom(self):
        if self.cropping:
            return
        if self.compare_rows:
            self.compare_view.toggle_zoom(self.compare_rows.index(self.current_row))
        else:
            self.image_view.toggle_zoom()

    def escape(self):
        if self.cropping:
            self._finish_crop(apply=False)
        elif self.compare_rows and self.compare_view.zoomed:
            self.compare_view.reset_zoom()
        elif self.compare_rows:
            self._end_compare()
        else:
            self.image_view.reset_zoom()

    def toggle_peaking(self):
        self.peaking = not self.peaking
        self.toast.show_message("Focus peaking on" if self.peaking else "Focus peaking off")
        self._refresh_view()

    def _on_peaking_ready(self):
        if self.peaking:
            self._refresh_view()

    def toggle_info(self):
        self.show_info = not self.show_info
        self.settings.setValue("show_info", self.show_info)
        self._refresh_view()

    # -- marking -----------------------------------------------------------

    def _target_rows(self):
        if self.tabs.currentIndex() == GALLERY_TAB:
            rows = sorted(self.proxy.mapToSource(i).row() for i in self.grid.selectionModel().selectedIndexes())
            if rows:
                return rows
            # Never mark a photo the gallery filter is hiding.
            if not self.proxy.mapFromSource(self.model.index(self.current_row)).isValid():
                return []
        return [self.current_row] if self.current_row >= 0 else []

    def _push_undo(self, changes):
        if changes:
            self.undo_stack.append(changes)
            del self.undo_stack[:-UNDO_LIMIT]

    def mark(self, status):
        rows = self._target_rows()
        if not rows:
            return
        in_gallery = self.tabs.currentIndex() == GALLERY_TAB
        first_visible = min(self.proxy.mapFromSource(self.model.index(row)).row() for row in rows)
        marked_row = self.current_row
        self._push_undo(self.set_marks(dict.fromkeys(rows, status)))
        if in_gallery:
            self._select_after_filtering(first_visible)
        elif status is not None and not self.compare_rows and self.current_row == marked_row:
            # (With "Keeps only", a rejected photo has already moved on to the next keep.)
            following = self._cull_neighbor(self.current_row, 1)
            if following is not None:
                self.go_to(following)
            else:
                self._report_end()

    def set_marks(self, statuses):
        """Mark photos, `statuses` being {row: status}; returns the undo changes.

        With colour labels passing on the marks, the photos get theirs too, unless the user gave them another.
        """
        changes = self.model.set_values("status", statuses)
        if self.xmp_marks == "labels":
            labels = {
                row: MARK_LABELS.get(status) for row, status in statuses.items()
                if self.model.photo(row).label in (None, *MARK_LABELS.values())
            }
            changes += self.model.set_values("label", labels)
        return changes

    def set_xmp_marks(self, value):
        """How keep and reject reach other editors, for all photos from now on, starting with this folder's."""
        was_rating = self.xmp_marks == "rating"
        self.xmp_marks = value
        self.settings.setValue("xmp_marks", value)
        if value == "labels":
            marked = {row: photo.status for row, photo in enumerate(self.model.photos) if photo.status}
            self._push_undo(self.set_marks(marked))
        if value == "rating" or was_rating:
            self.xmp_dirty |= {photo.path for photo in self.model.photos if photo.status == REJECT}
        self.session_timer.start()

    def _on_marks_changed(self, paths):
        if self.xmp_marks == "rating":
            self.xmp_dirty.update(paths)

    def _first_visible(self, rows):
        return min(self.proxy.mapFromSource(self.model.index(row)).row() for row in rows)

    def _reselect_in_gallery(self, proxy_row):
        if self.tabs.currentIndex() == GALLERY_TAB:
            self._select_after_filtering(max(0, proxy_row))

    def rate(self, stars):
        rows = self._target_rows()
        if rows:
            first_visible = self._first_visible(rows)
            self._push_undo(self.model.set_field(rows, "rating", stars))
            self._reselect_in_gallery(first_visible)  # a star filter may have hidden them

    def edit_metadata(self):
        """Title, caption and keywords of the target photos, as one undo step."""
        rows = self._target_rows()
        if not rows:
            return
        dialog = MetadataDialog(self, [self.model.photo(row).metadata for row in rows])
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.changes:
            return
        values = {
            row: metadata.clean({**self.model.photo(row).metadata, **dialog.changes}) for row in rows
        }
        self._push_undo(self.model.set_values("metadata", values))
        self.toast.show_message(f"Saved for {len(rows)} photo(s)" if len(rows) > 1 else "Saved")
        if self.show_info:
            self._refresh_view()

    def set_label(self, label):
        """Give `label` to the target photos, or remove it if they all have it already."""
        rows = self._target_rows()
        if not rows:
            return
        if all(self.model.photo(row).label == label for row in rows):
            label = None
        first_visible = self._first_visible(rows)
        self._push_undo(self.model.set_field(rows, "label", label))
        self._reselect_in_gallery(first_visible)  # a label filter may have hidden them

    def _report_end(self):
        unmarked = self.model.counts()[2]
        if self.keeps_only:
            self.toast.show_message("Last keep", 3000)
        elif unmarked:
            self.toast.show_message(f"Last photo · {unmarked} still unmarked (N to jump)", 3000)
        else:
            self.toast.show_message("Last photo · every photo is marked", 3000)

    def _select_after_filtering(self, proxy_row):
        """Marked photos may drop out of a filtered gallery: select the one now in their place."""
        if self.grid.selectionModel().hasSelection() or self.proxy.rowCount() == 0:
            return
        index = self.proxy.index(min(proxy_row, self.proxy.rowCount() - 1), 0)
        self.grid.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.grid.scrollTo(index)
        self.go_to(self.proxy.mapToSource(index).row())

    def undo(self):
        if self.cropping:
            self._finish_crop(apply=False)
            return
        if not self.undo_stack:
            self.toast.show_message("Nothing to undo")
            return
        changes = self.undo_stack.pop()
        rows = self.model.restore(changes)
        shown = [row for row in rows if self._in_cull(row)]
        if shown and self.tabs.currentIndex() == CULL_TAB and self.current_row not in rows:
            self.go_to(shown[0])
        self.toast.show_message("Undone" if len(changes) == 1 else f"Undone ({len(changes)} photos)")

    def _on_statuses_changed(self, rows):
        keeps, rejects, unmarked = self.model.counts()
        self.stats.update_counts(keeps, rejects, unmarked)
        self.gallery_toolbar.update_counts(self.model.rowCount(), keeps, rejects, unmarked)
        # Even when everything is sorted, processing refreshes _EXPORTS.
        self.header.set_can_process(bool(self.folder) and self.model.rowCount() > 0)
        if self.current_row in rows and self.live_edit is None:
            self._show_edit_in_panel()  # undo, paste, reset…
        if set(rows) & set(self._shown_rows()):
            self._refresh_view()
        self._update_counter()
        self._keep_current_in_filter()
        self.session_timer.start()

    # -- gallery -----------------------------------------------------------

    def _on_tab_changed(self, index):
        self._set_before(False)
        for shortcut in self.cull_shortcuts:
            shortcut.setEnabled(index == CULL_TAB)
        self.keeps_button.setVisible(index == CULL_TAB)  # the gallery has its own filter
        if index == GALLERY_TAB:
            self._finish_crop(apply=True)
            self._end_compare(show=False)
            if self.current_row >= 0:
                proxy_index = self.proxy.mapFromSource(self.model.index(self.current_row))
                if proxy_index.isValid():
                    self.grid.selectionModel().setCurrentIndex(
                        proxy_index, QItemSelectionModel.SelectionFlag.ClearAndSelect
                    )
                    self.grid.scrollTo(proxy_index, QAbstractItemView.ScrollHint.PositionAtCenter)
            self.grid.setFocus()
        else:
            self.setFocus()
            if self.keeps_only and self.current_row >= 0 and not self._in_cull(self.current_row):
                self._set_keeps_only(False)  # this photo was picked in the gallery: show it
                self.toast.show_message("Showing all photos")

    def _on_grid_current_changed(self, proxy_index, _previous):
        # Clicks and arrow keys both move the current photo, so G opens that one.
        # Only while the gallery is shown: marking in the Cull view can also
        # move the grid's current item, when the filter hides the marked photo.
        if self.tabs.currentIndex() != GALLERY_TAB or not proxy_index.isValid():
            return
        row = self.proxy.mapToSource(proxy_index).row()
        if row != self.current_row:
            self.go_to(row)

    def _on_grid_activated(self, proxy_index):
        self.go_to(self.proxy.mapToSource(proxy_index).row())
        self.tabs.setCurrentIndex(CULL_TAB)

    def _on_grid_size_changed(self, size):
        self.grid_delegate.thumb_size = size
        self.grid.doItemsLayout()
        self.settings.setValue("grid_thumb_size", size)

    # -- sorting, analysis and bursts ---------------------------------------

    def set_sort_order(self, order):
        self.sort_order = order
        self.settings.setValue("sort_order", order)
        self.gallery_toolbar.set_sort_order(order)
        self._apply_order()
        self._request_analysis()

    def _apply_order(self):
        """Sort the photos again, staying on the same ones."""
        current = self.model.photo(self.current_row).path if self.current_row >= 0 else None
        compared = [self.model.photo(row).path for row in self.compare_rows]
        if not self.model.reorder(SORT_KEYS[self.sort_order]):
            return
        self.compare_rows = [self.model.row_of(path) for path in compared]
        if current is None:
            return
        self.current_row = self.model.row_of(current)
        if self.live_edit is None:
            self.go_to(self.current_row)  # for the filmstrip, and what to prefetch
        else:
            self._update_counter()  # a slider is being dragged: go_to() would end that
        if self.tabs.currentIndex() == GALLERY_TAB:
            self.grid.scrollTo(self.grid.currentIndex())

    def set_bursts(self, on):
        self.show_bursts = on
        self.settings.setValue("show_bursts", on)
        self.gallery_toolbar.bursts_button.setChecked(on)
        self.toast.show_message("Showing bursts" if on else "Not showing bursts")
        self._show_analysis()
        self._request_analysis()

    def _on_focus_filter_changed(self, value):
        self.proxy.set_focus(value)
        self._request_analysis()

    def _request_analysis(self):
        """Ask for what the current options need, and nothing more: reading every file of a card takes a while."""
        photos = [photo for photo in self.model.photos if not photo.copy]  # copies get their file's results
        capture = photos if self.sort_order == "date" or self.show_bursts else []
        if self.sort_order == "sharpness" or self.gallery_toolbar.focus_combo.currentData() != StatusFilterProxy.ALL:
            images = photos
        else:
            images = [photo for photo in photos if photo.burst]  # to find their sharpest frame
        self.analyzer.request(capture, images, photos if self.detect_faces else [])

    def _on_analyzed(self, path, results):
        rows = self.model.rows_of_file(path)  # a photo's virtual copies are the same picture
        if not rows:
            return
        for row in rows:
            photo = self.model.photo(row)
            for name, value in results.items():
                setattr(photo, name, faces.faces_from_json(value) if name == "faces" else value)
        if not self.analysis_timer.isActive():
            self.analysis_timer.start()
        shown = self._shown_rows()
        if self.face_zoom_pending and "faces" in results and all(self.model.photo(row).faces is not None for row in shown):
            self.zoom_to_faces()

    def _show_analysis(self):
        self.analysis_timer.stop()
        if self.show_bursts:
            bursts.assign(self.model.photos)
        else:
            for photo in self.model.photos:
                photo.burst = None
        self.model.analysis_changed()
        self._refresh_view()

    def _on_analysis_finished(self):
        self._show_analysis()
        self._apply_order()  # only now: the photos shouldn't keep moving while they are looked at
        self._request_analysis()  # bursts are known now, whose frames get scored

    def keep_one_of_burst(self):
        """Keep the current photo and reject the rest of its burst, as one undo step."""
        if self.current_row < 0:
            return
        current = self.model.photo(self.current_row)
        if current.burst is None:
            self.toast.show_message("This photo isn't part of a burst" if self.show_bursts else "Show bursts first (B)")
            return
        rows = [row for row, photo in enumerate(self.model.photos) if photo.burst and photo.burst.id == current.burst.id]
        first_visible = self.proxy.mapFromSource(self.model.index(self.current_row)).row()
        statuses = {row: KEEP if row == self.current_row else REJECT for row in rows}
        self._push_undo(self.set_marks(statuses))
        self.toast.show_message(f"Kept {current.name}, rejected {len(rows) - 1}")
        if self.tabs.currentIndex() == GALLERY_TAB:
            self._reselect_in_gallery(first_visible)
        elif not self.compare_rows and (following := self._cull_neighbor(max(rows), 1)) is not None:
            self.go_to(following)

    # -- window ------------------------------------------------------------

    def _restore_window(self):
        geometry = self.settings.value("geometry")
        if geometry is None or not self.restoreGeometry(geometry):
            self.resize(1400, 900)

    def closeEvent(self, event):
        if self.moving:
            event.ignore()  # files are being moved; the progress dialog has a Stop button
            return
        self._finish_crop(apply=True)
        self.plugins.emit("closing")
        self.save_session()
        self.settings.setValue("geometry", self.saveGeometry())
        if self.current_row >= 0:
            self.settings.setValue("last_file", relative_key(self.folder, self.model.photo(self.current_row).path))
        self.loader.shutdown()
        self.editor.shutdown()
        self.peaker.shutdown()
        self.tones.shutdown()
        self.edit_thumbnails.shutdown()
        self.analyzer.shutdown()
        self.scanner.shutdown()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.toast.isVisible():
            self.toast.show_message(self.toast.text())

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile() and os.path.isdir(urls[0].toLocalFile()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.load_folder(event.mimeData().urls()[0].toLocalFile())
