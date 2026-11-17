"""The edit panel's Advanced tab in the main window: its settings, the tools on the photo, masks, spots,
automatic settings, history and snapshots."""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import test_app  # noqa: E402
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QMouseEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from test_app import drag, open_folder, press, wait_for_image, wait_until  # noqa: E402

from tamis import automasks, editing  # noqa: E402
from tamis.widgets import NameDialog  # noqa: E402

CTRL = Qt.KeyboardModifier.ControlModifier
qapp, window = test_app.qapp, test_app.window  # the main window's fixtures


def editing_panel(win):
    if not win.edit_panel.isVisible():
        win.toggle_edit_panel()
    win.edit_panel.tabs.setCurrentIndex(1)
    wait_for_image(win)
    assert wait_until(lambda: win.image_view.image_rect() is not None)


def at(win, x, y):
    """The point of the image view at fractions (x, y) of the photo shown."""
    rect = win.image_view.image_rect()
    return QPoint(round(rect.x() + x * rect.width()), round(rect.y() + y * rect.height()))


def stroke(view, start, end, steps=8, modifiers=Qt.KeyboardModifier.NoModifier):
    """A drag with the button held down all along (QTest's moves don't say so)."""
    QTest.mousePress(view, Qt.MouseButton.LeftButton, modifiers, start)
    for i in range(1, steps + 1):
        point = QPointF(start + (end - start) * (i / steps))
        event = QMouseEvent(
            QEvent.Type.MouseMove, point, view.mapToGlobal(point), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, modifiers
        )
        QApplication.sendEvent(view, event)
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, modifiers, end)


def test_advanced_settings_go_into_the_edit_and_mark_the_tab(window):
    editing_panel(window)
    panel = window.edit_panel
    assert panel.tabs.tabText(1) == "Advanced"
    panel._sliders["whites"][0].setValue(30)
    assert window.model.photo(0).edit == {"whites": 30}
    assert panel.tabs.tabText(1) == "Advanced •"
    panel._toggles["bw"].setChecked(True)
    assert window.model.photo(0).edit == {"whites": 30, "bw": 1}
    press(window, Qt.Key.Key_Z, CTRL)
    assert window.model.photo(0).edit == {"whites": 30}


def test_the_curve_editor_adds_a_point(window):
    editing_panel(window)
    editor = window.edit_panel.curve_editor
    editor.resize(250, 190)
    drag(editor, QPoint(125, 95), QPoint(125, 60))
    curve = window.model.photo(0).edit["curve"]["rgb"]
    assert len(curve) == 3 and curve[1][1] > curve[1][0]


def test_the_colour_wheel_grades(window):
    editing_panel(window)
    panel = window.edit_panel
    panel.grade_region.set_index(2)
    panel._show_grade()
    panel.grade_wheel._set_from(QPoint(75, 8), True)  # straight up: 90°, strong
    edit = window.model.photo(0).edit
    assert edit["grade_highlights_sat"] > 50 and edit["grade_highlights_hue"] == 90


def test_picking_white_balance_on_the_photo(window):
    editing_panel(window)
    window.edit_panel.wb_button.click()
    assert window.edit_tool == "wb" and window.image_view.tool is not None
    QTest.mouseClick(window.image_view, Qt.MouseButton.LeftButton, pos=at(window, 0.5, 0.5))
    edit = window.model.photo(0).edit
    assert edit["temperature"] > 0  # the photo is blue-ish (0, 80, 120): warmed up
    assert window.model.photo(0).history[-1]["label"] == "White balance picker"


