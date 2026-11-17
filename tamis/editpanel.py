"""The quick edit panel shown beside the photo in the Cull view."""
import os

import numpy as np
from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPolygonF
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import colours, curves, develop, local, masks, tones
from .constants import COLOR_BORDER
from .editwidgets import ColourWheel, CurveEditor, Section, Segmented, shrinkable

# key, label, slider range, slider units per setting unit
_SECTIONS = (
    ("Crop", (
        ("straighten", "Straighten", (-150, 150), 10),
    )),
    ("Light", (
        ("exposure", "Exposure", (-500, 500), 100),
        ("contrast", "Contrast", (-100, 100), 1),
        ("highlights", "Highlights", (-100, 100), 1),
        ("shadows", "Shadows", (-100, 100), 1),
    )),
    ("Colour", (
        ("temperature", "Temperature", (-100, 100), 1),
        ("tint", "Tint", (-100, 100), 1),
        ("saturation", "Saturation", (-100, 100), 1),
    )),
    ("Film", (
        ("lut_strength", "Strength", (0, 100), 1),
        ("grain", "Grain", (0, 100), 1),
        ("grain_size", "Grain size", (1, 3), 1),
        ("vignette", "Vignette", (-100, 100), 1),
    )),
)


_SIGNED = (-100, 100)
_ADVANCED_TONE = (
    ("whites", "Whites", _SIGNED, 1),
    ("blacks", "Blacks", _SIGNED, 1),
)
_PARAMETRIC = (
    ("curve_highlights", "Highlights", _SIGNED, 1),
    ("curve_lights", "Lights", _SIGNED, 1),
    ("curve_darks", "Darks", _SIGNED, 1),
    ("curve_shadows", "Shadows", _SIGNED, 1),
)
_SPLITS = (
    ("curve_split1", "Shadows | darks", (5, 45), 1),
    ("curve_split2", "Darks | lights", (30, 70), 1),
    ("curve_split3", "Lights | highlights", (55, 95), 1),
)
_DETAIL = (
    ("texture", "Texture", _SIGNED, 1),
    ("clarity", "Clarity", _SIGNED, 1),
    ("dehaze", "Dehaze", _SIGNED, 1),
)
_SHARPEN = (
    ("sharpen", "Amount", (0, 150), 1),
    ("sharpen_radius", "Radius", (5, 30), 10),
    ("sharpen_detail", "Detail", (0, 100), 1),
    ("sharpen_masking", "Masking", (0, 100), 1),
)
_NOISE = (
    ("nr_luma", "Luminance", (0, 100), 1),
    ("nr_color", "Colour", (0, 100), 1),
)
_LENS_PROFILE = (
    ("lens_distortion", "Distortion", (0, 200), 1),
    ("lens_vignetting", "Vignetting", (0, 200), 1),
)
_LENS_MANUAL = (
    ("distortion", "Distortion", _SIGNED, 1),
    ("devignette", "Vignetting", _SIGNED, 1),
    ("ca_red", "Red / cyan fringe", _SIGNED, 1),
    ("ca_blue", "Blue / yellow fringe", _SIGNED, 1),
    ("defringe", "Defringe", (0, 100), 1),
)
_GRADE_LABELS = ("Shadows", "Midtones", "Highlights", "Global")
# Sliders a local adjustment has: (key, label, slider range, scale).
_MASK_SLIDERS = tuple(
    (key, key.capitalize() if key != "sharpen" else "Sharpness",
     (round(low * 100), round(high * 100)) if key == "exposure" else (low, high), 100 if key == "exposure" else 1)
    for key, (low, high) in masks.SETTINGS.items()
)
# Settings whose default depends on the photo: their value for a RAW, and for anything else.
_AUTOMATIC = {"sharpen": (local.SHARPEN_RAW, 0), "nr_color": (local.NR_COLOR_RAW, 0)}
MASK_KINDS = (
    ("linear", "Linear Gradient"),
    ("radial", "Radial Gradient"),
    ("brush", "Brush"),
    ("subject", "Subject"),
    ("sky", "Sky"),
    ("person", "People"),
)
MASK_MODES = (("new", "New mask"), ("add", "Add to mask"), ("subtract", "Subtract from mask"), ("intersect", "Intersect with mask"))


def mask_label(mask, index):
    if mask.get("name"):
        return mask["name"]
    kinds = dict(MASK_KINDS)
    first = mask["components"][0]["type"] if mask.get("components") else "linear"
    extra = len(mask.get("components", ())) - 1
    return f"Mask {index + 1} · {kinds.get(first, first)}" + (f" +{extra}" if extra > 0 else "")


class _ScrollsPanel:
    """Leaves the wheel to the panel's scroll area, so scrolling over a control never changes it."""

    def wheelEvent(self, event):
        event.ignore()  # passes the event on to the parent, which ends at the scroll area


class _Slider(_ScrollsPanel, QSlider):
    """A slider that goes back to its default on double-click."""

    def __init__(self, default, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.default = default
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep the window's shortcuts working

    def mouseDoubleClickEvent(self, event):
        self.setValue(self.default)
        self.sliderReleased.emit()  # a deliberate change: commit it


class _Combo(_ScrollsPanel, QComboBox):
    """A combo box the wheel doesn't change."""


class _LutCombo(_Combo):
    """A combo box whose popup is a menu tree following the LUT folder's sub-folders.

    Items stay a flat list (text: display name, data: LUT id like "Fuji/Velvia 50.png");
    only the popup is nested, so a big LUT collection is browsed one folder at a time.
    """

    def showPopup(self):
        menu = self.build_menu()
        menu.setMinimumWidth(self.width())
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))
        menu.deleteLater()
        self.hidePopup()  # resets the combo's pressed state

    def build_menu(self):
        menu = QMenu(self)
        submenus = {}  # folder path -> QMenu

        def folder_menu(parts):
            if not parts:
                return menu
            key = "/".join(parts)
            if key not in submenus:
                submenus[key] = folder_menu(parts[:-1]).addMenu(parts[-1])
            return submenus[key]

        luts = []
        for index in range(self.count()):
            lut_id = self.itemData(index)
            if lut_id and self.itemText(index).startswith("Missing: "):
                lut_id = None  # not in the folder: keep it at the top level
            if lut_id:
                luts.append((lut_id.split("/"), index))
            else:
                self._add_action(menu, self.itemText(index), index)
        if luts:
            menu.addSeparator()
        # Sub-folders before the LUTs next to them, like a file browser.
        for parts, index in sorted(luts, key=lambda item: (
            [p.casefold() for p in item[0][:-1]] + ["￿"], item[0][-1].casefold()
        )):
            *folders, name = parts
            self._add_action(folder_menu(folders), os.path.splitext(name)[0], index)
        return menu

    def _add_action(self, menu, text, index):
        action = menu.addAction(text)
        action.setCheckable(True)
        action.setChecked(index == self.currentIndex())
        action.triggered.connect(lambda _checked, index=index: self.setCurrentIndex(index))
        if index == self.currentIndex():
            # Bold the folders leading to the current LUT, so it's easy to find again.
            while isinstance(menu.parent(), QMenu):
                font = menu.menuAction().font()
                font.setBold(True)
                menu.menuAction().setFont(font)
                menu = menu.parent()


