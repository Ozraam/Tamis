"""Faces: where they are, whether their eyes are closed, and how sharp they are.

Detection uses Apple's Vision framework (through pyobjc), which runs on the
Neural Engine and needs no model in the app; elsewhere, or without pyobjc,
there is no detection and the option is hidden. Like the other scores, these
are only suggestions: the marks stay the user's.

Faces are found on the upright photo (its orientation applied, not its edit's
framing), with boxes in fractions of its width and height, from the top left.
"""
import logging
import math
import sys
from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QBuffer, QIODevice

from . import develop

log = logging.getLogger(__name__)

FACE_MAX_SIZE = 2048  # longest edge faces are looked for on: a face in a group photo is small
# Eye aspect ratio (height / width of the eye's outline) below which eyes count as closed:
# open eyes measure about 0.25–0.35, closed ones 0.05–0.12.
EYES_CLOSED_RATIO = 0.15
# Faces smaller than this share of the largest one are in the background: they don't count.
MAIN_FACE_SHARE = 0.4
# Below this share of the folder's median face sharpness, a face is suggested as soft.
# Untuned: a first guess, a little higher than models.SOFT_RATIO, as a face has little texture.
FACE_SOFT_RATIO = 0.3


@dataclass(frozen=True)
class Face:
    box: tuple  # (x, y, width, height), fractions of the upright photo, from the top left
    eyes: float | None = None  # eye aspect ratio of the more open eye; None if unknown
    sharpness: float | None = None  # analysis.sharpness() of the face alone

    @property
    def area(self):
        return self.box[2] * self.box[3]

    @property
    def center(self):
        x, y, width, height = self.box
        return x + width / 2, y + height / 2

    @property
    def eyes_closed(self):
        return self.eyes is not None and self.eyes < EYES_CLOSED_RATIO

    def to_json(self):
        return [*(round(value, 5) for value in self.box), self.eyes, self.sharpness]

    @classmethod
    def from_json(cls, value):
        """A Face from to_json(), None if it isn't one."""
        if not isinstance(value, (list, tuple)) or len(value) != 6:
            return None
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value[:4]):
            return None
        eyes, score = value[4], value[5]
        eyes = float(eyes) if isinstance(eyes, (int, float)) and not isinstance(eyes, bool) else None
        score = float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None
        return cls(tuple(float(v) for v in value[:4]), eyes, score)


def faces_from_json(value):
    """[Face] from what the analysis cache keeps; None if it isn't a list."""
    if not isinstance(value, list):
        return None
    return [face for face in map(Face.from_json, value) if face is not None]


# -- what faces suggest ---------------------------------------------------------

def main_faces(faces):
    """The faces that matter in a photo: not those far smaller than the largest (background)."""
    if not faces:
        return []
    largest = max(face.area for face in faces)
    return [face for face in faces if face.area >= largest * MAIN_FACE_SHARE]


def main_face(faces):
    """The largest face, None if there is none."""
    return max(faces, key=lambda face: face.area) if faces else None


def eyes_closed(faces):
    """Whether someone who matters in the photo has their eyes closed."""
    return any(face.eyes_closed for face in main_faces(faces or []))


def face_score(faces):
    """Sharpness of the least sharp face that matters (a group is as sharp as its softest face); None if unknown."""
    scores = [face.sharpness for face in main_faces(faces or []) if face.sharpness is not None]
    return min(scores) if scores else None


def is_soft(faces, median):
    score = face_score(faces)
    return score is not None and median is not None and score < median * FACE_SOFT_RATIO


def eye_ratio(points):
    """Eye aspect ratio of an eye's outline, [(x, y)] in any unit: its height over its width."""
    if len(points) < 3:
        return None
    xs = [x for x, _y in points]
    ys = [y for _x, y in points]
    width = max(xs) - min(xs)
    return None if width <= 0 else (max(ys) - min(ys)) / width


