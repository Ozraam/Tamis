"""Widgets of the edit panel's Advanced tab: collapsible sections, segmented choices, the tone
curve editor and the colour wheel of colour grading."""
import math

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QConicalGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from . import curves
from .constants import COLOR_BORDER


def shrinkable(widget):
    """Let `widget` be narrower than its text, so a row of them fits the panel."""
    widget.setMinimumWidth(10)
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    return widget


class Section(QWidget):
    """A titled block that folds away; `dot` marks one that holds settings in use."""

    toggled = pyqtSignal(bool)

    def __init__(self, title, expanded=False, parent=None):
        super().__init__(parent)
        self._title = title
        self.header = QToolButton()
        self.header.setObjectName("SectionHeader")
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.toggled.connect(self._on_toggled)
        self.body = QWidget()
        self.content = QVBoxLayout(self.body)
        self.content.setContentsMargins(0, 2, 0, 6)
        self.content.setSpacing(6)
        self.body.setVisible(expanded)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.header)
        layout.addWidget(self.body)
        self.set_dot(False)

    def set_dot(self, on):
        title = self._title.upper().replace("&", "&&")
        self.header.setText(f"{title}  •" if on else title)

    def expanded(self):
        return self.header.isChecked()

    def set_expanded(self, on):
        self.header.setChecked(on)

    def _on_toggled(self, on):
        self.header.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self.body.setVisible(on)
        self.toggled.emit(on)


class Segmented(QWidget):
    """A row of buttons of which one is chosen."""

    changed = pyqtSignal(int)

    def __init__(self, labels, parent=None):
        super().__init__(parent)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for index, label in enumerate(labels):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setChecked(index == 0)
            shrinkable(button)
            self.group.addButton(button, index)
            row.addWidget(button)
        self.group.idClicked.connect(self.changed)

    def index(self):
        return self.group.checkedId()

    def set_index(self, index):
        self.group.button(index).setChecked(True)

    def button(self, index):
        return self.group.button(index)


class CurveEditor(QWidget):
    """The point curve of one channel, over the histogram. Click to add a point, drag to move it,
    double-click a point (or drag it off the side) to remove it.

    `changed(points, final)`: `final` once the mouse is let go.
    """

    changed = pyqtSignal(list, bool)

    PADDING = 6
    REACH = 9  # pixels around a point that grab it
    COLORS = {"rgb": "#e8e8e8", "red": "#e05050", "green": "#50c060", "blue": "#5080e0"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(190)
        self.setMouseTracking(True)
        self.channel = "rgb"
        self.points = [[0.0, 0.0], [1.0, 1.0]]
        self.base = None  # the parametric curve, drawn under the points (list of points), or None
        self.counts = None  # histogram (3×256), or None
        self._drag = None  # index of the point being dragged

    def set_curve(self, channel, points, base=None):
        self.channel = channel
        self.points = [list(p) for p in points] if points else [[0.0, 0.0], [1.0, 1.0]]
        self.base = base
        self._drag = None
        self.update()

    def set_histogram(self, counts):
        self.counts = counts
        self.update()

    def _area(self):
        p = self.PADDING
        return QRectF(p, p, self.width() - 2 * p, self.height() - 2 * p)

    def _to_widget(self, x, y):
        area = self._area()
        return QPointF(area.left() + x * area.width(), area.bottom() - y * area.height())

    def _from_widget(self, position):
        area = self._area()
        x = (position.x() - area.left()) / area.width()
        y = (area.bottom() - position.y()) / area.height()
        return min(1.0, max(0.0, x)), min(1.0, max(0.0, y))

    def _point_at(self, position):
        for index, (x, y) in enumerate(self.points):
            spot = self._to_widget(x, y)
            if abs(spot.x() - position.x()) <= self.REACH and abs(spot.y() - position.y()) <= self.REACH:
                return index
        return None

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        index = self._point_at(event.position())
        if index is None:
            x, y = self._from_widget(event.position())
            if any(abs(x - px) < 0.02 for px, _ in self.points):
                return
            # Added on the curve where it is, then dragged from there.
            y = float(curves.interpolate(self.points, np.array([x]))[0]) if len(self.points) >= 2 else y
            self.points.append([x, y])
            self.points.sort()
            index = next(i for i, p in enumerate(self.points) if p[0] == x)
            self.changed.emit(self._clean(), False)
        self._drag = index
        self.update()

    def mouseMoveEvent(self, event):
        if self._drag is None:
            over = self._point_at(event.position()) is not None
            self.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.CrossCursor)
            return
        x, y = self._from_widget(event.position())
        index = self._drag
        if 0 < index < len(self.points) - 1:
            x = min(self.points[index + 1][0] - 0.01, max(self.points[index - 1][0] + 0.01, x))
        elif index == 0:
            x = min(x, self.points[1][0] - 0.01)
        else:
            x = max(x, self.points[-2][0] + 0.01)
        self.points[index] = [x, y]
        self.changed.emit(self._clean(), False)
        self.update()

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            index = self._drag
            self._drag = None
            area = self._area()
            off = event.position().y() < area.top() - 20 or event.position().y() > area.bottom() + 20
            if off and 0 < index < len(self.points) - 1:
                del self.points[index]
            self.changed.emit(self._clean(), True)
            self.update()

    def mouseDoubleClickEvent(self, event):
        index = self._point_at(event.position())
        if index is not None and 0 < index < len(self.points) - 1:
            del self.points[index]
        elif index is None:
            self.points = [[0.0, 0.0], [1.0, 1.0]]  # double-click elsewhere: back to straight
        self._drag = None
        self.changed.emit(self._clean(), True)
        self.update()

    def _clean(self):
        return [[round(x, 4), round(y, 4)] for x, y in self.points]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#111111"))
        area = self._area()
        if self.counts is not None:
            channel = {"red": 0, "green": 1, "blue": 2}.get(self.channel)
            counts = self.counts.sum(axis=0) if channel is None else self.counts[channel]
            peak = max(1, int(counts[2:-2].max()))
            path = QPainterPath(QPointF(area.left(), area.bottom()))
            for i, value in enumerate(np.minimum(counts / peak, 1.0)):
                path.lineTo(area.left() + i / 255 * area.width(), area.bottom() - float(value) * area.height())
            path.lineTo(area.right(), area.bottom())
            painter.fillPath(path, QColor(255, 255, 255, 36))
        painter.setPen(QPen(QColor("#333333"), 1))
        for i in range(1, 4):
            x, y = area.left() + area.width() * i / 4, area.top() + area.height() * i / 4
            painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        painter.drawLine(area.bottomLeft(), area.topRight())
        xs = np.linspace(0, 1, 129)
        if self.base:
            painter.setPen(QPen(QColor("#777777"), 1, Qt.PenStyle.DashLine))
            self._draw(painter, xs, curves.interpolate(self.base, xs))
        painter.setPen(QPen(QColor(self.COLORS.get(self.channel, "#e8e8e8")), 1.6))
        self._draw(painter, xs, curves.interpolate(self.points, xs))
        painter.setBrush(QColor(self.COLORS.get(self.channel, "#e8e8e8")))
        for x, y in self.points:
            painter.drawEllipse(self._to_widget(x, y), 3.5, 3.5)
        painter.setPen(QColor(COLOR_BORDER))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))

    def _draw(self, painter, xs, ys):
        path = QPainterPath(self._to_widget(xs[0], float(np.clip(ys[0], 0, 1))))
        for x, y in zip(xs[1:], ys[1:], strict=False):
            path.lineTo(self._to_widget(float(x), float(np.clip(y, 0, 1))))
        painter.drawPath(path)