# Label, width / height of the frame in pixels; "original" is the photo's own shape.
CROP_RATIOS = (
    ("Free", None),
    ("Original", "original"),
    ("1:1", 1.0),
    ("3:2", 3 / 2),
    ("2:3", 2 / 3),
    ("4:5", 4 / 5),
    ("5:4", 5 / 4),
    ("16:9", 16 / 9),
    ("9:16", 9 / 16),
)


def _format(key, value):
    if key in ("exposure", "mask_exposure"):
        return f"{value:+.2f} EV"
    if key == "straighten":
        return f"{value:+.1f}°" if value else "0°"
    if key == "grain_size":
        return ("fine", "medium", "coarse")[int(value) - 1]
    if key == "sharpen_radius":
        return f"{value:.1f}"
    if key.startswith("hue_"):
        return f"{value:+d}°" if value else "0°"
    if key in ("lens_distortion", "lens_vignetting"):
        return f"{int(value)} %"
    low = develop.RANGES.get(key, (-1, 1))[0]
    if low >= 0:
        return f"{int(value)}"
    return f"{int(value):+d}" if value else "0"


class Histogram(QWidget):
    """The red, green and blue histogram of what is shown, with how much of it is clipped at either end."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tone = None
        self.setFixedHeight(84)

    def set_tone(self, tone):
        if tone is not self.tone:
            self.tone = tone
            self.update()

    def _curve(self, counts, peak):
        """The outline of one channel, as a closed polygon filling the widget's width."""
        width, height = self.width(), self.height()
        heights = np.minimum(counts / peak, 1.0)
        points = [QPointF(0, height)]
        points += [QPointF(i / 255 * width, height * (1 - float(value))) for i, value in enumerate(heights)]
        points.append(QPointF(width, height))
        return QPolygonF(points)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#000000"))
        if self.tone is not None:
            counts = self.tone.counts
            # The ends can tower over everything else: they are told apart by the figures below.
            peak = max(1, int(counts[:, 2:-2].max()))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            for channel, color in enumerate(("#c03030", "#30a040", "#3060d0")):
                painter.setBrush(QColor(color))
                painter.drawPolygon(self._curve(counts[channel], peak))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            self._draw_clipped(painter, self.tone.shadows, tones.SHADOW_COLOR, Qt.AlignmentFlag.AlignLeft)
            self._draw_clipped(painter, self.tone.highlights, tones.HIGHLIGHT_COLOR, Qt.AlignmentFlag.AlignRight)
        painter.setPen(QColor(COLOR_BORDER))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def _draw_clipped(self, painter, fraction, color, side):
        if fraction >= 0.0005:  # shown as at least 0.1 %
            painter.setPen(QColor(*color))
            painter.drawText(self.rect().adjusted(5, 3, -5, -3), side | Qt.AlignmentFlag.AlignTop, f"{fraction * 100:.1f}%")