def framed_point(x, y, edit, size):
    """Where the point (x, y) of the upright photo (fractions) is once framed by `edit`, as fractions of the frame.

    `size` is the upright photo's (width, height), for straightening. None if the
    frame cuts it off.
    """
    rotate = edit.get("rotate", 0)
    width, height = size
    if rotate == 90:
        x, y, width, height = 1 - y, x, height, width
    elif rotate == 180:
        x, y = 1 - x, 1 - y
    elif rotate == 270:
        x, y, width, height = y, 1 - x, height, width
    degrees = edit.get("straighten", 0)
    if degrees:
        # Turned clockwise about the centre and enlarged to fill the frame, like develop.straighten_rows().
        zoom = develop.straighten_zoom(height, width, degrees)
        dx, dy = (x - 0.5) * width, (y - 0.5) * height
        angle = math.radians(degrees)
        dx, dy = dx * math.cos(angle) - dy * math.sin(angle), dx * math.sin(angle) + dy * math.cos(angle)
        x, y = 0.5 + dx * zoom / width, 0.5 + dy * zoom / height
    crop = edit.get("crop")
    if crop:
        left, top, right, bottom = crop
        x, y = (x - left) / (right - left), (y - top) / (bottom - top)
    if not (0 <= x <= 1 and 0 <= y <= 1):
        return None
    return x, y


# -- detection -------------------------------------------------------------------

_vision = None


def _load_vision():
    """The Vision module, or None where it can't be had."""
    global _vision
    if _vision is None:
        _vision = False
        if sys.platform == "darwin":
            try:
                import Vision  # pyobjc-framework-Vision, macOS only

                _vision = Vision
            except ImportError:
                log.info("Face detection is off: pyobjc-framework-Vision isn't installed")
    return _vision or None


def available():
    """Whether faces can be detected here."""
    return _load_vision() is not None


def _png(image):
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG", 100)  # lossless and quick at the lowest compression
    return bytes(buffer.data())


def _points(region):
    """[(x, y)] of a VNFaceLandmarkRegion2D, in its face's box (fractions, from the bottom left)."""
    if region is None:
        return []
    count = region.pointCount()
    points = region.normalizedPoints()
    return [(points[i].x, points[i].y) for i in range(count)]


def _vision_faces(image):
    """[(box from the top left, eye ratio or None)] that Vision finds in a QImage."""
    vision = _load_vision()
    from Foundation import NSData, NSDictionary  # comes with pyobjc

    data = _png(image)
    # An NSDictionary: an empty Python dict is refused ("key does not exist").
    handler = vision.VNImageRequestHandler.alloc().initWithData_options_(
        NSData.dataWithBytes_length_(data, len(data)), NSDictionary.dictionary()
    )
    request = vision.VNDetectFaceLandmarksRequest.alloc().init()
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise OSError(str(error))
    found = []
    for observation in request.results() or []:
        box = observation.boundingBox()
        x, y, width, height = box.origin.x, box.origin.y, box.size.width, box.size.height
        landmarks = observation.landmarks()
        ratios = []
        if landmarks is not None:
            # The box's own fractions: scaled by its size in pixels, so that the ratio isn't squashed.
            for region in (landmarks.leftEye(), landmarks.rightEye()):
                try:
                    points = [(px * width * image.width(), py * height * image.height()) for px, py in _points(region)]
                except (TypeError, ValueError, IndexError, AttributeError) as exc:  # the face counts, its eyes unknown
                    log.warning("Could not read the eyes of a face: %s", exc)
                    continue
                ratio = eye_ratio(points)
                if ratio is not None:
                    ratios.append(ratio)
        found.append(((x, 1 - y - height, width, height), max(ratios) if ratios else None))
    return found


def detect(image, find=None):
    """[Face] in a QImage, sharpness scored on each face alone. `find` replaces Vision (tests)."""
    from .analysis import sharpness  # here: analysis imports models, which imports this module
    from .imaging import gray_array

    find = find or _vision_faces
    gray = None
    faces = []
    for box, eyes in find(image):
        x, y, width, height = (min(1.0, max(0.0, value)) for value in box)
        if gray is None:
            gray = gray_array(image)
        rows, columns = gray.shape
        top, left = int(y * rows), int(x * columns)
        crop = gray[top:max(top + 1, int((y + height) * rows)), left:max(left + 1, int((x + width) * columns))]
        faces.append(Face((x, y, width, height), eyes, sharpness(np.ascontiguousarray(crop)) if crop.size else None))
    return faces


def analyze(path, find=None):
    """{"faces": [Face as JSON]} of a photo; {"faces": None} if it can't be read (tried again next time)."""
    from .imaging import load_image

    try:
        image, _native = load_image(path, FACE_MAX_SIZE)
        return {"faces": [face.to_json() for face in detect(image, find)]}
    except Exception as exc:  # a bad photo, or Vision failing, must never kill a worker
        log.warning("Could not look for faces in %s: %s", path, exc, exc_info=not isinstance(exc, OSError))
        return {"faces": None}