class ColourWheel(QWidget):
    """Hue around, strength outward. `changed(hue, saturation, final)`: degrees, 0–100.

    Double-click resets it; holding Shift while dragging keeps the hue and changes only the strength.
    """

    changed = pyqtSignal(int, int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(150, 150)
        self.hue, self.saturation = 0, 0
        self._dragging = False

    def set_value(self, hue, saturation):
        self.hue, self.saturation = int(hue), int(saturation)
        self.update()

    def _geometry(self):
        radius = min(self.width(), self.height()) / 2 - 6
        return QPointF(self.width() / 2, self.height() / 2), radius

    def _set_from(self, position, final, keep_hue=False):
        centre, radius = self._geometry()
        dx, dy = position.x() - centre.x(), centre.y() - position.y()
        saturation = round(min(1.0, math.hypot(dx, dy) / radius) * 100)
        hue = self.hue if keep_hue else round(math.degrees(math.atan2(dy, dx))) % 360
        self.hue, self.saturation = hue, saturation
        self.changed.emit(hue, saturation, final)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._set_from(event.position(), False, bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._set_from(event.position(), False, bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self._set_from(event.position(), True, bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))

    def mouseDoubleClickEvent(self, event):
        self._dragging = False
        self.hue, self.saturation = 0, 0
        self.changed.emit(0, 0, True)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        centre, radius = self._geometry()
        hues = QConicalGradient(centre, 0)
        for i in range(13):
            # Qt's conical gradient turns counter-clockwise from 3 o'clock, like the angle used here.
            hues.setColorAt(i / 12, QColor.fromHsvF((i / 12) % 1.0, 0.75, 0.85))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(hues))
        painter.drawEllipse(centre, radius, radius)
        fade = QRadialGradient(centre, radius)
        fade.setColorAt(0, QColor(128, 128, 128, 255))
        fade.setColorAt(1, QColor(128, 128, 128, 0))
        painter.setBrush(QBrush(fade))
        painter.drawEllipse(centre, radius, radius)
        painter.setPen(QPen(QColor(COLOR_BORDER), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(centre, radius, radius)
        angle = math.radians(self.hue)
        distance = self.saturation / 100 * radius
        knob = QPointF(centre.x() + math.cos(angle) * distance, centre.y() - math.sin(angle) * distance)
        painter.setPen(QPen(QColor("#000000"), 2))
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(knob, 5, 5)
