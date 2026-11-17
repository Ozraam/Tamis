"""Camera colour profiles for RAWs: how a RAW's colours are rendered.

Built in: "Standard" (None: LibRaw's colour matrix, then develop's highlight
roll-off, fitted to camera JPEGs) and "Neutral" (the same, flatter). Besides,
DCP profiles (the format of Adobe and RawTherapee) are read from the folder
chosen in the app and from Adobe's own folders when they are there: Tamis
can't ship Adobe's camera matching profiles, but uses those the user has.

A DCP is applied to the camera's own colours (LibRaw's development without
its matrix, white balanced): its forward matrix (or its colour matrix) to
XYZ, its hue / saturation map and look table in linear ProPhoto RGB, its tone
curve, then to linear sRGB. All of it per pixel, so a preview and an export
in strips match. A profile is chosen per photo (the edit's "profile": an id
from scan()), with a default per camera kept by the app.
"""
import glob
import logging
import os
import re
import struct
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)

NEUTRAL = "neutral"
BUILT_IN = ((None, "Standard"), (NEUTRAL, "Neutral"))
ADOBE_FOLDERS = (
    "~/Library/Application Support/Adobe/CameraRaw/CameraProfiles",
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles",
)
GAIN = 2.0 ** 1.5  # what DCPs' camera colours are scaled by before their tone curve

# Linear ProPhoto RGB <-> XYZ (D50), and XYZ (D50) -> linear sRGB (D65, Bradford).
_XYZ_TO_PROPHOTO = np.array([
    [1.3459433, -0.2556075, -0.0511118],
    [-0.5445989, 1.5081673, 0.0205351],
    [0.0, 0.0, 1.2118128],
], dtype=np.float64)
_PROPHOTO_TO_XYZ = np.linalg.inv(_XYZ_TO_PROPHOTO)
_XYZ_TO_SRGB = np.array([
    [3.1338561, -1.6168667, -0.4906146],
    [-0.9787684, 1.9161415, 0.0334540],
    [0.0719453, -0.2289914, 1.4052427],
], dtype=np.float64)
_D50 = np.array([0.9642, 1.0, 0.8249])

# DCP tags
_TAGS = {
    0xC614: "camera", 0xC6F8: "name", 0xC65A: "illuminant1", 0xC65B: "illuminant2",
    0xC621: "color_matrix1", 0xC622: "color_matrix2", 0xC714: "forward_matrix1", 0xC715: "forward_matrix2",
    0xC6F9: "hsm_dims", 0xC6FA: "hsm1", 0xC6FB: "hsm2", 0xC725: "look_dims", 0xC726: "look",
    0xC6FC: "tone_curve", 0xC7A4: "hsm_encoding", 0xC7A5: "look_encoding", 0xC7A6: "exposure_offset",
}
_D65_ILLUMINANTS = (21, 20, 22, 23, 4, 10, 11, 12, 13, 14, 15)  # daylight-like: preferred over tungsten (17, 2…)


class ProfileError(Exception):
    pass


@dataclass
class Profile:
    name: str
    camera: str
    matrix: np.ndarray  # white-balanced camera RGB -> XYZ D50
    hue_sat: tuple | None = None  # (dims, data h×s×v×3, sRGB-encoded value axis)
    look: tuple | None = None
    tone: np.ndarray | None = None  # [[x, y]], linear in and out
    exposure: float = 0.0
    path: str = ""
    _tone_table: np.ndarray | None = field(default=None, repr=False)

    def linearize(self, block):
        """Linear sRGB (float32) of a block of LibRaw's camera colours (uint16, white balanced)."""
        rgb = block.astype(np.float32) * np.float32(GAIN * 2.0 ** self.exposure / 65535)
        pro = rgb @ (_XYZ_TO_PROPHOTO @ self.matrix).T.astype(np.float32)
        np.maximum(pro, 0.0, out=pro)
        if self.hue_sat is not None:
            pro = _apply_map(pro, *self.hue_sat)
        if self.look is not None:
            pro = _apply_map(pro, *self.look)
        if self.tone is not None:
            pro = self._curve(pro)
        return (pro @ (_XYZ_TO_SRGB @ _PROPHOTO_TO_XYZ).T.astype(np.float32)).astype(np.float32)

    def _curve(self, values):
        if self._tone_table is None:
            x = np.linspace(0.0, 1.0, 4097)
            self._tone_table = np.interp(x, self.tone[:, 0], self.tone[:, 1]).astype(np.float32)
        index = np.clip(values, 0.0, 1.0) * 4096
        return self._tone_table[index.astype(np.int32)]

    @property
    def has_tone(self):
        return self.tone is not None


