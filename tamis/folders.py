"""Opening, reading and refreshing the photo folder, and saving its session and sidecars.

`FolderMixin` is part of `TamisApp`.
"""

import json
import logging
import os
import time
from html import escape

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFileDialog

from . import bursts, xmp
from .constants import KEEP_DIR, LEGACY_GLOBAL_CONFIG_FILENAME, REJECT, REJECT_DIR, SESSION_FILENAME
from .models import SORT_KEYS, relative_key, save_session
from .widgets import inform

log = logging.getLogger(__name__)

# Changes in the folder are looked for this long after they happen, so that a burst of files is read once;
# after the app's own session writes, a while longer.
REFRESH_DELAY_MS = 800
SCAN_PROGRESS_AFTER = 0.3  # seconds of reading a folder before saying how far it got
OWN_WRITE_SECONDS = 1.0
OWN_WRITE_REFRESH_MS = 4000


class FolderMixin:
    # -- folder ------------------------------------------------------------

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select a photo folder", self.folder or os.path.expanduser("~")
        )
        if folder:
            self.load_folder(folder)

    def load_folder(self, folder, select=None, keep_state=False):
        """Open `folder`; `select` is a photo's path relative to it (or, from older versions, its name).

        The folder is read in the background (see FolderScanner): the photos of
        the folder open until then stay usable, and opening another folder
        meanwhile supersedes this one. `keep_state`, for the open folder, keeps
        cached images and undo.
        """
        if self.moving:
            return
        # A folder dropped on the window ends with a separator: paths are joined to it and compared.
        folder = os.path.normpath(os.path.abspath(folder))
        self._finish_crop(apply=True)
        self.save_session()
        self.refresh_timer.stop()
        self.scan_started = time.monotonic()
        self.scanner.scan(folder, self.session_fallback_dir, ("open", folder, select, keep_state, self.model.revision))

    def _on_scan_progress(self, folder, count):
        if count and time.monotonic() - self.scan_started > SCAN_PROGRESS_AFTER:  # only when it takes a while
            self.toast.show_message(f"Reading {os.path.basename(folder) or folder}… {count} photo(s)", 3000)

    def _on_scanned(self, result):
        kind, folder, *details = result.request
        self._report_damaged_sessions(result.problems)
        if kind == "open":
            self._open_scanned(result, *details)
        elif folder == self.folder and not self.moving:
            self._apply_folder_changes(result, *details)

    def _open_scanned(self, result, select, keep_state, revision):
        folder, photos = result.folder, result.photos
        if result.error:
            inform(self, "Cannot open folder", escape(f"{folder}: {result.error}"))
            return
        same_folder = keep_state and folder == self.folder
        if same_folder and self.model.revision != revision:
            # Marked while being read: what was read is out of date, save it and read again.
            self.load_folder(folder, select, keep_state)
            return
        if self.moving:  # (cannot happen: no long job starts while a folder is being read)
            return
        self.save_session()  # what was marked meanwhile in the folder open until now
        if self.toast.text().startswith("Reading "):
            self.toast.hide()

        self.live_edit = None
        if not same_folder:
            self.loader.reset()
            self.editor.reset()
            self.session_warning = None
            self.xmp_warned = False
            self.undo_stack.clear()
            self.crop_cache.clear()
            self.moved_keys.clear()
            self.peaker.reset()
            self.tones.reset()
            self.edit_thumbnails.reset()
            self.analyzer.open_folder(folder)
        self.analyzer.apply_known(photos, result.file_keys)
        if self.show_bursts:
            bursts.assign(photos)
        photos.sort(key=SORT_KEYS[self.sort_order])
        self.xmp_dirty = {photo.path for photo in photos if photo.xmp_pending}  # try those again
        if self.xmp_marks == "rating":
            self.xmp_dirty |= {photo.path for photo in photos if photo.status == REJECT}
        self._end_compare(show=False)
        self.folder = folder
        self.current_row = -1
        if not same_folder:
            self._set_keeps_only(False)
        self.model.set_photos(photos)
        self.model.analysis_changed()
        self._request_analysis()
        self.settings.setValue("last_folder", folder)
        self.header.set_folder(folder)
        self._watch(folder)
        event = "folder_refreshed" if same_folder else "folder_opened"

        if not photos:
            card = os.path.isdir(os.path.join(folder, "DCIM"))
            hint = "\n\nA memory card? Copy its photos with Import…" if card else ""
            self.image_view.set_image(None, f"No photos in this folder{hint}")
            self.header.set_counter("")
            self.plugins.emit(event, folder)
            return

        keys = [relative_key(folder, photo.path) for photo in photos]
        names = [photo.name for photo in photos]
        if select in keys:
            self.go_to(keys.index(select))
        else:
            self.go_to(names.index(select) if select in names else 0)
        self._keep_current_in_filter()
        self.plugins.emit(event, folder)

    def refresh(self, auto=False):
        """Pick up photos added to or removed from the folder, keeping marks, the selection and the current photo.

        Only new photos are read (in the background), and the others stay as they are.
        """
        if not self.folder or self.moving or self.scanner.busy:
            return
        self.loader.retry_failed_thumbnails()
        known = {photo.path: photo.companions for photo in self.model.photos}
        self.scanner.refresh(self.folder, known, self.session_fallback_dir, ("refresh", self.folder, auto))

    def _apply_folder_changes(self, result, auto):
        changes = result.changes
        if result.error or not changes:
            if not auto:
                self.toast.show_message("No new photos")
            return
        # Read from what was shown when the refresh started: since then, a photo may have gone another way.
        changes.removed &= {photo.path for photo in self.model.photos}
        changes.added = [photo for photo in changes.added if self.model.row_of(photo.path) == -1]
        # A RAW landing after its JPEG makes one photo of them: it keeps the JPEG's marks.
        replaced = {photo.path: photo for photo in self.model.photos if photo.path in changes.removed}
        for photo in changes.added:
            earlier = next((replaced[path] for path in photo.companions if path in replaced), None)
            if earlier is not None and (photo.status, photo.rating, photo.label) == (None, 0, None):
                photo.status, photo.rating, photo.label = earlier.status, earlier.rating, earlier.label
        before = {path for photo in self.model.photos for path in photo.files}
        self.analyzer.apply_known(changes.added, result.file_keys)
        self._update_photos(changes)
        after = {path for photo in self.model.photos for path in photo.files}
        added, removed = len(after - before), len(before - after)
        if not (added or removed):
            if not auto:
                self.toast.show_message("No new photos")
            return
        self._watch(self.folder)  # _KEEPS / _REJECTS may have just appeared
        parts = [f"{added} new file(s)" if added else "", f"{removed} gone" if removed else ""]
        self.toast.show_message("Folder updated · " + ", ".join(part for part in parts if part))
        self.plugins.emit("folder_refreshed", self.folder)

    def _update_photos(self, changes, select=None):
        """Add and remove photos of the open folder in place (FolderChanges), staying on the current photo.

        Photos are identified by path: nothing may keep referring to removed ones.
        `select`, a path, is the photo to show when the current one goes.
        """
        current = self.model.photo(self.current_row).path if self.current_row >= 0 else None
        if current in changes.removed and select is None:
            # The next photo that stays, or else the one before.
            rows = range(self.model.rowCount())
            after = [r for r in rows if r > self.current_row and self.model.photo(r).path not in changes.removed]
            before = [r for r in rows if r < self.current_row and self.model.photo(r).path not in changes.removed]
            select = self.model.photo(after[0] if after else before[-1]).path if after or before else None
        compared = [self.model.photo(row).path for row in self.compare_rows]
        gone = changes.removed
        if gone:
            files = [path for photo in self.model.photos if photo.path in gone for path in photo.files]
            self.loader.forget(files)
            for path in gone:
                self.editor.forget(path)
                self.edit_thumbnails.forget(path)
            self.xmp_dirty -= gone
            self.undo_stack = [kept for undo in self.undo_stack if (kept := [c for c in undo if c[0] not in gone])]
            if set(compared) & gone:
                self._end_compare(show=False)
                compared = []
        for photo in changes.added:
            if photo.xmp_pending or (self.xmp_marks == "rating" and photo.status == REJECT):
                self.xmp_dirty.add(photo.path)

        self.model.update_photos(changes, SORT_KEYS[self.sort_order])
        self.compare_rows = [self.model.row_of(path) for path in compared]
        if changes.added or gone:
            self._show_analysis()  # bursts may have changed
            self._request_analysis()
        follow = current if current not in gone else select
        if follow is None or self.model.row_of(follow) == -1:
            self.current_row = -1
            if not self.model.rowCount():
                self.image_view.set_image(None, "No photos in this folder")
                self.header.set_counter("")
            elif current in gone:
                self.go_to(0)
            return
        row = self.model.row_of(follow)
        if follow == current:
            self.current_row = row  # the same photo: only its row moved
            if self.live_edit is None:
                self.go_to(row)  # for the filmstrip, and what to prefetch
            else:
                self._update_counter()  # a slider is being dragged: go_to() would end that
        else:
            self.go_to(row)
            self._keep_current_in_filter()

    def _on_directory_changed(self, path):
        if path == self.folder and time.monotonic() - self.saved_at < OWN_WRITE_SECONDS:
            # Most likely the session that was just saved: saving follows every mark, so look
            # later, once for all of them, rather than reading the folder after each one.
            if not self.refresh_timer.isActive():
                self.refresh_timer.start(OWN_WRITE_REFRESH_MS)
        else:
            self.refresh_timer.start(REFRESH_DELAY_MS)

    def _watch(self, folder):
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        directories = [d for d in (folder, os.path.join(folder, KEEP_DIR), os.path.join(folder, REJECT_DIR)) if os.path.isdir(d)]
        if directories:
            self.watcher.addPaths(directories)

    def _restore_last_folder(self):
        folder = self.settings.value("last_folder", "")
        last_file = self.settings.value("last_file", "")
        if not folder:
            folder, last_file = self._legacy_global_state()
        if folder and os.path.isdir(folder):
            self.load_folder(folder, select=last_file)

    @staticmethod
    def _legacy_global_state():
        """Last folder from the pre-QSettings `~/.cullspeed_global.json`."""
        try:
            with open(os.path.join(os.path.expanduser("~"), LEGACY_GLOBAL_CONFIG_FILENAME), encoding="utf-8") as file:
                config = json.load(file)
            return config.get("last_folder") or "", config.get("last_file") or ""
        except (OSError, ValueError, AttributeError):
            return "", ""

    def save_session(self):
        self.session_timer.stop()
        if not self.folder or not self.model.photos:
            return
        self._write_xmp()
        problems = []
        try:
            path = save_session(self.folder, self.model.photos, self.session_fallback_dir, self.moved_keys, problems)
        except OSError as exc:
            log.warning("Could not save the session of %s: %s", self.folder, exc)
            self._session_problem("failed", f"Could not save marks: {exc.strerror or exc}")
            return
        finally:
            self._report_damaged_sessions(problems)
        self.moved_keys.clear()
        if path != os.path.join(self.folder, SESSION_FILENAME):
            self._session_problem("fallback", "This folder is read-only: marks are saved in the app's data folder")
        else:
            self.saved_at = time.monotonic()
            self.session_warning = None

    def _session_problem(self, kind, message):
        # Saving runs after every mark: say it once, not on every keystroke.
        if self.session_warning != kind:
            self.session_warning = kind
            self.toast.show_message(message, 5000)

    def _report_damaged_sessions(self, paths):
        # In a read-only folder a damaged file stays where it is, and is found again.
        paths = [path for path in paths if path not in self.damaged_sessions]
        self.damaged_sessions.update(paths)
        if paths:
            # Once the current work is done: this can come up in the middle of opening or saving a folder.
            QTimer.singleShot(0, lambda: inform(
                self,
                "Saved marks could not be read",
                "A session file is damaged, so some marks, ratings or edits may be missing. It was kept here:",
                details=paths,
            ))

    def _write_xmp(self):
        """Write pending ratings and labels to the photos' XMP sidecars; what fails is tried again at the next save."""
        dirty, self.xmp_dirty = self.xmp_dirty, set()
        listings = {}
        for path in dirty:
            row = self.model.row_of(path)
            if row == -1:
                continue
            photo = self.model.photo(row)
            directory = os.path.dirname(photo.path)
            try:
                if directory not in listings:
                    listings[directory] = {name.lower(): name for name in os.listdir(directory)}
                rejected = self.xmp_marks == "rating" and photo.status == REJECT
                xmp.write(photo.files, xmp.REJECTED_RATING if rejected else photo.rating, photo.label, listings[directory])
                photo.xmp_pending = False
            except OSError as exc:
                log.warning("Could not write the XMP sidecar of %s: %s", photo.path, exc)
                photo.xmp_pending = True
                self.xmp_dirty.add(path)
                if not self.xmp_warned:
                    self.xmp_warned = True
                    self.toast.show_message(
                        f"Could not write XMP sidecars ({exc.strerror or exc}): ratings stay in Tamis only", 5000
                    )
