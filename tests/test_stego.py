import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PyQt6.QtCore import QBuffer, QIODevice  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from tamis import editing, stego  # noqa: E402
from tamis.imaging import array_to_image, image_to_array  # noqa: E402

app = QApplication.instance() or QApplication([])


def photo(height=400, width=600, seed=1):
    y, x = np.mgrid[0:height, 0:width]
    base = np.stack([128 + 80 * np.sin(x / 50), 128 + 60 * np.cos(y / 40), 100 + 50 * np.sin((x + y) / 70)], -1)
    return np.clip(base + np.random.default_rng(seed).normal(0, 6, (height, width, 3)), 0, 255)


def as_jpeg(pixels, quality):
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    array_to_image(np.round(pixels).astype(np.uint8)).save(buffer, "JPG", quality)
    return image_to_array(QImage.fromData(bytes(buffer.data()))) / 257


def test_a_mark_is_invisible_and_read_back():
    original = photo()
    marked = original.copy()
    assert stego.embed(marked, "© Ana 2026")
    assert np.abs(marked - original).max() < 4 and np.sqrt(((marked - original) ** 2).mean()) < 1
    assert stego.read(np.round(marked)) == "© Ana 2026"


@pytest.mark.parametrize("quality", [95, 80, 60])
def test_a_mark_survives_jpeg(quality):
    marked = photo()
    stego.embed(marked, "Tamis")
    assert stego.read(as_jpeg(marked, quality)) == "Tamis"


def test_an_unmarked_or_resized_picture_has_no_mark():
    assert stego.read(photo()) is None
    marked = photo()
    stego.embed(marked, "Tamis")
    assert stego.read(marked[3:, 5:]) is None  # cropped: the blocks no longer line up


def test_too_long_or_too_small_is_refused():
    assert stego.problem("é" * 16) is None and stego.problem("é" * 17)
    small = photo(40, 40)
    assert not stego.embed(small, "Tamis") and stego.read(small) is None


def test_a_16_bit_export_keeps_its_mark(tmp_path):
    image = array_to_image(np.round(photo()).astype(np.uint8))
    marked = editing.with_hidden_mark(image, "Tamis", bits=16)
    assert marked.depth() == 64
    marked.save(str(tmp_path / "a.tif"))
    assert editing.read_hidden_mark(str(tmp_path / "a.tif")) == "Tamis"