def _read(data):
    """{name: value} of the DCP tags in `data`."""
    if data[:4] == b"IIRC":
        endian = "<"
    elif data[:4] == b"MMCR":
        endian = ">"
    else:
        raise ProfileError("not a DCP file")
    (offset,) = struct.unpack(endian + "I", data[4:8])
    (count,) = struct.unpack(endian + "H", data[offset:offset + 2])
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8, 11: 4, 12: 8}
    formats = {3: "H", 4: "I", 9: "i", 11: "f", 12: "d"}
    found = {}
    for i in range(count):
        entry = data[offset + 2 + i * 12: offset + 14 + i * 12]
        tag, kind, number = struct.unpack(endian + "HHI", entry[:8])
        if tag not in _TAGS or kind not in sizes:
            continue
        size = sizes[kind] * number
        raw = entry[8:8 + size] if size <= 4 else data[struct.unpack(endian + "I", entry[8:12])[0]:][:size]
        if kind == 2:
            value = raw.split(b"\0")[0].decode("latin-1").strip()
        elif kind in (5, 10):
            pairs = struct.unpack(endian + ("ii" if kind == 10 else "II") * number, raw)
            value = [a / b if b else 0.0 for a, b in zip(pairs[::2], pairs[1::2], strict=True)]
        else:
            value = list(struct.unpack(endian + formats[kind] * number, raw))
        found[_TAGS[tag]] = value
    return found


def _matrix(values):
    return np.array(values, dtype=np.float64).reshape(3, 3) if values and len(values) == 9 else None


def load(path):
    """The Profile of a DCP file."""
    with open(path, "rb") as file:
        tags = _read(file.read())
    first, second = tags.get("illuminant1", [0])[0], tags.get("illuminant2", [0])[0]
    # The daylight half of a dual-illuminant profile: photos are mostly taken in daylight.
    pick = 2 if second in _D65_ILLUMINANTS and first not in _D65_ILLUMINANTS else 1
    if tags.get(f"forward_matrix{pick}") or tags.get("forward_matrix1"):
        matrix = _matrix(tags.get(f"forward_matrix{pick}") or tags.get("forward_matrix1"))
    else:
        colour = _matrix(tags.get(f"color_matrix{pick}") or tags.get("color_matrix1"))
        if colour is None:
            raise ProfileError("the profile has no colour matrix")
        matrix = np.linalg.inv(colour)
        # Camera white (1, 1, 1) to D50 white.
        matrix = matrix * (_D50 / (matrix @ np.ones(3)))[:, None]
    if matrix is None:
        raise ProfileError("the profile has no usable matrix")
    hue_sat = _map(tags.get("hsm_dims"), tags.get(f"hsm{pick}") or tags.get("hsm1"), tags.get("hsm_encoding"))
    look = _map(tags.get("look_dims"), tags.get("look"), tags.get("look_encoding"))
    tone = tags.get("tone_curve")
    tone = np.array(tone, dtype=np.float64).reshape(-1, 2) if tone and len(tone) >= 4 else None
    offset = tags.get("exposure_offset")
    return Profile(
        name=tags.get("name") or os.path.splitext(os.path.basename(path))[0],
        camera=tags.get("camera", ""),
        matrix=matrix,
        hue_sat=hue_sat,
        look=look,
        tone=tone,
        exposure=float(offset[0]) if offset else 0.0,
        path=path,
    )


def _map(dims, data, encoding):
    if not dims or not data or len(dims) < 3:
        return None
    hues, sats, vals = (max(1, int(v)) for v in dims[:3])
    if len(data) != hues * sats * vals * 3:
        return None
    table = np.array(data, dtype=np.float32).reshape(vals, hues, sats, 3)
    return (hues, sats, vals), table, bool(encoding and encoding[0] == 1)


