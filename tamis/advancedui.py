"""The edit panel's Advanced tab, from the main window: tools on the photo (white balance picker,
colour target, gradients, brush, spot removal), automatic settings and masks, lens profiles,
the history and snapshots of an edit.

`AdvancedEditingMixin` is part of `TamisApp`.
"""
import json
import math
import os

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog

from . import autoadjust, automasks, colours, develop, editing, lenses, masks, profiles, spots
from .constants import CULL_TAB, is_raw
from .edittools import BrushTool, MaskTool, PickTool, SpotTool, TargetTool
from .models import HISTORY_LENGTH, FolderChanges, new_copy
from .widgets import MessageDialog, NameDialog

MASK_OVERLAY_SIZE = 900  # longest edge the selected mask is worked out at to be shown
MASKS_DIR = "masks"
LABELS = {
    "temperature": "Temperature", "tint": "Tint", "exposure": "Exposure", "contrast": "Contrast", "highlights": "Highlights",
    "shadows": "Shadows", "saturation": "Saturation", "vibrance": "Vibrance", "whites": "Whites", "blacks": "Blacks",
    "texture": "Texture", "clarity": "Clarity", "dehaze": "Dehaze", "sharpen": "Sharpening", "nr_luma": "Noise reduction",
    "nr_color": "Colour noise reduction", "crop": "Crop", "rotate": "Rotation", "straighten": "Straighten",
    "lut": "Film simulation", "grain": "Grain", "vignette": "Vignette", "masks": "Masks", "spots": "Spot removal",
    "curve": "Tone curve", "bw": "Black and white", "profile": "Profile", "lens_profile": "Lens profile",
}


def describe(old, new):
    """A short label for the change from edit `old` to `new`, for the history."""
    if not new:
        return "Reset"
    changed = [key for key in develop.DEFAULTS if old.get(key) != new.get(key)]
    if not changed:
        return "No change"
    if len(changed) == 1:
        key = changed[0]
        value = new.get(key)
        name = LABELS.get(key) or key.replace("_", " ").capitalize()
        if isinstance(value, (int, float)) and not isinstance(value, bool) and key not in ("rotate", "bw", "lens_profile"):
            return f"{name} {value:+.2f}" if isinstance(value, float) else f"{name} {value:+d}"
        return name
    groups = {LABELS.get(key, "").split()[0] if key in LABELS else key.split("_")[0] for key in changed}
    return f"{len(changed)} settings" if len(groups) > 1 else f"{(LABELS.get(changed[0]) or changed[0]).capitalize()}…"