def test_drawing_a_linear_gradient_makes_a_mask(window):
    editing_panel(window)
    window.set_edit_tool("linear")
    drag(window.image_view, at(window, 0.5, 0.1), at(window, 0.5, 0.6))
    found = window.model.photo(0).edit["masks"]
    assert len(found) == 1 and found[0]["components"][0]["type"] == "linear"
    assert found[0]["components"][0]["y0"] == pytest.approx(0.1, abs=0.03)
    panel = window.edit_panel
    assert panel.selected_mask == 0
    panel.mask_sliders["exposure"][0].setValue(-100)
    assert window.model.photo(0).edit["masks"][0]["settings"] == {"exposure": -1.0}
    # Its handles move it.
    stroke(window.image_view, at(window, 0.5, 0.6), at(window, 0.5, 0.8))
    assert window.model.photo(0).edit["masks"][0]["components"][0]["y1"] == pytest.approx(0.8, abs=0.03)
    panel.mask_overlay.setChecked(True)
    assert window.image_view._mask_overlay is not None
    panel._delete_mask()
    assert "masks" not in window.model.photo(0).edit


def test_a_radial_gradient_added_to_a_mask(window):
    editing_panel(window)
    window.set_edit_tool("radial")
    drag(window.image_view, at(window, 0.3, 0.3), at(window, 0.4, 0.5))
    window.edit_panel.mask_mode.setCurrentIndex(2)  # subtract from the selected mask
    drag(window.image_view, at(window, 0.7, 0.7), at(window, 0.8, 0.8))
    (mask,) = window.model.photo(0).edit["masks"]
    assert [c.get("mode") for c in mask["components"]] == [None, "subtract"]
    assert mask["components"][0]["rx"] == pytest.approx(0.1 * 300 / 300, abs=0.02)


def test_brushing_a_mask(window):
    editing_panel(window)
    window.set_edit_tool("brush")
    stroke(window.image_view, at(window, 0.2, 0.5), at(window, 0.8, 0.5))
    (mask,) = window.model.photo(0).edit["masks"]
    (first,) = mask["components"][0]["strokes"]
    assert len(first["points"]) >= 2 and not first["erase"]
    window.edit_panel.brush_erase.setChecked(True)
    stroke(window.image_view, at(window, 0.5, 0.2), at(window, 0.5, 0.8))
    strokes = window.model.photo(0).edit["masks"][0]["components"][0]["strokes"]  # the same brush
    assert [s["erase"] for s in strokes] == [False, True]


def test_removing_a_spot_and_taking_it_back(window):
    editing_panel(window)
    window.edit_panel.spots_button.click()
    QTest.mouseClick(window.image_view, Qt.MouseButton.LeftButton, pos=at(window, 0.5, 0.5))
    (spot,) = window.model.photo(0).edit["spots"]
    assert spot["points"] == [pytest.approx([0.5, 0.5], abs=0.02)] and (spot["dx"], spot["dy"]) != (0, 0)
    assert window.edit_panel.spots_count.text() == "1 spot(s) removed"
    QTest.mouseClick(window.image_view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, at(window, 0.5, 0.5))
    assert "spots" not in window.model.photo(0).edit


def test_spots_and_masks_are_not_copied(window):
    window.model.set_field([0], "edit", {"whites": 10, "spots": [{"points": [[0.5, 0.5]], "r": 0.01, "dx": 0.1, "dy": 0}]})
    window.copy_edit()
    window.go_to(1)
    window.paste_edit()
    assert window.model.photo(1).edit == {"whites": 10}


def test_auto_tone_and_auto_white_balance(window):
    editing_panel(window)
    window.edit_panel.auto_tone_requested.emit()
    edit = window.model.photo(0).edit
    assert {"exposure", "whites"} & set(edit)
    window.edit_panel.auto_wb_requested.emit()
    assert window.model.photo(0).edit.get("temperature", 0) > 0


def test_finding_the_sky(window, tmp_path):
    image = QImage(300, 200, QImage.Format.Format_RGB32)
    image.fill(QColor(70, 70, 70))
    for y in range(80):
        for x in range(300):
            image.setPixelColor(x, y, QColor(120, 170, 240))
    assert image.save(str(tmp_path / "IMG_0.jpg"))
    open_folder(window, str(tmp_path))
    editing_panel(window)
    window.add_automatic_mask("sky")
    (mask,) = window.model.photo(0).edit["masks"]
    assert mask["components"] == [{"type": "sky"}] and mask["name"] == "Sky"
    rasters = editing.CONTEXT.rasters(window.model.photo(0).path, window.model.photo(0).edit)
    assert rasters["sky"][:20].mean() > 0.8 and rasters["sky"][-20:].mean() < 0.1