def _apply_map(pro, dims, table, srgb_values):
    """A hue / saturation / value map applied to linear ProPhoto `pro` (…×3)."""
    hues, sats, vals = dims
    r, g, b = pro[..., 0], pro[..., 1], pro[..., 2]
    high = np.maximum(np.maximum(r, g), b)
    low = np.minimum(np.minimum(r, g), b)
    chroma = high - low
    safe = np.where(chroma > 1e-9, chroma, 1.0)
    hue = np.where(high == r, ((g - b) / safe) % 6, np.where(high == g, (b - r) / safe + 2, (r - g) / safe + 4))
    hue = np.where(chroma > 1e-9, hue, 0.0)
    sat = np.where(high > 1e-9, chroma / np.where(high > 1e-9, high, 1.0), 0.0)
    # Indices along each axis (hue wraps around), and the weights in between.
    h = hue / 6.0 * hues
    h0 = np.floor(h).astype(np.int32)
    fh = (h - h0).astype(np.float32)
    h0 %= hues
    h1 = (h0 + 1) % hues
    s = np.clip(sat, 0.0, 1.0) * (sats - 1)
    s0 = np.minimum(np.floor(s).astype(np.int32), max(0, sats - 2))
    fs = (s - s0).astype(np.float32) if sats > 1 else np.zeros_like(fh)
    s1 = np.minimum(s0 + 1, sats - 1)
    if vals > 1:
        value = np.clip(high, 0.0, 1.0)
        if srgb_values:
            value = np.where(value <= 0.0031308, value * 12.92, 1.055 * np.power(value, 1 / 2.4) - 0.055)
        v = value * (vals - 1)
        v0 = np.minimum(np.floor(v).astype(np.int32), vals - 2)
        fv = (v - v0).astype(np.float32)
        v1 = v0 + 1
    else:
        v0 = v1 = np.zeros_like(h0)
        fv = np.zeros_like(fh)
    result = np.zeros((*pro.shape[:-1], 3), dtype=np.float32)
    for vi, wv in ((v0, 1 - fv), (v1, fv)):
        for hi, wh in ((h0, 1 - fh), (h1, fh)):
            for si, ws in ((s0, 1 - fs), (s1, fs)):
                result += table[vi, hi, si] * (wv * wh * ws)[..., None]
    shift, scale, value_scale = result[..., 0], result[..., 1], result[..., 2]
    new_hue = (hue + shift / 60.0) % 6.0
    new_sat = np.clip(sat * scale, 0.0, 1.0)
    new_value = high * value_scale
    return _from_hsv(new_hue, new_sat, new_value).astype(np.float32)


def _from_hsv(hue, sat, value):
    sector = np.floor(hue).astype(np.int32) % 6
    f = hue - np.floor(hue)
    p = value * (1 - sat)
    q = value * (1 - sat * f)
    t = value * (1 - sat * (1 - f))
    r = np.choose(sector, [value, q, p, p, t, value])
    g = np.choose(sector, [t, value, value, q, p, p])
    b = np.choose(sector, [p, p, t, value, value, q])
    return np.stack([r, g, b], axis=-1)


# -- finding them -------------------------------------------------------------------

def normalise(text):
    """`text` lower case, words of letters and digits only: to compare camera names."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").casefold()).strip()


_norm = normalise


def camera_name(make, model):
    """A camera as DCPs name it ("Canon EOS R5", "Panasonic DC-G9"), from EXIF's make and model."""
    make, model = (make or "").strip(), (model or "").strip()
    brand = make.split()[0] if make else ""
    if brand and brand.casefold() in model.casefold():
        return model
    return f"{brand.capitalize() if brand.isupper() else brand} {model}".strip()


def matches(profile_camera, make, model):
    """Whether a profile made for `profile_camera` is for this camera."""
    wanted, given = _norm(profile_camera), _norm(camera_name(make, model))
    return bool(wanted) and (wanted == given or wanted.endswith(_norm(model)) and _norm(model) != "")


def profile_id(camera, name):
    return f"{camera}/{name}"


def scan_file(path):
    """{profile id: (path, display name, camera)} of one DCP file; {} if it can't be read."""
    try:
        with open(path, "rb") as file:
            tags = _read(file.read())
    except (OSError, ProfileError, struct.error, ValueError, IndexError) as exc:
        log.warning("Skipping colour profile %s: %s", path, exc)
        return {}
    name = tags.get("name") or os.path.splitext(os.path.basename(path))[0]
    camera = tags.get("camera", "")
    return {profile_id(camera, name): (path, name, camera)}


def scan(folders):
    """{profile id: (path, display name, camera)} of the DCPs under `folders` (and their sub-folders)."""
    found = {}
    for folder in folders:
        if not folder:
            continue
        for path in sorted(glob.glob(os.path.join(os.path.expanduser(folder), "**", "*.dcp"), recursive=True)):
            for key, value in scan_file(path).items():
                found.setdefault(key, value)
    return found


def default_folders():
    return [os.path.expanduser(folder) for folder in ADOBE_FOLDERS if os.path.isdir(os.path.expanduser(folder))]


def is_dcp(profile):
    return bool(profile) and profile != NEUTRAL


def describe(profile):
    """The display name of a profile id."""
    if not profile:
        return "Standard"
    if profile == NEUTRAL:
        return "Neutral"
    return profile.split("/", 1)[-1]