class AdvancedEditingMixin:
    def _init_advanced(self, cache_dir):
        self.edit_tool = ""  # the tool working on the photo: see EditPanel.tool_requested
        self.edit_view = ""  # what the photo shows instead of itself ("spots", "sharpen_mask")
        self.mask_overlay_on = False
        self._mask_overlay_key = None
        self._target = None  # the colour target's drag: (lower, upper, share, part, values at the start)
        self._brush_target = None  # (mask index, component index) the brush paints into
        self._spot_drag = None  # spots when a source drag started
        self.lens_folder = self.settings.value("lens_folder", "") or lenses.default_folder() or ""
        editing.CONTEXT.lens_folder = self.lens_folder or None
        editing.CONTEXT.mask_store = automasks.MaskStore(os.path.join(cache_dir, MASKS_DIR))
        self.profile_folder = self.settings.value("profile_folder", "")
        try:
            defaults = json.loads(self.settings.value("camera_profiles", "") or "{}")
        except ValueError:
            defaults = {}
        editing.CONTEXT.camera_profiles = {k: v for k, v in defaults.items() if isinstance(k, str) and isinstance(v, str)}
        self._profile_cameras = {}  # camera -> [(id, name)] found for it in Adobe's folders
        self._scan_profiles()
        panel = self.edit_panel
        panel.tool_requested.connect(self.set_edit_tool)
        panel.auto_tone_requested.connect(self.auto_tone)
        panel.auto_wb_requested.connect(self.auto_white_balance)
        panel.lens_folder_requested.connect(self.choose_lens_folder)
        panel.profile_folder_requested.connect(self.choose_profile_folder)
        panel.profile_default_requested.connect(self.set_camera_profile)
        panel.automask_requested.connect(self.add_automatic_mask)
        panel.mask_selected.connect(self._on_mask_selected)
        panel.mask_overlay_toggled.connect(self._on_mask_overlay)
        panel.view_changed.connect(self._on_view_changed)
        panel.history_chosen.connect(self.go_to_history)
        panel.snapshot_save_requested.connect(self.save_snapshot)
        panel.snapshot_chosen.connect(self.apply_snapshot)
        panel.snapshot_delete_requested.connect(self.delete_snapshot)

    # -- helpers ---------------------------------------------------------------

    def _current_photo(self):
        return self.model.photo(self.current_row) if self.current_row >= 0 else None

    def _geometry(self, photo):
        """Where the shown photo's pixels come from in its source (masks.Geometry)."""
        edit = self._shown_edit(photo)
        size = self.image_view.image_size()
        if size is None or not size.isValid():
            return masks.Geometry(edit, (1000, 1500))
        return masks.Geometry(edit, (size.height(), size.width()))

    def _base(self, photo):
        """The photo as developed for editing (linear RGB, preview size)."""
        process = editing.process_of(photo.path, photo.edit)
        return self.editor.base(photo.path, process)

    def _sample(self, photo, us, vs, radius=2):
        base = self._base(photo)
        height, width = base.shape[:2]
        row, col = int(min(height - 1, max(0, vs * height))), int(min(width - 1, max(0, us * width)))
        patch = base[max(0, row - radius):row + radius + 1, max(0, col - radius):col + radius + 1]
        return patch.reshape(-1, 3).mean(axis=0)

    def _change_edit(self, photo, edit, final, label=None):
        """Show `edit` on the current photo: kept (undoable) if `final`, previewed otherwise."""
        edit = develop.clean(edit)
        if final:
            self.live_edit = None
            self._set_edits({self.current_row: edit}, label)
        else:
            self.live_edit = (photo.path, edit)
            self.edit_panel.set_edit(edit, is_raw(photo.path))
        self._refresh_view()

    def _with_wait(self, work):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return work()
        finally:
            QApplication.restoreOverrideCursor()

    # -- tools -------------------------------------------------------------------

    def set_edit_tool(self, tool):
        """Put `tool` to work on the photo ("" for none); see EditPanel.tool_requested."""
        if tool and (self.current_row < 0 or self.compare_rows):
            self.edit_panel.set_tool("")
            return
        self._finish_crop(apply=True)
        if tool:
            self.tabs.setCurrentIndex(CULL_TAB)
            if not self.edit_panel.isVisible():
                self.toggle_edit_panel()
        self.edit_tool = tool
        self._brush_target = None
        self.edit_panel.set_tool(tool)
        self.image_view.set_tool(self._make_tool(tool))
        self._refresh_view()

    def _make_tool(self, tool):
        if tool == "wb":
            return PickTool(self._pick_white_balance)
        if tool == "target":
            return TargetTool(self._on_target)
        if tool in ("linear", "radial", "edit_mask"):
            return MaskTool(tool, self._mask_handles, self._move_handle, self._draw_gradient, self._mask_outlines)
        if tool == "brush":
            return BrushTool(self._on_brush, self._brush_size, lambda: self.edit_panel.brush()[3])
        if tool == "spots":
            return SpotTool(self._on_spot, self._spot_size, self._shown_spots, self._move_spot, self._remove_spot)
        if self.edit_panel.isVisible() and self.edit_panel.selected_mask >= 0 and self.edit_panel.masks():
            return MaskTool("edit_mask", self._mask_handles, self._move_handle, self._draw_gradient, self._mask_outlines)
        return None

    def _after_photo_change(self):
        """A new photo is shown: tools start over on it."""
        self._brush_target = None
        self._target = None
        self._spot_drag = None
        if not self.edit_tool and self.image_view.tool is not None:
            self.image_view.set_tool(self._make_tool(""))

    def _pick_white_balance(self, x, y):
        photo = self._current_photo()
        if photo is None:
            return
        us, vs = self._geometry(photo).framed_to_source(x, y)
        values = autoadjust.white_balance_for(self._with_wait(lambda: self._sample(photo, us, vs)))
        self._change_edit(photo, {**photo.edit, **values}, True, "White balance picker")
        self.toast.show_message(f"White balance · temperature {values['temperature']:+d}, tint {values['tint']:+d}")

    def _on_target(self, x, y, distance, final):
        photo = self._current_photo()
        if photo is None:
            return
        if self._target is None:
            image = self.image_view._pixmap.toImage() if self.image_view._pixmap is not None else None
            if image is None:
                return
            column = int(min(image.width() - 1, x * image.width()))
            color = image.pixelColor(column, int(min(image.height() - 1, y * image.height())))
            rgb = np.array([[color.redF(), color.greenF(), color.blueF()]], dtype=np.float32)
            hue, _value, saturation = colours.hue_of(rgb)
            if float(saturation[0]) < 0.04:
                self.toast.show_message("No colour here to change: pick a coloured part")
                return
            lower, upper, share = colours.weights(hue)
            part = self.edit_panel.mixer_part()
            self._target = (int(lower[0]), int(upper[0]), float(share[0]), part, dict(photo.edit))
        lower, upper, share, part, start = self._target
        amount = distance * 0.4
        values = {}
        for index, weight in ((lower, 1 - share), (upper, share)):
            key = f"{part}_{colours.COLOURS[index]}"
            values[key] = max(-100, min(100, start.get(key, 0) + amount * weight))
        self._change_edit(photo, {**start, **values}, final, "Colour mixer")
        if final:
            self._target = None

    # -- masks ------------------------------------------------------------------

    def _selected_mask(self, edit=None):
        found = (edit if edit is not None else (self._current_photo() or {}).edit or {}).get("masks") or []
        index = self.edit_panel.selected_mask
        return (index, found[index]) if 0 <= index < len(found) else (-1, None)

    def _mask_handles(self):
        photo = self._current_photo()
        if photo is None:
            return []
        index, mask = self._selected_mask(self._shown_edit(photo))
        if mask is None:
            return []
        geometry = self._geometry(photo)
        handles = []
        for c, component in enumerate(mask["components"]):
            if component["type"] == "linear":
                handles.append((*geometry.source_to_framed(component["x0"], component["y0"]), (index, c, "p0")))
                handles.append((*geometry.source_to_framed(component["x1"], component["y1"]), (index, c, "p1")))
            elif component["type"] == "radial":
                cx, cy = geometry.from_source(component["x"], component["y"])
                angle = math.radians(geometry.angle_from_source(component.get("angle", 0)))
                rx, ry = component["rx"] * geometry.long, component["ry"] * geometry.long
                handles.append((*geometry.to_framed(cx, cy), (index, c, "c")))
                handles.append((*geometry.to_framed(cx + rx * math.cos(angle), cy + rx * math.sin(angle)), (index, c, "rx")))
                handles.append((*geometry.to_framed(cx - ry * math.sin(angle), cy + ry * math.cos(angle)), (index, c, "ry")))
        return handles

    def _mask_outlines(self):
        photo = self._current_photo()
        if photo is None:
            return []
        _index, mask = self._selected_mask(self._shown_edit(photo))
        if mask is None:
            return []
        geometry = self._geometry(photo)
        size = self.image_view.image_size()
        shapes = []
        for component in mask["components"]:
            if component["type"] == "linear":
                shapes.append(("line", geometry.source_to_framed(component["x0"], component["y0"]),
                               geometry.source_to_framed(component["x1"], component["y1"])))
            elif component["type"] == "radial" and size is not None:
                per_unit = geometry.pixels_per_unit()
                rx = component["rx"] * geometry.long * per_unit / size.width()
                ry = component["ry"] * geometry.long * per_unit / size.height()
                angle = geometry.angle_from_source(component.get("angle", 0)) + geometry.straighten
                shapes.append(("ellipse", geometry.source_to_framed(component["x"], component["y"]), rx, ry, angle))
        return shapes

    def _move_handle(self, key, x, y, final):
        photo = self._current_photo()
        if photo is None:
            return
        index, c, handle = key
        found = [dict(mask) for mask in photo.edit.get("masks") or []]
        if index >= len(found) or c >= len(found[index]["components"]):
            return
        geometry = self._geometry(photo)
        component = dict(found[index]["components"][c])
        if handle in ("p0", "p1"):
            sx, sy = geometry.framed_to_source(x, y)
            component[f"x{handle[1]}"], component[f"y{handle[1]}"] = sx, sy
        elif handle == "c":
            component["x"], component["y"] = geometry.framed_to_source(x, y)
        else:
            cx, cy = geometry.from_source(component["x"], component["y"])
            px, py = geometry.from_framed(x, y)
            distance = max(0.003, math.hypot(px - cx, py - cy) / geometry.long)
            if handle == "rx":
                component["rx"] = distance
                component["angle"] = math.degrees(math.atan2(py - cy, px - cx)) - geometry.rotate
            else:
                component["ry"] = distance
        components = list(found[index]["components"])
        components[c] = component
        found[index] = {**found[index], "components": components}
        self._change_edit(photo, {**photo.edit, "masks": found}, final, "Mask moved")

    def _draw_gradient(self, kind, start, end):
        photo = self._current_photo()
        if photo is None:
            return
        geometry = self._geometry(photo)
        if kind == "linear":
            (x0, y0), (x1, y1) = geometry.framed_to_source(*start), geometry.framed_to_source(*end)
            component = {"type": "linear", "x0": x0, "y0": y0, "x1": x1, "y1": y1}
        else:
            cx, cy = geometry.from_framed(*start)
            ex, ey = geometry.from_framed(*end)
            x, y = geometry.to_source(cx, cy)
            component = {
                "type": "radial", "x": x, "y": y, "rx": max(0.005, abs(ex - cx) / geometry.long),
                "ry": max(0.005, abs(ey - cy) / geometry.long), "angle": -geometry.rotate, "feather": 50,
            }
        self._add_component(photo, component, "Linear gradient" if kind == "linear" else "Radial gradient")

    def _add_component(self, photo, component, label):
        """Add `component` to the masks as the panel's mode says: as a new mask, or to the selected one."""
        found = [dict(mask) for mask in photo.edit.get("masks") or []]
        mode = self.edit_panel.mask_mode_choice()
        index = self.edit_panel.selected_mask
        if mode == "new" or not 0 <= index < len(found):
            found.append({"components": [component], "settings": {}})
            index = len(found) - 1
        else:
            found[index] = {**found[index], "components": [*found[index]["components"], {**component, "mode": mode}]}
        self.edit_panel.select_mask(index)
        self._change_edit(photo, {**photo.edit, "masks": found}, True, label)
        return index

    def _brush_size(self):
        size = self.image_view.image_size()
        photo = self._current_photo()
        if size is None or photo is None:
            return 0.02
        geometry = self._geometry(photo)
        return self.edit_panel.brush()[0] * geometry.long * geometry.pixels_per_unit() / size.width()

    def _on_brush(self, points, final):
        photo = self._current_photo()
        if photo is None:
            return
        geometry = self._geometry(photo)
        size, feather, flow, erase = self.edit_panel.brush()
        stroke = {"points": [list(geometry.framed_to_source(x, y)) for x, y in points], "size": size,
                  "feather": feather, "flow": flow, "erase": erase}
        found = [dict(mask) for mask in photo.edit.get("masks") or []]
        target = self._brush_target
        if target is None or target[0] >= len(found) or target[1] >= len(found[target[0]]["components"]):
            mode = self.edit_panel.mask_mode_choice()
            index = self.edit_panel.selected_mask
            component = {"type": "brush", "strokes": []}
            if mode == "new" or not 0 <= index < len(found):
                found.append({"components": [component], "settings": {}})
                target = (len(found) - 1, 0)
            else:
                found[index] = {**found[index], "components": [*found[index]["components"], {**component, "mode": mode}]}
                target = (index, len(found[index]["components"]) - 1)
        index, c = target
        components = list(found[index]["components"])
        components[c] = {**components[c], "strokes": [*components[c].get("strokes", []), stroke]}
        found[index] = {**found[index], "components": components}
        if final:
            self._brush_target = target
            self.edit_panel.select_mask(index)
        self._change_edit(photo, {**photo.edit, "masks": found}, final, "Brush")

    def _on_mask_selected(self, index):
        self._brush_target = None
        if not self.edit_tool:
            self.image_view.set_tool(self._make_tool(""))
        self._refresh_view()

    def _on_mask_overlay(self, on):
        self.mask_overlay_on = on
        self._mask_overlay_key = None
        self._refresh_view()

    def _show_mask_overlay(self, photo):
        """The selected mask in red over the photo, while asked for."""
        index, _mask = self._selected_mask(self._shown_edit(photo))
        size = self.image_view.image_size()
        if not self.mask_overlay_on or index < 0 or size is None or self.cropping or not self.edit_panel.isVisible():
            self.image_view.set_mask_overlay(None)
            return
        edit = self._shown_edit(photo)
        scale = min(1.0, MASK_OVERLAY_SIZE / max(size.width(), size.height()))
        height, width = max(1, round(size.height() * scale)), max(1, round(size.width() * scale))
        key = json.dumps([photo.path, edit.get("masks"), develop.framing(edit), index, height, width], sort_keys=True)
        if key == self._mask_overlay_key:
            return
        weight = masks.overlay(edit["masks"], index, edit, (height, width), editing.CONTEXT.rasters(photo.path, edit))
        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        rgba[..., 0] = 255
        rgba[..., 1] = 40
        rgba[..., 2] = 40
        rgba[..., 3] = np.clip(weight * 150, 0, 255).astype(np.uint8)
        image = QImage(rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
        self._mask_overlay_key = key
        self.image_view.set_mask_overlay(QPixmap.fromImage(image))

    def add_automatic_mask(self, kind):
        """Find the subject, people or sky of the current photo and add them as a mask (or to the selected one)."""
        photo = self._current_photo()
        if photo is None:
            return
        if not automasks.available(kind):
            self.toast.show_message("Finding the subject and people needs macOS 14 or later (Apple's Vision)", 4000)
            return
        store = editing.CONTEXT.mask_store
        if not store.has(photo.path, kind):
            def work():
                linear = self._base(photo)
                return automasks.find(kind, develop.encode(linear))

            try:
                found = self._with_wait(work)
            except Exception as exc:  # Vision or a bad photo: nothing added
                self.toast.show_message(f"Could not find the {kind}: {exc}", 4000)
                return
            if not found.any():
                self.toast.show_message({"subject": "No subject found", "person": "Nobody found", "sky": "No sky found"}[kind])
                return
            store.save(photo.path, kind, found)
        names = {"subject": "Subject", "person": "People", "sky": "Sky"}
        index = self._add_component(photo, {"type": kind}, f"{names[kind]} mask")
        found = [dict(mask) for mask in self.model.photo(self.current_row).edit.get("masks") or []]
        if 0 <= index < len(found) and len(found[index]["components"]) == 1 and not found[index].get("name"):
            found[index]["name"] = names[kind]
            self._change_edit(photo, {**self.model.photo(self.current_row).edit, "masks": found}, True, f"{names[kind]} mask")

    # -- spots ------------------------------------------------------------------

    def _spot_size(self):
        size = self.image_view.image_size()
        photo = self._current_photo()
        if size is None or photo is None:
            return 0.01
        geometry = self._geometry(photo)
        return self.edit_panel.spot_settings()[0] * geometry.long * geometry.pixels_per_unit() / size.width()

    def _shown_spots(self):
        photo = self._current_photo()
        if photo is None:
            return []
        geometry = self._geometry(photo)
        size = self.image_view.image_size()
        width = size.width() if size is not None else 1
        shown = []
        for spot in self._shown_edit(photo).get("spots") or []:
            points = [geometry.source_to_framed(x, y) for x, y in spot["points"]]
            sources = [geometry.source_to_framed(x + spot["dx"], y + spot["dy"]) for x, y in spot["points"]]
            shown.append((points, sources, spot["r"] * geometry.long * geometry.pixels_per_unit() / width))
        return shown

    def _on_spot(self, points, final):
        photo = self._current_photo()
        if photo is None or not final:
            return
        geometry = self._geometry(photo)
        radius, mode = self.edit_panel.spot_settings()
        source_points = [list(geometry.framed_to_source(x, y)) for x, y in points]
        if any(not (0 <= x <= 1 and 0 <= y <= 1) for x, y in source_points):
            return
        cx = sum(x for x, _ in source_points) / len(source_points)
        cy = sum(y for _, y in source_points) / len(source_points)
        span = max(max(abs(x - cx), abs(y - cy)) for x, y in source_points)
        dx, dy = self._with_wait(lambda: spots.find_source(self._base(photo), cx, cy, radius + span))
        spot = {"points": source_points, "r": radius, "dx": dx, "dy": dy, "mode": mode}
        self._change_edit(photo, {**photo.edit, "spots": [*(photo.edit.get("spots") or []), spot]}, True, "Spot removed")

    def _move_spot(self, index, dx, dy, final):
        photo = self._current_photo()
        if photo is None:
            return
        if self._spot_drag is None:
            self._spot_drag = [dict(spot) for spot in photo.edit.get("spots") or []]
        found = [dict(spot) for spot in self._spot_drag]
        if index >= len(found):
            return
        geometry = self._geometry(photo)
        spot = found[index]
        x, y = spot["points"][0]
        fx, fy = geometry.source_to_framed(x + spot["dx"], y + spot["dy"])
        sx, sy = geometry.framed_to_source(fx + dx, fy + dy)
        found[index] = {**spot, "dx": sx - x, "dy": sy - y}
        if final:
            self._spot_drag = None
        self._change_edit(photo, {**photo.edit, "spots": found}, final, "Spot source moved")

    def _remove_spot(self, index):
        photo = self._current_photo()
        found = list((photo.edit.get("spots") or []) if photo else [])
        if index < len(found):
            del found[index]
            self._change_edit(photo, {**photo.edit, "spots": found}, True, "Spot deleted")

    def _on_view_changed(self, view):
        if view != self.edit_view:
            self.edit_view = view
            self._refresh_view()

    # -- automatic settings ---------------------------------------------------------

    def auto_tone(self):
        photo = self._current_photo()
        if photo is None:
            return
        edit = self._with_wait(lambda: autoadjust.auto_tone(self._base(photo), photo.edit, is_raw(photo.path)))
        self._change_edit(photo, edit, True, "Auto tone")
        self.toast.show_message("Auto tone: a starting point to adjust")

    def auto_white_balance(self):
        photo = self._current_photo()
        if photo is None:
            return
        values = self._with_wait(lambda: autoadjust.auto_white_balance(self._base(photo)))
        self._change_edit(photo, {**photo.edit, **values}, True, "Auto white balance")

    # -- lens profiles -------------------------------------------------------------

    def choose_lens_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "The lensfun database's folder (with its .xml files)", self.lens_folder or os.path.expanduser("~")
        )
        if folder:
            self.lens_folder = folder
            self.settings.setValue("lens_folder", folder)
            editing.CONTEXT.lens_folder = folder
            editing.CONTEXT._lenses.clear()
            found = lenses.database(folder)
            self.toast.show_message(f"{len(found.lenses)} lens profile(s) found")
            if self.current_row >= 0:
                self._show_edit_in_panel()
            self.model.thumbnails_changed()
            self._refresh_view()

    def _lens_text(self, photo):
        if not self.lens_folder:
            return "No lens profiles: choose the lensfun database's folder below."
        try:
            found = editing.CONTEXT.lens(photo.path, {"lens_profile": 1})
        except Exception:  # an unreadable file: no profile
            found = None
        if found:
            return f"Profile: {found['name']}"
        from . import exif

        lens = exif.read_lens(photo.path).get("lens")
        return f"No profile for {lens}." if lens else "The photo doesn't say which lens took it."

    # -- colour profiles -------------------------------------------------------------

    def _scan_profiles(self):
        """The DCPs of the chosen folder (Adobe's are looked through per camera, when needed)."""
        editing.CONTEXT.profiles = profiles.scan([self.profile_folder]) if self.profile_folder else {}
        self._profile_cameras = {}

    def _profiles_for(self, camera):
        """[(id, name)] of the profiles for `camera`, from the chosen folder and Adobe's."""
        if camera not in self._profile_cameras:
            model = camera.split(" ", 1)[-1]
            adobe = {}
            for folder in profiles.default_folders():
                # Adobe's are many: only the files that name this camera are read.
                for path in self._adobe_files(folder, model):
                    adobe.update(profiles.scan_file(path))
            editing.CONTEXT.profiles.update(adobe)
            self._profile_cameras[camera] = sorted(
                ((profile_id, name) for profile_id, (_path, name, maker) in editing.CONTEXT.profiles.items()
                 if profiles.matches(maker, *camera.split(" ", 1)) or profiles.matches(maker, "", camera)),
                key=lambda item: item[1].casefold(),
            )
        return self._profile_cameras[camera]

    @staticmethod
    def _adobe_files(folder, model):
        wanted = profiles.normalise(model)
        found = []
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if name.lower().endswith(".dcp") and wanted and wanted in profiles.normalise(os.path.join(root, name)):
                    found.append(os.path.join(root, name))
        return found

    def choose_profile_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "A folder of DCP colour profiles (.dcp)", self.profile_folder or os.path.expanduser("~")
        )
        if folder:
            self.profile_folder = folder
            self.settings.setValue("profile_folder", folder)
            self._scan_profiles()
            self.toast.show_message(f"{len(editing.CONTEXT.profiles)} colour profile(s) found")
            self._profiles_changed()

    def set_camera_profile(self):
        """Develop every RAW of the current photo's camera with its profile, unless one has its own."""
        photo = self._current_photo()
        if photo is None or not is_raw(photo.path):
            return
        camera = editing.CONTEXT.camera(photo.path)
        if not camera:
            self.toast.show_message("The photo doesn't say which camera took it")
            return
        chosen = photo.edit.get("profile")
        defaults = dict(editing.CONTEXT.camera_profiles)
        if chosen:
            defaults[camera] = chosen
        else:
            defaults.pop(camera, None)
        editing.CONTEXT.camera_profiles = defaults
        self.settings.setValue("camera_profiles", json.dumps(defaults))
        self.toast.show_message(f"{camera}: {profiles.describe(chosen)} by default")
        self._profiles_changed()

    def _profiles_changed(self):
        """Profiles were found or a default changed: everything developed is developed again."""
        self.editor.reset()
        self.edit_thumbnails.reset()
        self.model.thumbnails_changed()
        if self.current_row >= 0:
            self._show_edit_in_panel()
        self._refresh_view()

    def _show_profiles(self, photo):
        if not is_raw(photo.path):
            self.edit_panel.set_profiles([])
            self.edit_panel.profile_note.setText("Profiles are for RAWs.")
            return
        camera = editing.CONTEXT.camera(photo.path)
        self.edit_panel.set_profiles(self._profiles_for(camera) if camera else [])
        default = editing.CONTEXT.camera_profiles.get(camera)
        note = "Profiles change how a RAW's colours are rendered."
        if default:
            note += f" Default for the {camera}: {profiles.describe(default)}."
        self.edit_panel.profile_note.setText(note)

    # -- virtual copies ---------------------------------------------------------------

    def create_virtual_copy(self):
        """A virtual copy of each target photo: its edit and description, marks of its own, right after it."""
        rows = self._target_rows()
        if not rows or self.moving:
            return
        numbers = {}
        for photo in self.model.photos:
            numbers[photo.file] = max(numbers.get(photo.file, 0), photo.copy)
        added = []
        for row in rows:
            photo = self.model.photo(row)
            numbers[photo.file] += 1
            added.append(new_copy(photo, numbers[photo.file]))
        self._update_photos(FolderChanges(added=added))
        self.go_to(self.model.row_of(added[0].path))
        self.session_timer.start()
        self.toast.show_message(f"{len(added)} virtual cop{'y' if len(added) == 1 else 'ies'} made")

    def remove_virtual_copies(self):
        """Remove the target photos that are virtual copies (their files stay with their photo)."""
        copies = [self.model.photo(row) for row in self._target_rows() if self.model.photo(row).copy]
        if not copies or self.moving:
            self.toast.show_message("Only virtual copies can be removed")
            return
        edited = [photo.display_name for photo in copies if photo.edit or photo.status or photo.rating]
        if edited:
            dialog = MessageDialog(
                self, "Remove virtual copies", f"Remove {len(copies)} virtual cop{'y' if len(copies) == 1 else 'ies'}? "
                "Their edits and marks are lost; the files stay.", confirm_text="Remove", details=edited,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
        master = self.model.row_of(copies[0].file)
        self._update_photos(FolderChanges(removed={photo.path for photo in copies}),
                            select=self.model.photo(master).path if master >= 0 else None)
        self.session_timer.start()

    # -- history and snapshots -----------------------------------------------------

    def _record_history(self, row, old, new, label):
        """Add the change from `old` to `new` to the photo's history."""
        photo = self.model.photo(row)
        steps = list(photo.history)
        if not steps:
            steps.append({"label": "Original" if not old else "Earlier edit", "edit": old})
        steps.append({"label": label or describe(old, new), "edit": new})
        photo.history = steps[-HISTORY_LENGTH:]

    def _show_history(self, photo):
        steps = photo.history
        current = max((i for i, step in enumerate(steps) if step["edit"] == photo.edit), default=-1)
        self.edit_panel.set_history([step["label"] for step in steps], current)
        self.edit_panel.set_snapshots(sorted(photo.snapshots, key=str.casefold))

    def go_to_history(self, index):
        photo = self._current_photo()
        if photo is None or not 0 <= index < len(photo.history):
            return
        self.live_edit = None
        self._set_edits({self.current_row: dict(photo.history[index]["edit"])}, record=False)
        self._refresh_view()

    def save_snapshot(self):
        photo = self._current_photo()
        if photo is None:
            return
        dialog = NameDialog(self, "Save snapshot", "A name for this version of the edit, to come back to it.", "Save")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        photo.snapshots = {**photo.snapshots, dialog.name: dict(photo.edit)}
        self._show_history(photo)
        self.session_timer.start()
        self.toast.show_message(f"Snapshot “{dialog.name}” saved")

    def apply_snapshot(self, name):
        photo = self._current_photo()
        if photo is None or name not in photo.snapshots:
            return
        self.live_edit = None
        self._set_edits({self.current_row: dict(photo.snapshots[name])}, f"Snapshot {name}")
        self._refresh_view()

    def delete_snapshot(self, name):
        photo = self._current_photo()
        if photo is None or name not in photo.snapshots:
            return
        photo.snapshots = {key: value for key, value in photo.snapshots.items() if key != name}
        self._show_history(photo)
        self.session_timer.start()

    # -- shown with the photo --------------------------------------------------------

    def _show_advanced(self, photo):
        """What the Advanced tab says of the current photo: its lens, history and snapshots."""
        self.edit_panel.set_lens(self._lens_text(photo))
        self._show_profiles(photo)
        self.edit_panel.set_edit(photo.edit, is_raw(photo.path))  # with the profiles of its camera listed
        self._show_history(photo)
