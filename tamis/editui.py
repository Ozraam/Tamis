"""Editing from the main window: the edit panel, presets, rotation, crop, before/after,
and framing with Qt what the renderer didn't make.

`EditingMixin` is part of `TamisApp`.
"""

import json
import os
from html import escape

from PyQt6.QtCore import QEvent, QRect, QSize, Qt
from PyQt6.QtGui import QPainter, QPixmap, QTransform
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog

from . import cropping, develop, lut
from .constants import CULL_TAB, is_raw
from .editing import exposure_shift
from .widgets import MessageDialog, NameDialog, inform

CROP_CACHE_SIZE = 8  # cropped previews and full-resolution images kept


class EditingMixin:
    # -- showing edits -----------------------------------------------------

    def _shown_edit(self, photo):
        """The photo's edit, or the one being dragged."""
        return self.live_edit[1] if self.live_edit is not None and self.live_edit[0] == photo.path else photo.edit

    def _showing_before(self, photo):
        """Whether the photo is being shown without an edit it has."""
        return self.before and not self.cropping and bool(develop.look(self._shown_edit(photo)))

    def _develops(self, photo):
        """Whether the photo shows its own development even unedited: a RAW, while the edit panel is open.

        A RAW's preview is the camera's JPEG, which edits don't start from: shown while
        editing, it would change all at once with the first slider moved.
        """
        return self.edit_panel.isVisible() and is_raw(photo.path)

    def _rendered_edit(self, photo):
        """The edit the photo is shown with (the one being dragged, its development…), None if it shows its preview."""
        develops = self._develops(photo)
        edit = self._shown_edit(photo)
        if self.before and not self.cropping:
            if not develops:
                return None  # the preview any unedited photo shows, which _framed() keeps in its frame
            edit = develop.own(edit)  # the development, framed, without the look
        if self.cropping:
            edit = {key: value for key, value in edit.items() if key != "crop"}
        if not edit and not develops:
            return None
        return edit

    def _edited_pixmap(self, photo):
        """The render of the photo's edit (or of the one being dragged), if it has one, or of its development."""
        edit = self._rendered_edit(photo)
        if edit is None:
            return None
        live = self.live_edit is not None and self.live_edit[0] == photo.path
        return self.editor.pixmap(photo.path, edit, self._lut_path(edit), quick=live, same_crop=self.cropping)

    def _edited_full(self, photo):
        """(pixmap, up to date) of the photo's edit at full resolution, for the 1:1 zoom; (None, True) if it has none.

        While a slider is being dragged, nothing new is rendered: the last one stays up.
        """
        edit = self._rendered_edit(photo)
        if edit is None or self.cropping:
            return None, True
        live = self.live_edit is not None and self.live_edit[0] == photo.path
        return self.editor.full_pixmap(photo.path, edit, self._lut_path(edit), request=not live)

    def _framed(self, pixmap, framing):
        """`pixmap` turned, straightened and cropped as `framing` says, like the renderer does it."""
        if pixmap is None or not framing:
            return pixmap
        key = (pixmap.cacheKey(), json.dumps(framing, sort_keys=True))
        if key not in self.crop_cache:
            framed = pixmap
            if framing.get("rotate"):
                framed = framed.transformed(QTransform().rotate(framing["rotate"]))
            if framing.get("straighten"):
                framed = self._straightened(framed, framing["straighten"])
            if framing.get("crop"):
                top, bottom, left, right = develop.crop_box(framed.height(), framed.width(), framing["crop"])
                framed = framed.copy(QRect(left, top, right - left, bottom - top))
            self.crop_cache[key] = framed
            while len(self.crop_cache) > CROP_CACHE_SIZE:
                del self.crop_cache[next(iter(self.crop_cache))]  # oldest
        return self.crop_cache[key]

    @staticmethod
    def _straightened(pixmap, degrees):
        width, height = pixmap.width(), pixmap.height()
        zoom = develop.straighten_zoom(height, width, degrees)
        result = QPixmap(pixmap.size())
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.translate(width / 2, height / 2)
        painter.rotate(degrees)
        painter.scale(zoom, zoom)
        painter.translate(-width / 2, -height / 2)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()
        return result

    @staticmethod
    def _framed_size(size, framing):
        if size is None or not framing or not size.isValid():
            return size
        height, width = develop.framed_size(size.height(), size.width(), framing)
        return QSize(width, height)

    def _lut_path(self, edit):
        return self.luts.get(edit.get("lut")) if edit.get("lut") else None

    def _lut_missing(self, edit):
        """Whether the edit uses a film simulation that isn't in the LUT folder: rendering it would skip the look."""
        return bool(edit.get("lut")) and edit["lut"] not in self.luts

    def _on_edit_failed(self, path, error):
        if self._is_shown(path):
            self.toast.show_message(f"Could not render the edit: {error}", 4000)

    def set_clipping(self, on):
        self.show_clipping = on
        self.edit_panel.set_clipping(on)
        self.toast.show_message("Clipping warnings on" if on else "Clipping warnings off")
        self._refresh_view()

    def _hold_before(self):
        """The key was pressed: show the photo without its edit until a key is let go."""
        QApplication.instance().installEventFilter(self)
        self._set_before(True)

    def _set_before(self, on):
        if not on:
            QApplication.instance().removeEventFilter(self)
        if on != self.before:
            self.before = on
            self._refresh_view()

    def eventFilter(self, watched, event):
        # Only while a key holds the before view. Any key that is let go ends it: with a layout
        # where the key takes a modifier, it may be reported as another one, and this must never stick.
        if event.type() == QEvent.Type.WindowDeactivate or (
            event.type() == QEvent.Type.KeyRelease and not event.isAutoRepeat()
        ):
            self._set_before(False)
        return super().eventFilter(watched, event)

    # -- editing -----------------------------------------------------------

    def toggle_edit_panel(self):
        visible = not self.edit_panel.isVisible()
        self.edit_panel.setVisible(visible)
        self.settings.setValue("edit_panel", visible)
        if visible:
            self.tabs.setCurrentIndex(CULL_TAB)
            if self.current_row >= 0:
                self._show_edit_in_panel()
        self.live_edit = None
        self._refresh_view()

    def update_process(self):
        """Develop the current RAW the current way, with the exposure that keeps it as bright as it was."""
        if self.current_row < 0:
            return
        photo = self.model.photo(self.current_row)
        edit = photo.edit
        old = edit.get("process", develop.PROCESS)
        if not is_raw(photo.path) or old >= develop.PROCESS:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            shift = exposure_shift(photo.path, old)
        except Exception as exc:  # a RAW LibRaw can't read: nothing to compare, nothing changed
            self.toast.show_message(f"Could not develop {photo.name}: {exc}", 4000)
            return
        finally:
            QApplication.restoreOverrideCursor()
        exposure = edit.get("exposure", 0.0) + shift
        self.live_edit = None
        self._set_edit([self.current_row], develop.clean({**edit, "process": develop.PROCESS, "exposure": exposure}))
        self.toast.show_message(f"New development · exposure {shift:+.2f} EV, to keep the photo as bright")

    def _show_edit_in_panel(self):
        photo = self.model.photo(self.current_row)
        self.edit_panel.set_edit(photo.edit, is_raw(photo.path))

    def _on_edit_changed(self, edit, final):
        if self.current_row < 0:
            return
        photo = self.model.photo(self.current_row)
        if final:
            self.live_edit = None
            self._set_edit([self.current_row], edit)
        else:
            self.live_edit = (photo.path, edit)
        self._refresh_view()

    def _set_edit(self, rows, edit):
        self._set_edits({row: dict(edit) for row in rows})

    def _set_edits(self, values):
        """Give each row its own edit, as one undo step: `values` is {row: edit}."""
        self._push_undo(self.model.set_values("edit", values))
        for row, edit in values.items():
            if not edit:
                self.editor.forget(self.model.photo(row).path)

    def _apply_look(self, rows, look):
        """Give `rows` this look. Each photo keeps its own framing and process."""
        self._set_edits({row: develop.clean({**look, **develop.own(self.model.photo(row).edit)}) for row in rows})

    def copy_edit(self):
        """Copy the current photo's look. Its framing belongs to that photo: it is not copied."""
        if self.current_row >= 0:
            self.edit_clipboard = develop.look(self.model.photo(self.current_row).edit)
            self.toast.show_message("Edit copied" if self.edit_clipboard else "Copied: no edit")

    def paste_edit(self):
        """Paste the copied look on the target photos, which keep their own framing."""
        rows = self._target_rows()
        if self.edit_clipboard is None:
            self.toast.show_message("Nothing to paste: copy an edit first")
        elif rows:
            self._apply_look(rows, self.edit_clipboard)
            self.toast.show_message(f"Edit pasted on {len(rows)} photo(s)")

    def reset_edit(self):
        self._finish_crop(apply=False)
        rows = self._target_rows()
        if rows:
            self._set_edit(rows, {})

    # -- presets -----------------------------------------------------------

    def save_preset(self):
        """Keep the current photo's look under a name, for this and other shoots."""
        if self.current_row < 0:
            return
        look = develop.look(self.model.photo(self.current_row).edit)
        if not look:
            self.toast.show_message("Nothing to save: this photo has no look to keep")
            return
        dialog = NameDialog(self, "Save preset", "A name for this look. A crop or rotation is not part of it.", "Save")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.name
        if name in self.presets:
            confirm = MessageDialog(
                self, "Replace preset", f"Replace the preset <b>{escape(name)}</b>?", confirm_text="Replace"
            )
            if confirm.exec() != QDialog.DialogCode.Accepted:
                return
        self._change_presets(lambda: self.presets.save(name, look), f"Preset “{name}” saved")

    def apply_preset(self, name):
        look = self.presets.get(name)
        rows = self._target_rows()
        if look is not None and rows:
            self._apply_look(rows, look)
            self.toast.show_message(f"“{name}” applied to {len(rows)} photo(s)")

    def delete_preset(self, name):
        dialog = MessageDialog(
            self, "Delete preset", f"Delete the preset <b>{escape(name)}</b>? Photos using its look keep it.", confirm_text="Delete"
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._change_presets(lambda: self.presets.delete(name), f"Preset “{name}” deleted")

    def _change_presets(self, change, done):
        try:
            change()
        except OSError as exc:
            inform(self, "Could not save the presets", escape(str(exc)))
            return
        self.edit_panel.set_presets(self.presets.names())
        self.toast.show_message(done)

    # -- rotation ----------------------------------------------------------

    def rotate_photos(self, degrees):
        """Turn the target photos by a quarter turn (90 or -90): one undo step, also while culling."""
        self._finish_crop(apply=True)
        rows = self._target_rows()
        if rows:
            self._set_edits({row: develop.turned(self.model.photo(row).edit, degrees) for row in rows})

    # -- crop --------------------------------------------------------------

    def toggle_crop(self):
        """Open the crop tool on the current photo, or apply the frame and close it."""
        if self.cropping:
            self._finish_crop(apply=True)
            return
        if self.current_row < 0:
            return
        self._end_compare()
        self.tabs.setCurrentIndex(CULL_TAB)
        if not self.edit_panel.isVisible():
            self.toggle_edit_panel()  # the frame's shape is chosen there
        self.image_view.reset_zoom()
        self.live_edit = None
        crop = self.model.photo(self.current_row).edit.get("crop")
        if crop:
            self.edit_panel.set_crop_free()  # never reshape a frame the photo already has
        self.cropping = True
        self.edit_panel.set_cropping(True)
        self._refresh_view()  # the whole photo, before the frame's shape is worked out from it
        self.image_view.start_crop(crop or cropping.FULL, self.edit_panel.crop_pixel_ratio())

    def _finish_crop(self, apply):
        if not self.cropping:
            return
        frame = self.image_view.crop_frame
        self.cropping = False
        self.image_view.stop_crop()
        self.edit_panel.set_cropping(False)
        if apply and self.current_row >= 0:
            edit = {**self.model.photo(self.current_row).edit, "crop": frame}
            self._set_edit([self.current_row], develop.clean(edit))
        self._refresh_view()

    def _confirm_crop(self):
        if self.cropping:
            self._finish_crop(apply=True)

    def clear_crop(self):
        """Back to the whole photo: the frame while cropping, otherwise the photo's crop (undoable)."""
        if self.cropping:
            self.edit_panel.set_crop_free()
            self.image_view.set_crop_ratio(None)
            self.image_view.set_crop_frame(cropping.FULL)
        elif self.current_row >= 0:
            edit = self.model.photo(self.current_row).edit
            self._set_edit([self.current_row], develop.clean({key: value for key, value in edit.items() if key != "crop"}))

    def _on_crop_ratio_changed(self):
        if self.cropping:
            self.image_view.set_crop_ratio(self.edit_panel.crop_pixel_ratio())

    def choose_lut_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Folder of film simulation LUTs (HaldCLUT .png, .cube)", self.lut_folder or os.path.expanduser("~")
        )
        if folder:
            self.lut_folder = folder
            self.settings.setValue("lut_folder", folder)
            self._load_luts()

    def _load_luts(self, quiet=False):
        try:
            self.luts = lut.scan(self.lut_folder) if self.lut_folder and os.path.isdir(self.lut_folder) else {}
        except OSError:
            self.luts = {}
        self.edit_panel.set_luts([(lut_id, lut.display_name(lut_id)) for lut_id in self.luts])
        self.model.thumbnails_changed()
        if not quiet:
            self.toast.show_message(f"{len(self.luts)} film simulation(s) found")
        self._refresh_view()
