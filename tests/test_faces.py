import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from tamis import bursts, faces  # noqa: E402
from tamis.models import Photo  # noqa: E402

app = QApplication.instance() or QApplication([])


def face(x=0.4, y=0.3, size=0.2, eyes=0.3, sharpness=100.0):
    return faces.Face((x, y, size, size), eyes, sharpness)


def test_eye_ratio_tells_open_from_closed():
    open_eye = [(0, 0), (10, 3), (20, 0), (10, -3)]
    closed_eye = [(0, 0), (10, 0.5), (20, 0), (10, -0.5)]
    assert faces.eye_ratio(open_eye) == pytest.approx(0.3)
    assert faces.eye_ratio(closed_eye) < faces.EYES_CLOSED_RATIO
    assert faces.eye_ratio([(0, 0)]) is None


def test_background_faces_dont_count():
    main, background = face(size=0.3, eyes=0.3), face(size=0.05, eyes=0.05)
    assert faces.main_faces([main, background]) == [main]
    assert not faces.eyes_closed([main, background])
    assert faces.eyes_closed([main, face(size=0.25, eyes=0.05)])
    assert faces.main_face([background, main]) is main
    assert faces.main_face([]) is None


def test_a_group_is_as_sharp_as_its_softest_face():
    group = [face(sharpness=200), face(x=0.1, sharpness=40)]
    assert faces.face_score(group) == 40
    assert faces.is_soft(group, median=200)
    assert not faces.is_soft(group, median=100)
    assert faces.face_score(None) is None and not faces.is_soft([], 100)


def test_faces_round_trip_through_the_cache():
    found = [face(eyes=None), face(sharpness=None)]
    assert faces.faces_from_json([f.to_json() for f in found]) == found
    assert faces.faces_from_json([[1, 2], "x", [0.1, 0.1, 0.1, 0.1, "a", True]]) == [faces.Face((0.1, 0.1, 0.1, 0.1))]
    assert faces.faces_from_json("nonsense") is None


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        ({}, (0.25, 0.5)),
        ({"rotate": 90}, (0.5, 0.25)),
        ({"rotate": 180}, (0.75, 0.5)),
        ({"rotate": 270}, (0.5, 0.75)),
        ({"crop": [0, 0, 0.5, 1]}, (0.5, 0.5)),
        ({"crop": [0.5, 0, 1, 1]}, None),
    ],
)
def test_a_face_follows_the_frame(edit, expected):
    point = faces.framed_point(0.25, 0.5, edit, (300, 200))
    assert point == (pytest.approx(expected) if expected else None)


def test_straightening_keeps_the_centre_and_turns_the_rest():
    assert faces.framed_point(0.5, 0.5, {"straighten": 5.0}, (300, 200)) == pytest.approx((0.5, 0.5))
    x, y = faces.framed_point(0.75, 0.5, {"straighten": 5.0}, (300, 200))
    assert x > 0.75 and y > 0.5  # clockwise, and enlarged


def test_detection_scores_each_face_alone():
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(QColor(128, 128, 128))
    painter = QPainter(image)
    for x in range(0, 100, 4):  # sharp stripes on the left half only
        painter.fillRect(x, 0, 2, 100, QColor(0, 0, 0))
    painter.end()
    found = faces.detect(image, find=lambda _image: [((0.1, 0.2, 0.3, 0.6), 0.3), ((0.6, 0.2, 0.3, 0.6), None)])
    assert [f.box for f in found] == [(0.1, 0.2, 0.3, 0.6), (0.6, 0.2, 0.3, 0.6)]
    assert found[0].sharpness > 1000 and found[1].sharpness == 0
    assert found[1].eyes is None


def test_analyze_never_raises(tmp_path):
    assert faces.analyze(str(tmp_path / "missing.jpg"), find=lambda _image: []) == {"faces": None}
    image = QImage(64, 64, QImage.Format.Format_RGB32)
    image.fill(QColor(10, 20, 30))
    image.save(str(tmp_path / "a.jpg"))
    assert faces.analyze(str(tmp_path / "a.jpg"), find=lambda _image: []) == {"faces": []}

    def broken(_image):
        raise OSError("Vision failed")

    assert faces.analyze(str(tmp_path / "a.jpg"), find=broken) == {"faces": None}


def test_the_best_frame_of_a_burst_is_one_where_nobody_blinks():
    sharp_blink = Photo("a.jpg", sharpness=900, faces=[face(eyes=0.05, sharpness=500)])
    open_eyes = Photo("b.jpg", sharpness=100, faces=[face(eyes=0.3, sharpness=300)])
    soft = Photo("c.jpg", sharpness=50, faces=[face(eyes=0.3, sharpness=100)])
    assert bursts.best_frame([sharp_blink, open_eyes, soft]) is open_eyes
    # Without faces, the sharpest frame
    assert bursts.best_frame([Photo("d.jpg", sharpness=10), Photo("e.jpg", sharpness=20)]).path == "e.jpg"
    assert bursts.best_frame([Photo("f.jpg", sharpness=10)]) is None


@pytest.mark.skipif(not faces.available(), reason="needs macOS with pyobjc-framework-Vision")
def test_vision_runs_on_a_picture_without_faces():
    image = QImage(320, 240, QImage.Format.Format_RGB32)
    image.fill(QColor(30, 90, 160))
    assert faces.detect(image) == []
