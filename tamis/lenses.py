"""Lens corrections: distortion, vignetting and lateral chromatic aberration.

Profiles come from the lensfun database, read straight from its XML files
(no C library to bundle): the folder chosen in the app, or one where lensfun
is installed. The camera and lens are matched from the photo's EXIF. Each
correction can also be set by hand, without a profile.

Distortion and chromatic aberration move pixels, so they are applied to the
developed photo before it is framed (warp(), like straightening); vignetting
is a gain per pixel that depends on its distance to the centre of the source,
applied in develop.render() (vignette_gain()).

Lensfun's coordinates: the centre of the picture is 0 and half of its shorter
side is 1, scaled by the ratio of the calibration's crop factor to the camera's.
"""
import glob
import logging
import math
import os
import re
import threading
import xml.etree.ElementTree as ElementTree

import numpy as np

log = logging.getLogger(__name__)

SEARCH_PATHS = (
    "~/.local/share/lensfun/updates/version_1",
    "~/.local/share/lensfun/version_1",
    "/opt/homebrew/share/lensfun/version_1",
    "/usr/local/share/lensfun/version_1",
    "/usr/share/lensfun/version_1",
    "/opt/local/share/lensfun/version_1",
)

_lock = threading.Lock()
_databases = {}  # folder -> Database


def default_folder():
    """The first folder of SEARCH_PATHS that holds a lensfun database, None if there is none."""
    for folder in SEARCH_PATHS:
        folder = os.path.expanduser(folder)
        if glob.glob(os.path.join(folder, "*.xml")):
            return folder
    return None


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def _tokens(text):
    return [t for t in re.split(r"[\s/,()\-]+", _norm(text).replace("f/", "f")) if t]


class Database:
    """The cameras and lenses of the XML files of one folder."""

    def __init__(self, folder):
        self.cameras = []  # (maker, model, crop factor)
        self.lenses = []  # dicts: maker, model, crop, calibration
        for path in sorted(glob.glob(os.path.join(folder, "*.xml"))):
            try:
                root = ElementTree.parse(path).getroot()
            except (ElementTree.ParseError, OSError) as exc:
                log.warning("Skipping lensfun file %s: %s", path, exc)
                continue
            for camera in root.iter("camera"):
                crop = _float(camera.findtext("cropfactor"), 1.0)
                for maker in _texts(camera, "maker"):
                    for model in _texts(camera, "model"):
                        self.cameras.append((_norm(maker), _norm(model), crop))
            for lens in root.iter("lens"):
                self.lenses.append(_read_lens(lens))

    def camera_crop(self, make, model):
        """The crop factor of the camera, None if it isn't in the database."""
        make, model = _norm(make), _norm(model)
        for maker, name, crop in self.cameras:
            if name == model and (not make or maker == make or maker in make or make in maker):
                return crop
        for _maker, name, crop in self.cameras:
            if name == model:
                return crop
        return None

    def find_lens(self, make, lens_model, camera_crop=None):
        """The best match for the EXIF lens name, None if nothing is close enough."""
        wanted = set(_tokens(lens_model))
        if not wanted:
            return None
        best, best_score = None, 0.0
        for lens in self.lenses:
            names = lens["models"]
            for name in names:
                tokens = _tokens(name)
                if not tokens:
                    continue
                common = len(wanted & set(tokens))
                score = common / max(len(set(tokens)), len(wanted)) + (0.05 if make and _norm(make) in lens["maker"] else 0)
                if camera_crop and lens["crop"] and lens["crop"] > camera_crop * 1.1:
                    score -= 0.2  # calibrated on a smaller sensor: it doesn't cover this one
                if score > best_score:
                    best, best_score = lens, score
        return best if best_score >= 0.6 else None


def _texts(element, tag):
    return [child.text for child in element.findall(tag) if child.text] or [""]


def _float(text, default=None):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _read_lens(element):
    calibration = {"distortion": [], "tca": [], "vignetting": []}
    for block in element.findall("calibration"):
        for item in block:
            kind = item.tag
            if kind not in calibration:
                continue
            values = {key: _float(value) for key, value in item.attrib.items() if key != "model"}
            values["model"] = item.attrib.get("model", "")
            if values.get("focal") is not None:
                calibration[kind].append(values)
    return {
        "maker": _norm(element.findtext("maker")),
        "models": [child.text for child in element.findall("model") if child.text],
        "crop": _float(element.findtext("cropfactor"), 1.0),
        "aspect": _aspect(element.findtext("aspect-ratio")),
        "calibration": calibration,
    }


def _aspect(text):
    if not text:
        return 1.5
    if ":" in text:
        a, b = text.split(":", 1)
        return (_float(a, 3) or 3) / (_float(b, 2) or 2)
    return _float(text, 1.5)