def test_finding_nothing_says_so(window, monkeypatch):
    editing_panel(window)
    monkeypatch.setattr(automasks, "available", lambda kind: True)
    monkeypatch.setattr(automasks, "find", lambda kind, rgb, vision=None: np.zeros(rgb.shape[:2], np.float32))
    window.add_automatic_mask("subject")
    assert "masks" not in window.model.photo(0).edit
    assert window.toast.isVisible()


def test_history_goes_back_and_snapshots_come_back(window, monkeypatch):
    editing_panel(window)
    panel = window.edit_panel
    panel._sliders["whites"][0].setValue(20)
    panel._sliders["blacks"][0].setValue(-10)
    photo = window.model.photo(0)
    assert [step["label"] for step in photo.history] == ["Original", "Whites +20", "Blacks -10"]
    assert panel.history_list.item(0).text() == "Blacks -10"  # newest first
    window.go_to_history(1)
    assert window.model.photo(0).edit == {"whites": 20}
    assert len(window.model.photo(0).history) == 3  # going back is not a step of its own

    monkeypatch.setattr(NameDialog, "exec", lambda dialog: (setattr(dialog, "name", "Bright"), 1)[1])
    monkeypatch.setattr(NameDialog, "name", "Bright", raising=False)
    window.save_snapshot()
    assert window.model.photo(0).snapshots == {"Bright": {"whites": 20}}
    panel._sliders["whites"][0].setValue(0)
    window.apply_snapshot("Bright")
    assert window.model.photo(0).edit == {"whites": 20}
    window.save_session()
    window.load_folder(window.folder)
    assert wait_until(lambda: not window.scanner.busy)
    photo = window.model.photo(0)
    assert photo.snapshots == {"Bright": {"whites": 20}} and photo.history[-1]["label"] == "Snapshot Bright"
    window.delete_snapshot("Bright")
    assert window.model.photo(0).snapshots == {}


def test_tools_stop_with_the_crop_tool_and_the_panel(window):
    editing_panel(window)
    window.set_edit_tool("brush")
    window.toggle_crop()
    assert window.edit_tool == "" and window.image_view.tool is None
    window.toggle_crop()
    window.set_edit_tool("spots")
    window.toggle_edit_panel()
    assert window.edit_tool == ""


def test_the_lens_section_says_why_there_is_no_profile(window):
    editing_panel(window)
    window.lens_folder = ""
    window._show_advanced(window.model.photo(0))
    assert "choose the lensfun" in window.edit_panel.lens_label.text()


# -- virtual copies ---------------------------------------------------------------------

def make_copy(win, row=0, edit=None):
    win.go_to(row)
    if edit is not None:
        win.model.set_field([row], "edit", edit)
    press(win, Qt.Key.Key_Apostrophe, CTRL)
    return win.model.photo(win.current_row)


def test_a_virtual_copy_is_a_photo_of_its_own(window, tmp_path):
    copy = make_copy(window, 0, {"whites": 10})
    assert copy.copy == 1 and copy.file == str(tmp_path / "IMG_0.jpg") and copy.name == "IMG_0.jpg"
    assert copy.display_name == "IMG_0.jpg · copy 1" and copy.edit == {"whites": 10}
    assert [p.display_name for p in window.model.photos[:3]] == ["IMG_0.jpg", "IMG_0.jpg · copy 1", "IMG_1.jpg"]
    wait_for_image(window)
    assert window.loader.preview(copy.path) is window.loader.preview(copy.file)  # the same picture
    press(window, Qt.Key.Key_P)
    assert copy.status == "keep" and window.model.photo(0).status is None
    second = make_copy(window, 1)
    assert second.copy == 2  # numbers stay unique per file

    window.save_session()
    open_folder(window, str(tmp_path))
    names = [(p.display_name, p.status, p.edit) for p in window.model.photos[:3]]
    assert names == [
        ("IMG_0.jpg", None, {"whites": 10}),
        ("IMG_0.jpg · copy 1", "keep", {"whites": 10}),
        ("IMG_0.jpg · copy 2", None, {"whites": 10}),
    ]


