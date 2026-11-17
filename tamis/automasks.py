"""Automatic masks: the subject, the people and the sky of a photo.

The subject and people come from Apple's Vision (macOS 14 and later, through
pyobjc); the sky from the photo's own colours (bright, smooth, bluish or
pale, reaching the top). Each is worked out once per photo on its
development, refined so that its edges follow the picture's (a guided
filter: hair doesn't halo), and kept on disk by file name, size and
modification time, like the analysis. Masks are over the source (the upright
photo before any framing), which develop.render() samples (masks.sample()).
"""
import hashlib
import logging
import os
import sys
import threading
from collections import OrderedDict

import numpy as np

from . import local

log = logging.getLogger(__name__)

MASK_SIZE = 1024  # longest edge of a stored mask
KINDS = ("subject", "person", "sky")
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def vision_kinds():
    """The kinds Vision can find here."""
    if sys.platform != "darwin":
        return ()
    try:
        import Vision
    except ImportError:
        return ()
    found = []
    if hasattr(Vision, "VNGenerateForegroundInstanceMaskRequest"):
        found.append("subject")
    if hasattr(Vision, "VNGeneratePersonSegmentationRequest"):
        found.append("person")
    return tuple(found)


def available(kind):
    return kind == "sky" or kind in vision_kinds()


# -- finding them -------------------------------------------------------------------

def _pixel_buffer(buffer):
    """A CVPixelBuffer of one 8-bit or float channel, as a float32 array 0–1."""
    import Quartz

    Quartz.CVPixelBufferLockBaseAddress(buffer, 1)
    try:
        width, height = Quartz.CVPixelBufferGetWidth(buffer), Quartz.CVPixelBufferGetHeight(buffer)
        row = Quartz.CVPixelBufferGetBytesPerRow(buffer)
        kind = Quartz.CVPixelBufferGetPixelFormatType(buffer)
        address = Quartz.CVPixelBufferGetBaseAddress(buffer)
        data = bytes(address.as_buffer(row * height))
    finally:
        Quartz.CVPixelBufferUnlockBaseAddress(buffer, 1)
    if kind == Quartz.kCVPixelFormatType_OneComponent8:
        array = np.frombuffer(data, dtype=np.uint8).reshape(height, row)[:, :width].astype(np.float32) / 255
    else:  # kCVPixelFormatType_OneComponent32Float
        array = np.frombuffer(data, dtype=np.float32).reshape(height, row // 4)[:, :width].copy()
    return np.clip(array, 0.0, 1.0)


def _vision_mask(kind, png):
    """The mask Vision finds in a PNG (bytes), None when it finds nothing."""
    import Vision
    from Foundation import NSData, NSDictionary

    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        NSData.dataWithBytes_length_(png, len(png)), NSDictionary.dictionary()
    )
    if kind == "person":
        request = Vision.VNGeneratePersonSegmentationRequest.alloc().initWithCompletionHandler_(None)
        request.setQualityLevel_(Vision.VNGeneratePersonSegmentationRequestQualityLevelAccurate)
        request.setOutputPixelFormat_(1278226488)  # kCVPixelFormatType_OneComponent8
    else:
        request = Vision.VNGenerateForegroundInstanceMaskRequest.alloc().init()
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise OSError(str(error))
    results = request.results() or []
    if not results:
        return None
    observation = results[0]
    if kind == "person":
        return _pixel_buffer(observation.pixelBuffer())
    buffer, error = observation.generateScaledMaskForImageForInstances_fromRequestHandler_error_(
        observation.allInstances(), handler, None
    )
    if buffer is None:
        raise OSError(str(error))
    return _pixel_buffer(buffer)


def _resize(mask, height, width):
    """`mask` resampled to height × width (bilinear)."""
    from .masks import sample

    us = (np.arange(width, dtype=np.float32) + 0.5) / width
    vs = (np.arange(height, dtype=np.float32) + 0.5) / height
    return sample(mask, np.broadcast_to(us[None, :], (height, width)), np.broadcast_to(vs[:, None], (height, width)))


