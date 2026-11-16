import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from tamis import watermark  # noqa: E402
from tamis.exportsettings import ExportSettings  # noqa: E402

app = QApplication.instance() or QApplication([])


def black(width=400, height=200, fmt=QImage.Format.Format_RGB888):
    image = QImage(width, height, fmt)
    image.fill(QColor(0, 0, 0))
    return image


def brightness(image, x0, y0, x1, y1):
    return sum(image.pixelColor(x, y).lightness() for x in range(x0, x1, 4) for y in range(y0, y1, 4))


def test_text_lands_in_the_corner_asked_for():
    settings = ExportSettings(watermark_text="© Tamis", watermark_position="bottom_right", watermark_opacity=100)
    image = watermark.apply(black(), settings)
    assert brightness(image, 200, 100, 400, 200) > 0
    assert brightness(image, 0, 0, 200, 100) == 0


@pytest.mark.parametrize("fmt", [QImage.Format.Format_RGB888, QImage.Format.Format_RGBX64])
def test_a_logo_is_sized_on_the_long_edge(tmp_path, fmt):
    logo = QImage(100, 50, QImage.Format.Format_ARGB32)
    logo.fill(QColor(255, 255, 255))
    logo.save(str(tmp_path / "logo.png"))
    settings = ExportSettings(watermark_image=str(tmp_path / "logo.png"), watermark_position="top_left",
                              watermark_size=25, watermark_margin=0, watermark_opacity=100)
    image = watermark.apply(black(fmt=fmt), settings)
    assert image.pixelColor(10, 10).lightness() == 255  # 100×50 px: a quarter of the 400 px long edge
    assert image.pixelColor(110, 10).lightness() == 0
    assert image.pixelColor(10, 60).lightness() == 0


def test_no_watermark_leaves_the_image_alone():
    image = black()
    assert watermark.apply(image, ExportSettings()) is image


def test_a_missing_logo_is_an_error(tmp_path):
    with pytest.raises(watermark.WatermarkError):
        watermark.load_logo(str(tmp_path / "nothing.png"))