def test_removing_a_virtual_copy_asks_when_it_holds_work(window, monkeypatch):
    copy = make_copy(window, 0)
    window.model.set_status([window.current_row], "keep")
    seen = []
    monkeypatch.setattr(test_app.MessageDialog, "exec", lambda dialog: (seen.append(1), 1)[1])
    press(window, Qt.Key.Key_Backspace, CTRL | Qt.KeyboardModifier.ShiftModifier)
    assert seen and window.model.row_of(copy.path) == -1 and window.model.rowCount() == test_app.PHOTOS
    window.go_to(0)
    press(window, Qt.Key.Key_Backspace, CTRL | Qt.KeyboardModifier.ShiftModifier)  # not a copy: stays
    assert window.model.rowCount() == test_app.PHOTOS


def test_process_files_keeps_a_file_whose_copy_is_kept_and_exports_each(window, tmp_path, monkeypatch):
    make_copy(window, 0, {"whites": 10})
    window.model.set_status([window.current_row], "keep")  # the copy
    window.model.set_status([0], "reject")  # the photo itself
    test_app.process(window, monkeypatch, export=True)
    assert (tmp_path / "_KEEPS" / "IMG_0.jpg").exists()
    copy = window.model.photos[window.model.rows_of_file(str(tmp_path / "_KEEPS" / "IMG_0.jpg"))[1]]
    assert copy.path.startswith(str(tmp_path / "_KEEPS" / "IMG_0.jpg")) and copy.location == "keep"
    assert wait_until(lambda: (tmp_path / "_EXPORTS" / "IMG_0-1.jpg").exists())
    assert not (tmp_path / "_EXPORTS" / "IMG_0.jpg").exists()  # the photo itself is rejected
    window.undo()  # undo entries followed the copy


def test_a_file_with_a_kept_copy_is_not_trashed(window, tmp_path, monkeypatch, tmp_path_factory):
    bin_dir = test_app.fake_trash(monkeypatch, tmp_path_factory)
    test_app.confirm_dialogs(monkeypatch)
    make_copy(window, 1)
    window.model.set_status([window.model.row_of(str(tmp_path / "IMG_1.jpg")), window.model.row_of(str(tmp_path / "IMG_2.jpg"))],
                            "reject")
    test_app.process(window, monkeypatch, export=False)
    assert (tmp_path / "_REJECTS" / "IMG_1.jpg").exists()  # only a kept copy keeps the file
    window.trash_rejects()
    assert sorted(os.listdir(bin_dir)) == ["IMG_2.jpg"]  # but its copy isn't rejected: not trashed
    assert (tmp_path / "_REJECTS" / "IMG_1.jpg").exists()


def test_the_colour_target_changes_the_colour_under_the_pointer(window):
    editing_panel(window)
    window.edit_panel.mixer_mode.set_index(1)  # saturation
    window.edit_panel.target_button.click()
    stroke(window.image_view, at(window, 0.5, 0.5), at(window, 0.5, 0.3))  # dragged up
    edit = window.model.photo(0).edit
    assert any(key.startswith("sat_") and value > 0 for key, value in edit.items())  # (0, 80, 120): aqua and blue


def test_visualising_spots_shows_the_photo_in_grey(window):
    editing_panel(window)
    window.edit_panel.spots_view.setChecked(True)
    assert window._rendered_edit(window.model.photo(0))["_view"] == "spots"
    window.edit_panel.spots_view.setChecked(False)
    assert "_view" not in (window._rendered_edit(window.model.photo(0)) or {})