def database(folder):
    if not folder:
        return None
    with _lock:
        if folder not in _databases:
            _databases[folder] = Database(folder)
        return _databases[folder]


def _interpolate(entries, focal, keys):
    """The entry's `keys` at `focal`, linearly between the two calibrated focal lengths around it."""
    entries = sorted(entries, key=lambda e: e["focal"])
    if not entries:
        return None
    if focal is None or len(entries) == 1:
        entry = entries[0] if focal is None else min(entries, key=lambda e: abs(e["focal"] - focal))
        return entry["model"], [entry.get(k) or 0.0 for k in keys]
    if focal <= entries[0]["focal"]:
        low = high = entries[0]
    elif focal >= entries[-1]["focal"]:
        low = high = entries[-1]
    else:
        low = max((e for e in entries if e["focal"] <= focal), key=lambda e: e["focal"])
        high = min((e for e in entries if e["focal"] >= focal), key=lambda e: e["focal"])
    if low["model"] != high["model"] or low["focal"] == high["focal"]:
        entry = min((low, high), key=lambda e: abs(e["focal"] - focal))
        return entry["model"], [entry.get(k) or 0.0 for k in keys]
    t = (focal - low["focal"]) / (high["focal"] - low["focal"])
    return low["model"], [(low.get(k) or 0.0) * (1 - t) + (high.get(k) or 0.0) * t for k in keys]


def correction(lens, focal, aperture, camera_crop):
    """The corrections of `lens` (from Database.find_lens()) for one shot: a plain dict, None if it has none."""
    calibration = lens["calibration"]
    result = {"name": lens["models"][0] if lens["models"] else "", "scale": 1.0}
    if camera_crop and lens["crop"]:
        result["scale"] = lens["crop"] / camera_crop
    distortion = _interpolate(calibration["distortion"], focal, ("k1", "k2", "a", "b", "c"))
    if distortion and distortion[0] in ("poly3", "poly5", "ptlens"):
        result["distortion"] = [distortion[0], distortion[1]]
    tca = _interpolate(calibration["tca"], focal, ("vr", "vb", "cr", "cb", "br", "bb", "kr", "kb"))
    if tca and tca[0] in ("linear", "poly3"):
        result["tca"] = [tca[0], tca[1]]
    vignetting = [v for v in calibration["vignetting"] if v.get("model") == "pa"]
    if vignetting:
        if aperture is not None:
            nearest = min(abs((v.get("aperture") or 0) - aperture) for v in vignetting)
            vignetting = [v for v in vignetting if abs((v.get("aperture") or 0) - aperture) == nearest]
        far = max(v.get("distance") or 0 for v in vignetting)
        vignetting = [v for v in vignetting if (v.get("distance") or 0) == far]
        found = _interpolate(vignetting, focal, ("k1", "k2", "k3"))
        if found:
            result["vignetting"] = found[1]
    return result if len(result) > 2 else None


def find(info, folder):
    """The corrections for a photo whose EXIF says `info` (exif.read_lens()), None if there is no profile."""
    db = database(folder)
    if db is None or not info.get("lens"):
        return None
    crop = db.camera_crop(info.get("make"), info.get("model"))
    lens = db.find_lens(info.get("make"), info["lens"], crop)
    if lens is None:
        return None
    return correction(lens, info.get("focal"), info.get("aperture"), crop or lens["crop"])


# -- applying them ---------------------------------------------------------------

def _distortion_radius(model, params, ru):
    """Where a point at radius `ru` of the corrected picture is in the lens's picture (lensfun's models)."""
    k1, k2, a, b, c = params
    if model == "poly3":
        return ru * (1 - k1 + k1 * ru * ru)
    if model == "poly5":
        r2 = ru * ru
        return ru * (1 + k1 * r2 + k2 * r2 * r2)
    if model == "ptlens":
        return ru * (a * ru ** 3 + b * ru * ru + c * ru + 1 - a - b - c)
    return ru


def _tca_scale(model, params, rd, channel):
    vr, vb, cr, cb, br, bb, kr, kb = params
    if model == "linear":
        return (kr or 1.0) if channel == 0 else (kb or 1.0)
    v, c, b = (vr or 1.0, cr, br) if channel == 0 else (vb or 1.0, cb, bb)
    return b * rd * rd + c * rd + v


def has_geometry(settings, lens):
    """Whether the photo's pixels move: distortion or chromatic aberration to correct."""
    profile = settings.get("lens_profile") and lens
    return bool(
        settings.get("distortion") or settings.get("ca_red") or settings.get("ca_blue")
        or (profile and settings.get("lens_distortion", 100) and ("distortion" in lens or "tca" in lens))
    )


def has_vignetting(settings, lens):
    return bool(settings.get("devignette") or (settings.get("lens_profile") and lens and "vignetting" in lens
                                               and settings.get("lens_vignetting", 100)))


