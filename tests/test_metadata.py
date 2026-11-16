import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QBuffer, QIODevice  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from tamis import exif, metadata  # noqa: E402
from tamis.exportsettings import ExportSettings  # noqa: E402

app = QApplication.instance() or QApplication([])


def jpeg_bytes():
    image = QImage(32, 16, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 120, 150))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "JPG")
    return bytes(buffer.data())


def test_clean_keeps_only_what_is_set():
    assert metadata.clean({"title": "  ", "caption": " A day ", "keywords": ["a", " a", "", 3, "b"], "other": 1}) == {
        "caption": "A day", "keywords": ["a", "b"],
    }
    assert metadata.clean("nonsense") == {}


def test_an_export_carries_the_photo_s_words_and_the_settings_credits():
    settings = ExportSettings(creator="Ana", copyright="© 2026 Ana", keywords="wedding, Paris")
    data = metadata.for_export(settings, {"title": "Vows", "keywords": ["Paris", "rings"]})
    assert data == {"title": "Vows", "keywords": ["Paris", "rings", "wedding"], "creator": "Ana", "copyright": "© 2026 Ana"}
    assert metadata.for_export(ExportSettings(), {}) == {}


def test_jpeg_gets_xmp_and_iptc_and_still_decodes():
    data = {"title": "Vows & rings", "caption": "Été", "keywords": ["a", "b"], "creator": "Ana",
            "copyright": "© Ana", "contact": "ana@example.com"}
    written = metadata.with_metadata(exif.with_exif(jpeg_bytes(), b"Exif\0\0" + b"MM\0*\0\0\0\x08\0\0\0\0\0\0"), data)
    xmp, datasets = metadata.read_jpeg(written)
    assert "<rdf:li xml:lang=\"x-default\">Vows &amp; rings</rdf:li>" in xmp
    assert "<Iptc4xmpCore:CiEmailWork>ana@example.com</Iptc4xmpCore:CiEmailWork>" in xmp
    assert (2, 5, b"Vows & rings") in datasets
    assert (2, 120, "Été".encode()) in datasets
    assert [value for record, number, value in datasets if (record, number) == (2, 25)] == [b"a", b"b"]
    assert (1, 90, b"\x1b%G") in datasets
    image = QImage.fromData(written)
    assert not image.isNull() and image.width() == 32
    # EXIF stays first, right after JFIF: XMP and IPTC follow it
    assert markers(written)[:4] == [(0xE0, b"JFIF"), (0xE1, b"Exif"), (0xE1, b"http"), (0xED, b"Phot")]


def markers(jpeg):
    found, i = [], 2
    while jpeg[i] == 0xFF and jpeg[i + 1] != 0xDA:
        length = int.from_bytes(jpeg[i + 2:i + 4], "big")
        found.append((jpeg[i + 1], jpeg[i + 4:i + 8]))
        i += 2 + length
    return found


def test_metadata_written_again_replaces_the_old():
    once = metadata.with_metadata(jpeg_bytes(), {"title": "One"})
    twice = metadata.with_metadata(once, {"title": "Two"})
    xmp, datasets = metadata.read_jpeg(twice)
    assert "Two" in xmp and "One" not in xmp
    assert [value for record, number, value in datasets if (record, number) == (2, 5)] == [b"Two"]


def test_nothing_to_write_leaves_the_jpeg_alone():
    original = jpeg_bytes()
    assert metadata.with_metadata(original, {}) is original


def test_iim_fields_are_cut_on_a_character():
    block = metadata.iim_block({"creator": "é" * 40})  # 80 bytes, the field takes 32
    assert "é".encode() * 16 in block and "é".encode() * 17 not in block
