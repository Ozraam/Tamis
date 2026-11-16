"""Watermarks on exports: a PNG logo or a line of text, painted on the finished picture.

They are painted after resizing, on the assembled output, and sized and
placed in fractions of the long edge, so a watermark looks the same on a
web export as on a full-size one. Painting on a QImage is safe off the GUI thread.
"""
import os

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter


class WatermarkError(Exception):
    pass


def _target(image, width, height, settings):
    """Where a watermark of that size goes on `image`, as a QRectF."""
    long_edge = max(image.width(), image.height())
    margin = long_edge * settings.watermark_margin / 100
    position = settings.watermark_position
    x = margin if position.endswith("left") else image.width() - margin - width
    y = margin if position.startswith("top") else image.height() - margin - height
    if position == "center":
        x, y = (image.width() - width) / 2, (image.height() - height) / 2
    return QRectF(x, y, width, height)


def load_logo(path):
    """The logo, or WatermarkError if it can't be read."""
    path = os.path.expanduser(path)
    logo = QImage(path)
    if logo.isNull():
        raise WatermarkError(f"the watermark {os.path.basename(path) or path} can't be read")
    return logo


def apply(image, settings, logo=None):
    """`image` with the watermark of `settings` painted on it (the same QImage, changed in place).

    `logo` is the loaded logo, to read it only once for a whole export.
    """
    if not settings.watermarks:
        return image
    long_edge = max(image.width(), image.height())
    width = long_edge * settings.watermark_size / 100
    if image.format() in (QImage.Format.Format_RGB888, QImage.Format.Format_Indexed8, QImage.Format.Format_Grayscale8):
        image = image.convertToFormat(QImage.Format.Format_RGB32)  # what QPainter blends fastest at 8 bits
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(settings.watermark_opacity / 100)
        if settings.watermark_image:
            logo = logo if logo is not None else load_logo(settings.watermark_image)
            height = width * logo.height() / max(1, logo.width())
            painter.drawImage(_target(image, width, height, settings), logo)
        else:
            _draw_text(painter, image, settings.watermark_text, width, settings)
    finally:
        painter.end()
    return image


def _draw_text(painter, image, text, width, settings):
    font = QFont()
    font.setPixelSize(100)
    font.setWeight(QFont.Weight.DemiBold)
    metrics = QFontMetricsF(font)
    natural = metrics.horizontalAdvance(text)
    if natural <= 0:
        return
    font.setPixelSize(max(4, round(100 * width / natural)))
    metrics = QFontMetricsF(font)
    width, height = metrics.horizontalAdvance(text), metrics.height()
    box = _target(image, width, height, settings)
    baseline = QPointF(box.left(), box.top() + metrics.ascent())
    painter.setFont(font)
    shadow = max(1.0, height / 30)
    painter.setPen(QColor(0, 0, 0, 110))
    painter.drawText(baseline + QPointF(shadow, shadow), text)
    painter.setPen(QColor(Qt.GlobalColor.white))
    painter.drawText(baseline, text)
