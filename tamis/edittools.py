"""Tools that work on the photo in the Cull view: the white balance picker, the colour mixer's target,
gradients, the brush, mask handles and spot removal.

A tool gets the mouse in fractions of the framed photo shown (0–1 across and
down) and draws over it; what it does to the edit is left to the callbacks it
was given (see advancedui.py), so it holds no edit of its own.
"""
import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainterPath, QPen

HANDLE_REACH = 0.02  # fraction of the shown photo's width within which a handle is grabbed


def _point(rect, x, y):
    return QPointF(rect.x() + x * rect.width(), rect.y() + y * rect.height())


def _near(rect, a, b, reach_px=10):
    pa, pb = _point(rect, *a), _point(rect, *b)
    return math.hypot(pa.x() - pb.x(), pa.y() - pb.y()) <= reach_px


class Tool:
    cursor = Qt.CursorShape.CrossCursor
    handles_double_click = True

    def __init__(self):
        self.rect = QRectF(0, 0, 1, 1)  # where the photo was last drawn: to measure in pixels
        self.hover = None

    def press(self, x, y, modifiers):
        pass

    def move(self, x, y, modifiers, pressed):
        self.hover = (x, y)

    def release(self, x, y, modifiers):
        pass

    def leave(self):
        self.hover = None

    def paint(self, painter, rect):
        self.rect = rect


class PickTool(Tool):
    """Calls `picked(x, y)` on a click."""

    def __init__(self, picked):
        super().__init__()
        self.picked = picked

    def press(self, x, y, modifiers):
        if 0 <= x <= 1 and 0 <= y <= 1:
            self.picked(x, y)


class TargetTool(Tool):
    """Drag up or down: `dragged(x, y, distance, final)`, distance in pixels, up positive, from where it was pressed."""

    cursor = Qt.CursorShape.SizeVerCursor

    def __init__(self, dragged):
        super().__init__()
        self.dragged = dragged
        self.start = None

    def press(self, x, y, modifiers):
        if 0 <= x <= 1 and 0 <= y <= 1:
            self.start = (x, y)
            self.dragged(x, y, 0.0, False)

    def move(self, x, y, modifiers, pressed):
        super().move(x, y, modifiers, pressed)
        if self.start is not None and pressed:
            self.dragged(*self.start, (self.start[1] - y) * self.rect.height(), False)

    def release(self, x, y, modifiers):
        if self.start is not None:
            self.dragged(*self.start, (self.start[1] - y) * self.rect.height(), True)
            self.start = None


class MaskTool(Tool):
    """Draws a linear or radial gradient (`kind`), or moves the handles of the selected mask.

    `handles()` gives [(x, y, key)]; `moved(key, x, y, final)` moves one; `drawn(kind, start, end)` adds a gradient,
    `outlines()` gives what to draw of the selected mask: [("line", a, b) | ("ellipse", centre, rx, ry, angle)].
    """

    def __init__(self, kind, handles, moved, drawn, outlines):
        super().__init__()
        self.kind = kind
        self.handles, self.moved, self.drawn, self.outlines = handles, moved, drawn, outlines
        self.start = self.end = None
        self.dragging = None  # key of the handle being dragged

    def press(self, x, y, modifiers):
        for hx, hy, key in self.handles():
            if _near(self.rect, (x, y), (hx, hy)):
                self.dragging = key
                return
        if self.kind in ("linear", "radial"):
            self.start = self.end = (x, y)

    def move(self, x, y, modifiers, pressed):
        super().move(x, y, modifiers, pressed)
        if not pressed:
            return
        if self.dragging is not None:
            self.moved(self.dragging, x, y, False)
        elif self.start is not None:
            self.end = (x, y)

    def release(self, x, y, modifiers):
        if self.dragging is not None:
            self.moved(self.dragging, x, y, True)
            self.dragging = None
        elif self.start is not None:
            start, self.start, self.end = self.start, None, None
            if not _near(self.rect, start, (x, y), 4):
                self.drawn(self.kind, start, (x, y))

    def paint(self, painter, rect):
        super().paint(painter, rect)
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        for shape in self.outlines():
            self._draw(painter, rect, shape, QColor(255, 255, 255, 200))
        if self.start is not None and self.end is not None:
            if self.kind == "linear":
                shape = ("line", self.start, self.end)
            else:
                (cx, cy), (ex, ey) = self.start, self.end
                shape = ("ellipse", self.start, abs(ex - cx), abs(ey - cy), 0.0)
            self._draw(painter, rect, shape, QColor(255, 200, 0, 230))
        for hx, hy, _key in self.handles():
            painter.setPen(QPen(QColor("#000000"), 1.5))
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(_point(rect, hx, hy), 5, 5)

    @staticmethod
    def _draw(painter, rect, shape, color):
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if shape[0] == "line":
            a, b = _point(rect, *shape[1]), _point(rect, *shape[2])
            painter.setPen(QPen(color, 1.5))
            painter.drawLine(a, b)
            # The two ends of the gradient, across it: full effect, then none.
            dx, dy = b.x() - a.x(), b.y() - a.y()
            length = math.hypot(dx, dy) or 1
            nx, ny = -dy / length * 2000, dx / length * 2000
            painter.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
            for p in (a, b):
                painter.drawLine(QPointF(p.x() - nx, p.y() - ny), QPointF(p.x() + nx, p.y() + ny))
        else:
            _kind, centre, rx, ry, angle = shape
            c = _point(rect, *centre)
            painter.setPen(QPen(color, 1.5))
            painter.save()
            painter.translate(c)
            painter.rotate(angle)
            painter.drawEllipse(QPointF(0, 0), rx * rect.width(), ry * rect.height())
            painter.restore()