class EditPanel(QFrame):
    """Sliders for the edit of the current photo, in a Basic tab and an Advanced one.

    `changed(edit, final)`: `final` is False while a slider is being dragged
    (preview only) and True once the change should be kept (and undoable).
    """

    changed = pyqtSignal(dict, bool)
    copy_requested = pyqtSignal()
    paste_requested = pyqtSignal()
    reset_requested = pyqtSignal()
    export_requested = pyqtSignal()
    lut_folder_requested = pyqtSignal()
    crop_requested = pyqtSignal()
    crop_cleared = pyqtSignal()
    crop_ratio_changed = pyqtSignal()
    rotate_requested = pyqtSignal(int)  # degrees: 90 or -90
    clipping_toggled = pyqtSignal(bool)
    before_held = pyqtSignal(bool)  # the Before button is being held down, or was let go
    preset_chosen = pyqtSignal(str)
    preset_save_requested = pyqtSignal()
    preset_delete_requested = pyqtSignal(str)
    process_update_requested = pyqtSignal()  # the RAW is to be developed the current way
    # Advanced
    tool_requested = pyqtSignal(str)  # a tool on the photo ("wb", "target", "linear", "radial", "brush", "spots"), "" for none
    auto_tone_requested = pyqtSignal()
    auto_wb_requested = pyqtSignal()
    lens_folder_requested = pyqtSignal()
    profile_folder_requested = pyqtSignal()
    profile_default_requested = pyqtSignal()  # use the photo's profile for its camera from now on
    automask_requested = pyqtSignal(str)  # "subject", "sky" or "person", with mask_mode()
    mask_selected = pyqtSignal(int)
    mask_overlay_toggled = pyqtSignal(bool)
    view_changed = pyqtSignal(str)  # what the photo shows instead of itself: "spots", "sharpen_mask" or ""
    history_chosen = pyqtSignal(int)
    snapshot_save_requested = pyqtSignal()
    snapshot_chosen = pyqtSignal(str)
    snapshot_delete_requested = pyqtSignal(str)

    WIDTH = 320

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("EditPanel")
        self.setFixedWidth(self.WIDTH)
        self._sliders = {}
        self._values = {}
        self._toggles = {}  # key -> QCheckBox, for settings that are on or off
        self._updating = False
        self._crop = None  # the photo's crop and quarter turns: not sliders, but part of edit()
        self._rotate = 0
        self._process = develop.PROCESS  # how the photo's RAW is developed: not a slider either
        self._raw = False
        self._curve = None  # the point curves, as stored
        self._wheels = {}  # grade_*_hue and grade_*_sat
        self._masks = None
        self._spots = None
        self._profile = None
        self.selected_mask = -1
        self._sections = []
        self._section_keys = {}  # Section -> settings it holds, for its dot

        # Stays visible above the scrolling sliders, which are what it is there to judge.
        header = QWidget()
        header.setObjectName("EditPanelHeader")
        top = QVBoxLayout(header)
        top.setContentsMargins(14, 12, 14, 10)
        top.setSpacing(6)
        title = QLabel("Edit")
        title.setObjectName("DialogTitle")
        self.histogram = Histogram()
        top.addWidget(title)
        top.addWidget(self.histogram)
        top.addLayout(self._tone_row())

        self.tabs = QTabWidget()
        self.tabs.setObjectName("EditTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._scroll(self._basic_tab()), "Basic")
        self.tabs.addTab(self._scroll(self._advanced_tab()), "Advanced")
        self.tabs.currentChanged.connect(lambda _index: self.stop_tool())

        # Actions stay visible below the scrolling sliders.
        footer = QWidget()
        footer.setObjectName("EditPanelFooter")
        buttons = QGridLayout(footer)
        buttons.setContentsMargins(14, 10, 14, 12)
        buttons.setSpacing(6)
        for i, (text, signal) in enumerate((
            ("Copy", self.copy_requested),
            ("Paste", self.paste_requested),
            ("Reset", self.reset_requested),
        )):
            buttons.addWidget(self._button(text, signal), 0, i)
        self.presets_button = QPushButton("Presets")
        self.presets_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.presets_button.setToolTip("Save this look under a name, or apply a saved one to the selection")
        self.presets_menu = QMenu(self)
        self.presets_button.setMenu(self.presets_menu)
        self.set_presets([])
        buttons.addWidget(self.presets_button, 1, 0, 1, 3)
        export = self._button("Export JPEG…", self.export_requested)
        export.setObjectName("Primary")
        buttons.addWidget(export, 2, 0, 1, 3)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(header)
        outer.addWidget(self.tabs, 1)
        outer.addWidget(footer)

    # -- building --------------------------------------------------------------

    def _scroll(self, content):
        content.setObjectName("EditPanelContent")
        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll

    def _basic_tab(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 8, 14, 14)
        layout.setSpacing(6)

        hint = QLabel("Double-click a slider to reset it.")
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        layout.addWidget(self._raw_note())

        for section, rows in _SECTIONS:
            heading = QLabel(section.upper())
            heading.setObjectName("SectionTitle")
            layout.addSpacing(8)
            layout.addWidget(heading)
            if section == "Crop":
                layout.addLayout(self._crop_row())
                layout.addLayout(self._rotate_row())
            if section == "Film":
                layout.addLayout(self._lut_row())
            layout.addLayout(self._slider_grid(rows))
        layout.addStretch()
        return content

    def _slider_grid(self, rows, store=True, on_change=None, on_release=None):
        """A grid of labelled sliders. With `store`, they are settings of the edit; otherwise they call
        `on_change(key)` and `on_release()`, and are returned as {key: (slider, scale, value label)}."""
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        made = {}
        for row, (key, label, (low, high), scale) in enumerate(rows):
            default = develop.DEFAULTS.get(key, 0) if store else 0
            default = default if default is not None else 0
            slider = _Slider(round(default * scale))
            slider.setRange(low, high)
            slider.setValue(round(default * scale))
            name = QLabel(label)
            value = QLabel(_format(key, default))
            value.setObjectName("Muted")
            value.setMinimumWidth(64)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(name, row * 2, 0)
            grid.addWidget(value, row * 2, 1)
            grid.addWidget(slider, row * 2 + 1, 0, 1, 2)
            if store:
                slider.valueChanged.connect(lambda _value, key=key: self._on_slider(key))
                slider.sliderReleased.connect(self._commit)
                self._sliders[key] = (slider, scale)
                self._values[key] = value
            else:
                slider.valueChanged.connect(lambda _value, key=key: on_change(key))
                slider.sliderReleased.connect(on_release)
                made[key] = (slider, scale, value)
        return grid if store else (grid, made)

    def _section(self, layout, title, expanded=False):
        section = Section(title, expanded)
        layout.addWidget(section)
        self._sections.append(section)
        return section

    def _advanced_tab(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 8, 14, 14)
        layout.setSpacing(4)
        hint = QLabel("For finer work. Sections with a dot are in use.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Auto and white balance
        section = self._section(layout, "Auto", expanded=True)
        row = QHBoxLayout()
        row.setSpacing(6)
        auto_tone = self._button("Auto Tone", self.auto_tone_requested)
        auto_tone.setToolTip("Set exposure, contrast, highlights, shadows, whites and blacks from the histogram (undoable)")
        auto_wb = self._button("Auto WB", self.auto_wb_requested)
        auto_wb.setToolTip("Set temperature and tint so that the photo has no colour cast (undoable)")
        self.wb_button = self._tool_button("Pick WB", "wb", "Click a neutral grey or white in the photo to set the white balance")
        for widget in (auto_tone, auto_wb, self.wb_button):
            row.addWidget(shrinkable(widget))
        section.content.addLayout(row)

        # Whites and blacks
        section = self._section(layout, "Tone", expanded=True)
        section.content.addLayout(self._slider_grid(_ADVANCED_TONE))
        self._section_keys[section] = [key for key, *_ in _ADVANCED_TONE]

        # Tone curve
        section = self._section(layout, "Tone Curve")
        self.curve_channel = Segmented(["RGB", "Red", "Green", "Blue"])
        self.curve_channel.changed.connect(lambda _index: self._show_curve())
        self.curve_editor = CurveEditor()
        self.curve_editor.changed.connect(self._on_curve)
        curve_hint = QLabel("Click to add a point and drag it; double-click it to remove it.")
        curve_hint.setObjectName("Muted")
        curve_hint.setWordWrap(True)
        section.content.addWidget(self.curve_channel)
        section.content.addWidget(self.curve_editor)
        section.content.addWidget(curve_hint)
        section.content.addLayout(self._slider_grid(_PARAMETRIC))
        section.content.addLayout(self._slider_grid(_SPLITS))
        self._section_keys[section] = [key for key, *_ in (*_PARAMETRIC, *_SPLITS)] + ["curve"]

        # Vibrance and the HSL mixer
        section = self._section(layout, "Colour Mixer")
        section.content.addLayout(self._slider_grid((("vibrance", "Vibrance", _SIGNED, 1),)))
        self.mixer_mode = Segmented(["Hue", "Saturation", "Luminance"])
        self.target_button = self._tool_button(
            "◎  Drag on the Photo", "target", "Drag up or down on the photo to change the colour under the pointer"
        )
        section.content.addWidget(self.mixer_mode)
        section.content.addWidget(self.target_button)
        self.mixer_pages = QStackedWidget()
        for part in ("hue", "sat", "lum"):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.addLayout(self._slider_grid(
                [(f"{part}_{colour}", colour.capitalize(), _SIGNED, 1) for colour in colours.COLOURS]
            ))
            self.mixer_pages.addWidget(page)
        self.mixer_mode.changed.connect(self.mixer_pages.setCurrentIndex)
        section.content.addWidget(self.mixer_pages)
        self._section_keys[section] = ["vibrance", *colours.HSL_KEYS]

        # Colour grading
        section = self._section(layout, "Colour Grading")
        self.grade_region = Segmented(list(_GRADE_LABELS))
        self.grade_region.changed.connect(self._show_grade)
        self.grade_wheel = ColourWheel()
        self.grade_wheel.changed.connect(self._on_wheel)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch()
        wheel_row.addWidget(self.grade_wheel)
        wheel_row.addStretch()
        self.grade_values = QLabel()
        self.grade_values.setObjectName("Muted")
        self.grade_values.setAlignment(Qt.AlignmentFlag.AlignCenter)
        section.content.addWidget(self.grade_region)
        section.content.addLayout(wheel_row)
        section.content.addWidget(self.grade_values)
        self.grade_lum_pages = QStackedWidget()
        for grade in colours.GRADES:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.addLayout(self._slider_grid(((f"grade_{grade}_lum", "Luminance", _SIGNED, 1),)))
            self.grade_lum_pages.addWidget(page)
        section.content.addWidget(self.grade_lum_pages)
        section.content.addLayout(self._slider_grid((
            ("grade_blending", "Blending", (0, 100), 1),
            ("grade_balance", "Balance", _SIGNED, 1),
        )))
        self._section_keys[section] = [*colours.GRADE_KEYS, "grade_blending", "grade_balance"]

        # Black and white
        section = self._section(layout, "Black & White")
        self._toggles["bw"] = self._toggle("Black and white", "bw")
        section.content.addWidget(self._toggles["bw"])
        section.content.addLayout(self._slider_grid([(f"bw_{c}", c.capitalize(), _SIGNED, 1) for c in colours.COLOURS]))
        self._section_keys[section] = ["bw", *colours.BW_KEYS]

        # Detail
        section = self._section(layout, "Detail")
        section.content.addLayout(self._slider_grid(_DETAIL))
        sharpen_title = QLabel("SHARPENING")
        sharpen_title.setObjectName("SectionTitle")
        section.content.addWidget(sharpen_title)
        section.content.addLayout(self._slider_grid(_SHARPEN))
        masking = self._sliders["sharpen_masking"][0]
        masking.sliderPressed.connect(self._masking_pressed)
        masking.sliderReleased.connect(lambda: self.view_changed.emit(""))
        sharpen_hint = QLabel("Hold ⌥ while dragging Masking to see what is sharpened (white).")
        sharpen_hint.setObjectName("Muted")
        sharpen_hint.setWordWrap(True)
        section.content.addWidget(sharpen_hint)
        noise_title = QLabel("NOISE REDUCTION")
        noise_title.setObjectName("SectionTitle")
        section.content.addWidget(noise_title)
        section.content.addLayout(self._slider_grid(_NOISE))
        self._section_keys[section] = [key for key, *_ in (*_DETAIL, *_SHARPEN, *_NOISE)]

        # Lens
        section = self._section(layout, "Lens Corrections")
        self._toggles["lens_profile"] = self._toggle("Use the lens profile", "lens_profile")
        section.content.addWidget(self._toggles["lens_profile"])
        self.lens_label = QLabel("")
        self.lens_label.setObjectName("Muted")
        self.lens_label.setWordWrap(True)
        section.content.addWidget(self.lens_label)
        section.content.addLayout(self._slider_grid(_LENS_PROFILE))
        manual = QLabel("BY HAND")
        manual.setObjectName("SectionTitle")
        section.content.addWidget(manual)
        section.content.addLayout(self._slider_grid(_LENS_MANUAL))
        lens_folder = self._button("Lens Profiles Folder…", self.lens_folder_requested)
        lens_folder.setToolTip("The lensfun database's folder (its .xml files)")
        section.content.addWidget(lens_folder)
        self._section_keys[section] = ["lens_profile", *(key for key, *_ in (*_LENS_PROFILE, *_LENS_MANUAL))]

        # Camera profile
        section = self._section(layout, "Profile")
        self.profile_combo = _Combo()
        self.profile_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.profile_combo.currentIndexChanged.connect(self._on_profile)
        shrinkable(self.profile_combo)
        self.set_profiles([])
        section.content.addWidget(self.profile_combo)
        profile_row = QHBoxLayout()
        profile_row.setSpacing(6)
        profile_folder = self._button("Folder…", self.profile_folder_requested)
        profile_folder.setToolTip("A folder of DCP colour profiles (.dcp), such as Adobe's Camera Matching ones")
        self.profile_default = self._button("Default for Camera", self.profile_default_requested)
        self.profile_default.setToolTip("Use this profile for every RAW of this camera that has no profile of its own")
        profile_row.addWidget(shrinkable(profile_folder))
        profile_row.addWidget(shrinkable(self.profile_default))
        section.content.addLayout(profile_row)
        self.profile_note = QLabel("Profiles change how a RAW's colours are rendered.")
        self.profile_note.setObjectName("Muted")
        self.profile_note.setWordWrap(True)
        section.content.addWidget(self.profile_note)
        self._section_keys[section] = ["profile"]

        layout.addWidget(self._masks_section())
        layout.addWidget(self._spots_section())
        layout.addWidget(self._history_section())
        layout.addStretch()
        return content

    def _masks_section(self):
        section = Section("Masks")
        self._sections.append(section)
        self._section_keys[section] = ["masks"]
        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self.mask_add = QPushButton("Add…")
        self.mask_add.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        menu = QMenu(self)
        for kind, label in MASK_KINDS:
            menu.addAction(label).triggered.connect(lambda _checked, kind=kind: self._add_mask(kind))
        self.mask_add.setMenu(menu)
        self.mask_mode = _Combo()
        self.mask_mode.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for mode, label in MASK_MODES:
            self.mask_mode.addItem(label, mode)
        self.mask_mode.setToolTip("Whether what you draw next makes a new mask or changes the selected one")
        add_row.addWidget(shrinkable(self.mask_add), 1)
        add_row.addWidget(shrinkable(self.mask_mode), 2)
        section.content.addLayout(add_row)
        self.mask_list = QListWidget()
        self.mask_list.setFixedHeight(96)
        self.mask_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mask_list.currentRowChanged.connect(self._on_mask_row)
        section.content.addWidget(self.mask_list)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.mask_overlay = QPushButton("Show")
        self.mask_overlay.setCheckable(True)
        self.mask_overlay.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mask_overlay.setToolTip("Show the selected mask in red over the photo")
        self.mask_overlay.toggled.connect(self.mask_overlay_toggled)
        self.mask_invert = QPushButton("Invert")
        self.mask_invert.setCheckable(True)
        self.mask_invert.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mask_invert.clicked.connect(self._on_mask_invert)
        delete = QPushButton("Delete")
        delete.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        delete.clicked.connect(self._delete_mask)
        for widget in (self.mask_overlay, self.mask_invert, delete):
            actions.addWidget(shrinkable(widget))
        section.content.addLayout(actions)
        self.brush_box = QWidget()
        brush = QVBoxLayout(self.brush_box)
        brush.setContentsMargins(0, 0, 0, 0)
        grid, self.brush_sliders = self._slider_grid(
            (("brush_size", "Brush size", (2, 300), 1000), ("brush_feather", "Feather", (0, 100), 1),
             ("brush_flow", "Flow", (1, 100), 1)),
            store=False, on_change=self._on_brush_slider, on_release=lambda: None,
        )
        for key, value in (("brush_size", 30), ("brush_feather", 50), ("brush_flow", 100)):
            self.brush_sliders[key][0].setValue(value)
            self.brush_sliders[key][0].default = value
        brush.addLayout(grid)
        self.brush_erase = QCheckBox("Erase (or hold ⌥)")
        self.brush_erase.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        brush.addWidget(self.brush_erase)
        self.brush_box.hide()
        section.content.addWidget(self.brush_box)
        self.mask_settings_box = QWidget()
        box = QVBoxLayout(self.mask_settings_box)
        box.setContentsMargins(0, 0, 0, 0)
        grid, self.mask_sliders = self._slider_grid(
            _MASK_SLIDERS, store=False, on_change=self._on_mask_slider, on_release=self._commit
        )
        box.addLayout(grid)
        self.mask_settings_box.setEnabled(False)
        section.content.addWidget(self.mask_settings_box)
        return section

    def _spots_section(self):
        section = Section("Spot Removal")
        self._sections.append(section)
        self._section_keys[section] = ["spots"]
        row = QHBoxLayout()
        row.setSpacing(6)
        self.spots_button = self._tool_button("Remove Spots", "spots", "Click a spot, or paint over a blemish (⇧Q)")
        self.spot_mode = _Combo()
        self.spot_mode.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.spot_mode.addItem("Heal", "heal")
        self.spot_mode.addItem("Clone", "clone")
        row.addWidget(shrinkable(self.spots_button), 2)
        row.addWidget(shrinkable(self.spot_mode), 1)
        section.content.addLayout(row)
        grid, self.spot_sliders = self._slider_grid(
            (("spot_size", "Size", (2, 150), 1000),), store=False, on_change=self._on_brush_slider, on_release=lambda: None
        )
        self.spot_sliders["spot_size"][0].setValue(15)
        self.spot_sliders["spot_size"][0].default = 15
        self._on_brush_slider("spot_size")
        section.content.addLayout(grid)
        self.spots_view = QCheckBox("Visualise spots")
        self.spots_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.spots_view.setToolTip("Show the photo's edges in black and white, where sensor dust stands out")
        self.spots_view.toggled.connect(lambda on: self.view_changed.emit("spots" if on else ""))
        section.content.addWidget(self.spots_view)
        clear_row = QHBoxLayout()
        self.spots_count = QLabel("No spot removed")
        self.spots_count.setObjectName("Muted")
        clear = QPushButton("Clear")
        clear.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        clear.clicked.connect(self._clear_spots)
        clear_row.addWidget(self.spots_count, 1)
        clear_row.addWidget(clear)
        section.content.addLayout(clear_row)
        hint = QLabel("Drag a spot's source circle to move it; ⌥-click a spot to remove it.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        section.content.addWidget(hint)
        return section

    def _history_section(self):
        section = Section("History & Snapshots")
        self._sections.append(section)
        self.history_list = QListWidget()
        self.history_list.setFixedHeight(130)
        self.history_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.history_list.itemClicked.connect(lambda item: self.history_chosen.emit(item.data(Qt.ItemDataRole.UserRole)))
        section.content.addWidget(self.history_list)
        snapshots_title = QLabel("SNAPSHOTS")
        snapshots_title.setObjectName("SectionTitle")
        section.content.addWidget(snapshots_title)
        self.snapshot_list = QListWidget()
        self.snapshot_list.setFixedHeight(80)
        self.snapshot_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.snapshot_list.itemClicked.connect(lambda item: self.snapshot_chosen.emit(item.text()))
        section.content.addWidget(self.snapshot_list)
        row = QHBoxLayout()
        row.setSpacing(6)
        save = self._button("Save Snapshot…", self.snapshot_save_requested)
        delete = QPushButton("Delete")
        delete.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        delete.clicked.connect(self._delete_snapshot)
        row.addWidget(save, 1)
        row.addWidget(delete)
        section.content.addLayout(row)
        return section

    def _raw_note(self):
        """What a RAW is edited from, and the switch of older edits to the current development."""
        box = QWidget()
        rows = QVBoxLayout(box)
        rows.setContentsMargins(0, 4, 0, 0)
        rows.setSpacing(6)
        self.raw_note = QLabel(
            "RAW: edits start from Tamis's own development of the file, not from the camera's JPEG "
            "that culling shows, so its colours can differ a little."
        )
        self.raw_note.setObjectName("Muted")
        self.raw_note.setWordWrap(True)
        self.process_note = QLabel(
            "This edit uses the earlier RAW development, which clipped the brightest highlights."
        )
        self.process_note.setObjectName("Muted")
        self.process_note.setWordWrap(True)
        self.process_button = QPushButton("Use the New Development")
        self.process_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.process_button.setToolTip(
            "Develop this RAW the current way, so its highlights can be brought back. "
            "Exposure is adjusted to keep it as bright (undoable)."
        )
        self.process_button.clicked.connect(self._update_process)
        for widget in (self.raw_note, self.process_note, self.process_button):
            rows.addWidget(widget)
        self._raw_box = box
        box.hide()
        return box

    def _button(self, text, signal):
        button = QPushButton(text)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(signal)
        return button

    def _tool_button(self, text, tool, tip):
        button = QPushButton(text)
        button.setCheckable(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setToolTip(tip)
        button.setProperty("tool", tool)
        button.clicked.connect(lambda checked: self._choose_tool(tool if checked else ""))
        return button

    def _toggle(self, text, key):
        box = QCheckBox(text)
        box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        box.toggled.connect(lambda _on: self._commit())
        return box

    def _tone_row(self):
        self.clipping_button = QPushButton("Clipping")
        self.clipping_button.setCheckable(True)
        self.clipping_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clipping_button.setToolTip("Show blown highlights in red, clipped colours in orange and blocked shadows in blue (J)")
        self.clipping_button.clicked.connect(self.clipping_toggled)
        self.before_button = QPushButton("Before")
        self.before_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.before_button.setToolTip("Hold to see the photo without its edit (or hold Y)")
        self.before_button.pressed.connect(lambda: self.before_held.emit(True))
        self.before_button.released.connect(lambda: self.before_held.emit(False))
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.clipping_button)
        row.addWidget(self.before_button)
        return row

    def _rotate_row(self):
        left = QPushButton("↺ 90°")
        left.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        left.setToolTip("Rotate left ([ or ⇧L)")
        left.clicked.connect(lambda: self.rotate_requested.emit(-90))
        right = QPushButton("↻ 90°")
        right.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        right.setToolTip("Rotate right (] or ⇧R)")
        right.clicked.connect(lambda: self.rotate_requested.emit(90))
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(left)
        row.addWidget(right)
        return row

    def _crop_row(self):
        self.crop_button = self._button("Crop", self.crop_requested)
        self.crop_button.setToolTip("Crop this photo (R)")
        self.crop_ratio = _Combo()
        self.crop_ratio.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.crop_ratio.setToolTip("Shape of the crop frame")
        for label, ratio in CROP_RATIOS:
            self.crop_ratio.addItem(label, ratio)
        self.crop_ratio.currentIndexChanged.connect(lambda _index: self.crop_ratio_changed.emit())
        clear = self._button("Clear", self.crop_cleared)
        clear.setToolTip("Back to the whole photo")
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.crop_button)
        row.addWidget(self.crop_ratio, 1)
        row.addWidget(clear)
        return row

    def _lut_row(self):
        self.lut_combo = _LutCombo()
        self.lut_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lut_combo.setMinimumWidth(0)
        self.lut_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.lut_combo.addItem("No film simulation", None)
        self.lut_combo.currentIndexChanged.connect(self._on_lut)
        folder = self._button("Folder…", self.lut_folder_requested)
        folder.setToolTip("Choose the folder of film simulation LUTs (HaldCLUT .png, .cube)")
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.lut_combo, 1)
        row.addWidget(folder)
        return row

    # -- state ---------------------------------------------------------------

    def set_presets(self, names):
        """Rebuild the Presets menu: the presets to apply, saving, and deleting."""
        menu = self.presets_menu
        menu.clear()
        for name in names:
            menu.addAction(name).triggered.connect(lambda _checked, name=name: self.preset_chosen.emit(name))
        if names:
            menu.addSeparator()
        menu.addAction("Save Current Look…").triggered.connect(lambda _checked: self.preset_save_requested.emit())
        if names:
            delete = menu.addMenu("Delete")
            for name in names:
                delete.addAction(name).triggered.connect(lambda _checked, name=name: self.preset_delete_requested.emit(name))

    def set_clipping(self, on):
        """Show whether clipping is on, without emitting `clipping_toggled`."""
        self.clipping_button.setChecked(on)

    def set_luts(self, entries):
        """`entries`: [(LUT id, display name)]."""
        current = self.lut_combo.currentData()
        self._updating = True
        self.lut_combo.clear()
        self.lut_combo.addItem("No film simulation" if entries else "No film simulation (choose a LUT folder…)", None)
        for lut_id, name in entries:
            self.lut_combo.addItem(name, lut_id)
        self._select_lut(current)
        self._updating = False

    def _select_lut(self, lut_id):
        index = self.lut_combo.findData(lut_id) if lut_id else 0
        if index < 0:  # stored with the photo, but not in the current LUT folder
            self.lut_combo.addItem(f"Missing: {lut_id}", lut_id)
            index = self.lut_combo.count() - 1
        self.lut_combo.setCurrentIndex(index)

    def set_profiles(self, entries):
        """`entries`: [(profile id, display name)] besides the built-in ones."""
        from . import profiles  # (imports rawpy, only when there is a panel)

        current = self._profile
        self._updating = True
        self.profile_combo.clear()
        for profile_id, name in (*profiles.BUILT_IN, *entries):
            self.profile_combo.addItem(name, profile_id)
        self._select_profile(current)
        self._updating = False

    def _select_profile(self, profile_id):
        index = self.profile_combo.findData(profile_id)
        if index < 0:
            self.profile_combo.addItem(f"Missing: {profile_id}", profile_id)
            index = self.profile_combo.count() - 1
        self.profile_combo.setCurrentIndex(index)

    def set_lens(self, text):
        """What the lens profile is: the lens found, or why there is none."""
        self.lens_label.setText(text)

    def set_edit(self, edit, raw=False):
        """Show `edit`, of a RAW if `raw`, without emitting `changed`."""
        values = develop.settings(edit)
        self._raw = raw
        self._crop = values["crop"]
        self._rotate = values["rotate"]
        self._process = edit.get("process", develop.PROCESS)
        self._curve = values["curve"]
        self._spots = values["spots"]
        self._profile = values["profile"]
        legacy = raw and self._process < develop.PROCESS
        self._raw_box.setVisible(raw)
        self.process_note.setVisible(legacy)
        self.process_button.setVisible(legacy)
        self._updating = True
        for key, (slider, scale) in self._sliders.items():
            value = values[key]
            if key in _AUTOMATIC:
                automatic = _AUTOMATIC[key][0 if raw else 1]
                slider.default = automatic
                value = automatic if value is None else value
            slider.setValue(round(value * scale))
            self._values[key].setText(_format(key, value))
        for key, box in self._toggles.items():
            box.setChecked(bool(values[key]))
        self._wheels = {key: values[key] for key in colours.GRADE_KEYS if not key.endswith("_lum")}
        self._select_lut(values["lut"])
        self._select_profile(values["profile"])
        self.profile_combo.setEnabled(raw)
        self.profile_default.setEnabled(raw)
        self._set_masks(values["masks"])
        self._updating = False
        self._show_curve()
        self._show_grade()
        self._show_spots()
        self._show_dots(edit)

    def _show_dots(self, edit):
        """Mark the Advanced tab, and its sections, when they hold settings in use."""
        used = develop.advanced(edit)
        self.tabs.setTabText(1, "Advanced •" if used else "Advanced")
        for section, keys in self._section_keys.items():
            section.set_dot(any(key in edit for key in keys))

    def edit(self):
        values = {key: slider.value() / scale for key, (slider, scale) in self._sliders.items()}
        for key, (raw_default, other_default) in _AUTOMATIC.items():
            if values[key] == (raw_default if self._raw else other_default):
                values[key] = None  # left to the photo's kind
        for key, box in self._toggles.items():
            values[key] = int(box.isChecked())
        values.update(self._wheels)
        values["lut"] = self.lut_combo.currentData()
        values["crop"] = self._crop
        values["rotate"] = self._rotate
        values["process"] = self._process
        values["curve"] = self._curve
        values["masks"] = self._masks
        values["spots"] = self._spots
        values["profile"] = self.profile_combo.currentData()
        return develop.clean(values)

    def set_cropping(self, cropping):
        self.crop_button.setText("Done" if cropping else "Crop")
        self.crop_button.setObjectName("Primary" if cropping else "")
        self.crop_button.style().unpolish(self.crop_button)
        self.crop_button.style().polish(self.crop_button)

    def crop_pixel_ratio(self):
        """The chosen frame shape: width / height in pixels, "original", or None when free."""
        return self.crop_ratio.currentData()

    def set_crop_free(self):
        """Choose the free shape, without emitting `crop_ratio_changed`."""
        blocked = self.crop_ratio.blockSignals(True)
        self.crop_ratio.setCurrentIndex(0)
        self.crop_ratio.blockSignals(blocked)

    # -- tools ---------------------------------------------------------------

    def _tool_buttons(self):
        return [self.wb_button, self.target_button, self.spots_button]

    def _choose_tool(self, tool):
        for button in self._tool_buttons():
            button.setChecked(button.property("tool") == tool and bool(tool))
        self.brush_box.setVisible(tool == "brush")
        self.tool_requested.emit(tool)

    def set_tool(self, tool):
        """Show `tool` as the one in use ("" for none), without emitting `tool_requested`."""
        for button in self._tool_buttons():
            button.setChecked(button.property("tool") == tool and bool(tool))
        self.brush_box.setVisible(tool == "brush")

    def stop_tool(self):
        if any(button.isChecked() for button in self._tool_buttons()) or self.brush_box.isVisible():
            self._choose_tool("")

    def mixer_part(self):
        """What the target tool changes: "hue", "sat" or "lum"."""
        return ("hue", "sat", "lum")[max(0, self.mixer_mode.index())]

    def brush(self):
        """(size as a fraction of the long edge, feather, flow, erase) of the brush."""
        size, feather, flow = (self.brush_sliders[key][0].value() for key in ("brush_size", "brush_feather", "brush_flow"))
        erase = self.brush_erase.isChecked() or bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier)
        return size / 1000, feather, flow, erase

    def spot_settings(self):
        """(radius as a fraction of the long edge, mode) of new spots."""
        return self.spot_sliders["spot_size"][0].value() / 1000, self.spot_mode.currentData()

    def _on_brush_slider(self, key):
        sliders = {**self.brush_sliders, **getattr(self, "spot_sliders", {})}
        slider, scale, label = sliders[key]
        value = slider.value()
        label.setText(f"{value / 10:.1f} %" if key in ("brush_size", "spot_size") else f"{value}")

    def _masking_pressed(self):
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier:
            self.view_changed.emit("sharpen_mask")

    # -- curves and wheels ---------------------------------------------------

    def _curve_channel(self):
        return curves.CHANNELS[max(0, self.curve_channel.index())]

    def _show_curve(self):
        channel = self._curve_channel()
        points = (self._curve or {}).get(channel)
        base = None
        if channel == "rgb":
            values = {key: self._sliders[key][0].value() for key in (*curves.PARAMETRIC, *curves.SPLITS)}
            if curves.is_parametric(values):
                base = curves.parametric_points(
                    [values[key] for key in curves.PARAMETRIC], [values[key] for key in curves.SPLITS]
                )
        self.curve_editor.set_curve(channel, points, base)

    def set_histogram_counts(self, counts):
        self.curve_editor.set_histogram(counts)

    def _on_curve(self, points, final):
        curve = dict(self._curve or {})
        curve[self._curve_channel()] = points
        self._curve = curves.clean(curve)
        if not self._updating:
            self.changed.emit(self.edit(), final)

    def _grade(self):
        return colours.GRADES[max(0, self.grade_region.index())]

    def _show_grade(self, _index=None):
        grade = self._grade()
        self.grade_lum_pages.setCurrentIndex(colours.GRADES.index(grade))
        hue, sat = self._wheels.get(f"grade_{grade}_hue", 0), self._wheels.get(f"grade_{grade}_sat", 0)
        self.grade_wheel.set_value(hue, sat)
        self.grade_values.setText(f"Hue {hue}° · Saturation {sat}")

    def _on_wheel(self, hue, sat, final):
        grade = self._grade()
        self._wheels[f"grade_{grade}_hue"] = hue
        self._wheels[f"grade_{grade}_sat"] = sat
        self.grade_values.setText(f"Hue {hue}° · Saturation {sat}")
        if not self._updating:
            self.changed.emit(self.edit(), final)

    # -- masks -----------------------------------------------------------------

    def masks(self):
        return [dict(mask) for mask in self._masks or ()]

    def mask_mode_choice(self):
        """"new", or how a new part joins the selected mask: "add", "subtract" or "intersect"."""
        mode = self.mask_mode.currentData()
        return mode if self.selected_mask >= 0 or mode == "new" else "new"

    def _set_masks(self, found):
        self._masks = [dict(mask) for mask in found] if found else None
        count = len(self._masks or ())
        if self.selected_mask >= count or (self.selected_mask < 0 and count):
            self.selected_mask = count - 1
        blocked = self.mask_list.blockSignals(True)
        self.mask_list.clear()
        for index, mask in enumerate(self._masks or ()):
            QListWidgetItem(mask_label(mask, index), self.mask_list)
        self.mask_list.setCurrentRow(self.selected_mask)
        self.mask_list.blockSignals(blocked)
        self._show_mask()

    def select_mask(self, index):
        """Select mask `index` (-1: none), without emitting `mask_selected`."""
        self.selected_mask = index
        blocked = self.mask_list.blockSignals(True)
        self.mask_list.setCurrentRow(index)
        self.mask_list.blockSignals(blocked)
        self._show_mask()

    def _show_mask(self):
        mask = self._masks[self.selected_mask] if self._masks and 0 <= self.selected_mask < len(self._masks) else None
        self.mask_settings_box.setEnabled(mask is not None)
        self.mask_invert.setEnabled(mask is not None)
        updating, self._updating = self._updating, True
        self.mask_invert.setChecked(bool(mask and mask.get("invert")))
        for key, (slider, scale, label) in self.mask_sliders.items():
            value = (mask or {}).get("settings", {}).get(key, 0)
            slider.setValue(round(value * scale))
            label.setText(_format(f"mask_{key}", value))
        self._updating = updating

    def _on_mask_row(self, row):
        self.selected_mask = row
        self._show_mask()
        self.mask_selected.emit(row)

    def _on_mask_slider(self, key):
        slider, scale, label = self.mask_sliders[key]
        value = slider.value() / scale
        label.setText(_format(f"mask_{key}", value if key == "exposure" else int(value)))
        if self._updating or not self._masks or not 0 <= self.selected_mask < len(self._masks):
            return
        mask = dict(self._masks[self.selected_mask])
        mask["settings"] = masks.clean_settings({**mask.get("settings", {}), key: value})
        self._masks[self.selected_mask] = mask
        self.changed.emit(self.edit(), not slider.isSliderDown())

    def _on_mask_invert(self, on):
        if self._masks and 0 <= self.selected_mask < len(self._masks):
            self._masks[self.selected_mask] = {**self._masks[self.selected_mask], "invert": on}
            self._commit()

    def _delete_mask(self):
        if self._masks and 0 <= self.selected_mask < len(self._masks):
            del self._masks[self.selected_mask]
            self.selected_mask = min(self.selected_mask, len(self._masks) - 1)
            self._commit()
            self.mask_selected.emit(self.selected_mask)

    def _add_mask(self, kind):
        if kind in masks.AUTOMATIC:
            self.stop_tool()
            self.automask_requested.emit(kind)
            return
        for button in self._tool_buttons():
            button.setChecked(False)
        self.brush_box.setVisible(kind == "brush")
        self.tool_requested.emit(kind)

    # -- spots and history -----------------------------------------------------

    def _show_spots(self):
        count = len(self._spots or ())
        self.spots_count.setText(f"{count} spot(s) removed" if count else "No spot removed")

    def _clear_spots(self):
        if self._spots:
            self._spots = None
            self._show_spots()
            self._commit()

    def set_history(self, entries, current):
        """`entries`: labels, oldest first; `current`: the index of the one the photo is at (-1 if none)."""
        self.history_list.clear()
        for index in range(len(entries) - 1, -1, -1):  # newest first
            item = QListWidgetItem(entries[index], self.history_list)
            item.setData(Qt.ItemDataRole.UserRole, index)
            if index == current:
                font = item.font()
                font.setBold(True)
                item.setFont(font)

    def set_snapshots(self, names):
        self.snapshot_list.clear()
        for name in names:
            QListWidgetItem(name, self.snapshot_list)

    def _delete_snapshot(self):
        item = self.snapshot_list.currentItem()
        if item is not None:
            self.snapshot_delete_requested.emit(item.text())

    # -- signals -------------------------------------------------------------

    def _on_slider(self, key):
        slider, scale = self._sliders[key]
        value = slider.value() / scale
        self._values[key].setText(_format(key, value if key in develop.FLOATS else int(value)))
        if key in (*curves.PARAMETRIC, *curves.SPLITS):
            self._show_curve()
        if not self._updating:
            # Dragging previews; a click on the track is a final change.
            self.changed.emit(self.edit(), not slider.isSliderDown())

    def _update_process(self):
        self.process_update_requested.emit()  # the app works out the exposure that keeps the photo as bright

    def _commit(self):
        if not self._updating:
            self.changed.emit(self.edit(), True)

    def _on_lut(self, _index):
        if not self._updating:
            self.changed.emit(self.edit(), True)

    def _on_profile(self, _index):
        self._profile = self.profile_combo.currentData()
        if not self._updating:
            self.changed.emit(self.edit(), True)