class _Mapping:
    """Where each pixel of the corrected picture is read from in the lens's picture, per channel."""

    def __init__(self, settings, lens, height, width):
        self.height, self.width = height, width
        self.half = min(height, width) / 2  # lensfun's unit
        self.profile = lens if settings.get("lens_profile") and lens else None
        self.amount = settings.get("lens_distortion", 100) / 100
        self.manual = -settings.get("distortion", 0) / 100 * 0.25  # positive: straightens barrel distortion
        self.ca = (settings.get("ca_red", 0) / 100 * 0.004, settings.get("ca_blue", 0) / 100 * 0.004)
        self.scale = self.profile["scale"] if self.profile else 1.0
        self.fill = 1.0
        # Enlarged as little as needed so that no corner or edge reads outside the picture.
        diagonal = math.hypot(width, height) / 2 / self.half
        edges = [diagonal, width / 2 / self.half, height / 2 / self.half]
        for _ in range(3):
            worst = max(self.source_radius(r) / r for r in edges)
            if worst <= 1.0001:
                break
            self.fill /= worst

    def source_radius(self, ru):
        """Radius in the lens's picture of radius `ru` of the corrected one, for green (lensfun units)."""
        rd = ru * self.fill
        if self.profile and "distortion" in self.profile and self.amount:
            model, params = self.profile["distortion"]
            scaled = rd * self.scale
            moved = _distortion_radius(model, params, scaled) / self.scale
            rd = rd + (moved - rd) * self.amount
        if self.manual:
            diagonal = math.hypot(self.width, self.height) / 2 / self.half
            t = rd / diagonal
            rd = rd * (1 + self.manual * t * t)
        return rd

    def channel_scale(self, rd, channel):
        scale = 1.0
        if self.profile and "tca" in self.profile and self.amount:
            model, params = self.profile["tca"]
            scale = 1 + (_tca_scale(model, params, rd * self.scale, channel) - 1) * self.amount
        return scale * (1 + self.ca[0 if channel == 0 else 1])


def warp_rows(pixels, settings, lens, top, bottom):
    """Rows `top` to `bottom` of `pixels` (h×w×3) with distortion and chromatic aberration corrected (bilinear)."""
    height, width = pixels.shape[:2]
    mapping = _Mapping(settings, lens, height, width)
    cx, cy = (width - 1) / 2, (height - 1) / 2
    dx = (np.arange(width, dtype=np.float32) - cx)[None, :] / mapping.half
    dy = (np.arange(top, bottom, dtype=np.float32) - cy)[:, None] / mapping.half
    ru = np.sqrt(dx * dx + dy * dy) + 1e-9
    rd = mapping.source_radius(ru)
    out = np.empty((bottom - top, width, 3), dtype=np.float32)
    for channel in range(3):
        r = rd if channel == 1 else rd * mapping.channel_scale(rd, 0 if channel == 0 else 2)
        factor = r / ru
        x = np.clip(dx * factor * mapping.half + cx, 0, width - 1)
        y = np.clip(dy * factor * mapping.half + cy, 0, height - 1)
        x0, y0 = x.astype(np.intp), y.astype(np.intp)
        x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
        fx, fy = x - x0, y - y0
        plane = pixels[..., channel]
        upper = plane[y0, x0] * (1 - fx) + plane[y0, x1] * fx
        lower = plane[y1, x0] * (1 - fx) + plane[y1, x1] * fx
        out[..., channel] = upper * (1 - fy) + lower * fy
    if np.issubdtype(pixels.dtype, np.integer):
        return (out + 0.5).astype(pixels.dtype)
    return out


def vignette_gain(settings, lens, geometry, x, y):
    """The gain (rows × width) that takes away the lens's vignetting, at turned units `x`, `y` (masks.Geometry)."""
    # Distance to the centre of the source, in units of half its shorter side.
    half = min(geometry.aspect, 1.0) / 2
    r = np.hypot(x - geometry.aspect / 2, y - 0.5) / half
    gain = np.ones_like(r, dtype=np.float32)
    if settings.get("lens_profile") and lens and "vignetting" in lens:
        k1, k2, k3 = lens["vignetting"]
        rs = r * lens.get("scale", 1.0)
        r2 = rs * rs
        falloff = 1 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2
        amount = settings.get("lens_vignetting", 100) / 100
        gain *= np.power(1 / np.clip(falloff, 0.05, None), amount).astype(np.float32)
    if settings.get("devignette"):
        corner = math.hypot(geometry.aspect, 1.0) / 2 / half
        t = (r / corner) ** 2
        gain *= np.exp2(settings["devignette"] / 100 * 1.2 * t).astype(np.float32)
    return gain