class BrushTool(Tool):
    """Paints strokes: `stroked(points, final)` with the points in fractions; `size()` is the brush radius as a
    fraction of the shown photo's width, and `outlines` draws the selected mask like MaskTool."""

    cursor = Qt.CursorShape.BlankCursor
    handles_double_click = True

    def __init__(self, stroked, size, erasing):
        super().__init__()
        self.stroked, self.size, self.erasing = stroked, size, erasing
        self.points = []

    def press(self, x, y, modifiers):
        self.points = [(x, y)]
        self.stroked(list(self.points), False)

    def move(self, x, y, modifiers, pressed):
        super().move(x, y, modifiers, pressed)
        if pressed and self.points:
            last = self.points[-1]
            if math.hypot((x - last[0]) * self.rect.width(), (y - last[1]) * self.rect.height()) >= 2:
                self.points.append((x, y))
                self.stroked(list(self.points), False)

    def release(self, x, y, modifiers):
        if self.points:
            points, self.points = self.points, []
            self.stroked(points, True)

    def paint(self, painter, rect):
        super().paint(painter, rect)
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        radius = self.size() * rect.width()
        if len(self.points) > 1:
            path = QPainterPath(_point(rect, *self.points[0]))
            for p in self.points[1:]:
                path.lineTo(_point(rect, *p))
            painter.setPen(QPen(QColor(255, 80, 80, 90), radius * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPath(path)
        if self.hover is not None:
            centre = _point(rect, *self.hover)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 160), 2.5))
            painter.drawEllipse(centre, radius, radius)
            painter.setPen(QPen(QColor(255, 120, 120) if self.erasing() else QColor(255, 255, 255), 1.2))
            painter.drawEllipse(centre, radius, radius)
            if self.erasing():
                painter.drawLine(QPointF(centre.x() - 4, centre.y()), QPointF(centre.x() + 4, centre.y()))


class SpotTool(BrushTool):
    """Click a spot or paint a blemish: `stroked(points, True)` adds one. Existing spots, from `spots()` as
    [(points, source points, radius as a fraction of the shown width)], show with their source; dragging a
    source moves it (`moved(index, dx, dy, final)`), ⌥-click removes a spot (`removed(index)`)."""

    def __init__(self, stroked, size, spots, moved, removed):
        super().__init__(stroked, size, lambda: False)
        self.spots, self.moved, self.removed = spots, moved, removed
        self.dragging = None  # (index, where the drag started)

    def _hit(self, x, y, which):
        for index, (points, sources, radius) in enumerate(self.spots()):
            for px, py in (sources if which == "source" else points):
                if math.hypot((x - px) * self.rect.width(), (y - py) * self.rect.height()) <= max(6, radius * self.rect.width()):
                    return index
        return None

    def press(self, x, y, modifiers):
        if modifiers & Qt.KeyboardModifier.AltModifier:
            index = self._hit(x, y, "spot")
            if index is not None:
                self.removed(index)
            return
        index = self._hit(x, y, "source")
        if index is not None:
            self.dragging = (index, (x, y))
            return
        super().press(x, y, modifiers)

    def move(self, x, y, modifiers, pressed):
        if self.dragging is not None:
            self.hover = (x, y)
            if pressed:
                index, (sx, sy) = self.dragging
                self.moved(index, x - sx, y - sy, False)
            return
        Tool.move(self, x, y, modifiers, pressed)
        if pressed and self.points:
            last = self.points[-1]
            if math.hypot((x - last[0]) * self.rect.width(), (y - last[1]) * self.rect.height()) >= 2:
                self.points.append((x, y))

    def release(self, x, y, modifiers):
        if self.dragging is not None:
            index, (sx, sy) = self.dragging
            self.dragging = None
            self.moved(index, x - sx, y - sy, True)
            return
        super().release(x, y, modifiers)

    def paint(self, painter, rect):
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        for points, sources, radius in self.spots():
            r = radius * rect.width()
            for shape, color, style in ((points, QColor(255, 255, 255, 220), Qt.PenStyle.SolidLine),
                                        (sources, QColor(255, 210, 0, 220), Qt.PenStyle.DashLine)):
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(color, 1.3, style))
                if len(shape) == 1:
                    painter.drawEllipse(_point(rect, *shape[0]), r, r)
                else:
                    path = QPainterPath(_point(rect, *shape[0]))
                    for p in shape[1:]:
                        path.lineTo(_point(rect, *p))
                    painter.drawPath(path)
            painter.setPen(QPen(QColor(255, 255, 255, 140), 1))
            painter.drawLine(_point(rect, *sources[0]), _point(rect, *points[0]))
        super().paint(painter, rect)