def sky(rgb):
    """The sky of `rgb` (h×w×3, sRGB-encoded 0–1): bright, smooth, bluish or pale, reaching the top."""
    height, width = rgb.shape[:2]
    luma = rgb @ LUMA
    r, b = rgb[..., 0], rgb[..., 2]
    radius = max(1, round(max(height, width) / 200))
    mean = local.box2d(luma, radius)
    texture = np.sqrt(np.maximum(local.box2d(luma * luma, radius) - mean * mean, 0))
    blue = np.clip((b - r) * 3 + 0.3, 0, 1)
    pale = np.clip((luma - 0.55) * 3, 0, 1) * np.clip(1 - (np.max(rgb, axis=2) - np.min(rgb, axis=2)) * 4, 0, 1)
    score = np.maximum(blue * np.clip((luma - 0.25) * 4, 0, 1), pale) * np.clip(1 - texture * 25, 0, 1)
    candidate = score > 0.35
    # Only what is connected to the top of the picture: grown down from the first rows.
    grown = np.zeros_like(candidate)
    grown[0] = candidate[0]
    for _ in range(height):
        spread = grown.copy()
        spread[1:] |= grown[:-1]
        spread[:, 1:] |= grown[:, :-1]
        spread[:, :-1] |= grown[:, 1:]
        spread &= candidate
        if (spread == grown).all():
            break
        grown = spread
    return grown.astype(np.float32) * np.clip(score * 1.5, 0, 1)


def refine(mask, rgb, radius=None, eps=1e-3):
    """`mask` with its edges moved onto those of `rgb` (a guided filter with the picture as guide)."""
    guide = rgb @ LUMA
    radius = radius or max(2, round(max(mask.shape) / 150))
    mean_i = local.box2d(guide, radius)
    mean_p = local.box2d(mask, radius)
    cov = local.box2d(guide * mask, radius) - mean_i * mean_p
    var = local.box2d(guide * guide, radius) - mean_i * mean_i
    a = cov / (var + eps)
    b = mean_p - a * mean_i
    return np.clip(local.box2d(a, radius) * guide + local.box2d(b, radius), 0.0, 1.0).astype(np.float32)


def find(kind, rgb, vision=None):
    """The `kind` mask of `rgb` (h×w×3, sRGB-encoded 0–1, the upright photo) at its size; zeros if none was found.

    `vision(kind, png)` replaces Vision (tests).
    """
    height, width = rgb.shape[:2]
    if kind == "sky":
        found = sky(rgb)
    else:
        from .faces import _png
        from .imaging import array_to_image

        png = _png(array_to_image(np.clip(rgb * 255 + 0.5, 0, 255).astype(np.uint8)))
        found = (vision or _vision_mask)(kind, png)
        if found is None:
            return np.zeros((height, width), dtype=np.float32)
        if found.shape != (height, width):
            found = _resize(found, height, width)
    return refine(found.astype(np.float32), rgb)


# -- keeping them -------------------------------------------------------------------

def _file_key(path):
    from .analysis import file_key

    return file_key(path)


class MaskStore:
    """Automatic masks kept on disk (and the last few in memory), by photo and kind."""

    MEMORY = 12

    def __init__(self, folder):
        self.folder = folder
        self._memory = OrderedDict()  # (file key, kind) -> array
        self._lock = threading.Lock()

    def _file(self, key, kind):
        digest = hashlib.sha1(f"{key}\0{kind}".encode("utf-8", "surrogateescape")).hexdigest()
        return os.path.join(self.folder, digest[:2], f"{digest}.npy")

    def load(self, path, kinds):
        """{kind: mask} of those of `kinds` found for `path` before; the others are left out."""
        key = _file_key(path)
        if key is None:
            return None
        found = {}
        for kind in kinds:
            with self._lock:
                if (key, kind) in self._memory:
                    self._memory.move_to_end((key, kind))
                    found[kind] = self._memory[(key, kind)]
                    continue
            try:
                mask = np.load(self._file(key, kind), allow_pickle=False).astype(np.float32) / 255
            except (OSError, ValueError):
                continue
            self._remember(key, kind, mask)
            found[kind] = mask
        return found or None

    def has(self, path, kind):
        return bool(self.load(path, [kind]))

    def save(self, path, kind, mask):
        key = _file_key(path)
        if key is None:
            return
        height, width = mask.shape
        scale = min(1.0, MASK_SIZE / max(height, width))
        if scale < 1:
            mask = _resize(mask, max(1, round(height * scale)), max(1, round(width * scale)))
        self._remember(key, kind, mask.astype(np.float32))
        file = self._file(key, kind)
        try:
            os.makedirs(os.path.dirname(file), exist_ok=True)
            np.save(file, np.clip(mask * 255 + 0.5, 0, 255).astype(np.uint8), allow_pickle=False)
        except OSError as exc:  # kept in memory for this session
            log.warning("Could not keep the %s mask of %s: %s", kind, path, exc)

    def _remember(self, key, kind, mask):
        with self._lock:
            self._memory[(key, kind)] = mask
            while len(self._memory) > self.MEMORY:
                self._memory.popitem(last=False)
