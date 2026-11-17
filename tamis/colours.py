"""Colour tools of the edit, per pixel on sRGB-encoded values in [0, 1]: vibrance, the HSL mixer,
black and white, and colour grading.

The mixer works on eight ranges of hue. A pixel's hue lies between two of their
centres and takes a share of each, smoothly, so a gradient never breaks.
"""
import math

import numpy as np

COLOURS = ("red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta")
CENTRES = (0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 270.0, 300.0)  # degrees
_CENTRES_WRAPPED = np.array([*CENTRES, 360.0], dtype=np.float32)
HUE_REACH = 30.0  # degrees a hue slider at ±100 turns its range
GRADES = ("shadows", "midtones", "highlights", "global")
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def hsl_keys(colour):
    return f"hue_{colour}", f"sat_{colour}", f"lum_{colour}"


HSL_KEYS = tuple(key for colour in COLOURS for key in hsl_keys(colour))
BW_KEYS = tuple(f"bw_{colour}" for colour in COLOURS)
GRADE_KEYS = tuple(f"grade_{grade}_{part}" for grade in GRADES for part in ("hue", "sat", "lum"))


def hue_of(rgb):
    """Hue in degrees [0, 360), value (max) and saturation (chroma / max) of `rgb` (…×3)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    high = np.maximum(np.maximum(r, g), b)
    low = np.minimum(np.minimum(r, g), b)
    chroma = high - low
    safe = np.where(chroma > 1e-6, chroma, 1.0)
    hue = np.where(
        high == r, ((g - b) / safe) % 6,
        np.where(high == g, (b - r) / safe + 2, (r - g) / safe + 4),
    ) * 60.0
    hue = np.where(chroma > 1e-6, hue, 0.0).astype(np.float32)
    saturation = np.where(high > 1e-6, chroma / np.where(high > 1e-6, high, 1.0), 0.0).astype(np.float32)
    return hue, high, saturation


def from_hsv(hue, saturation, value):
    """RGB (…×3) from hue in degrees, saturation and value."""
    h = (hue % 360.0) / 60.0
    c = value * saturation
    x = c * (1 - np.abs(h % 2 - 1))
    m = value - c
    sector = np.floor(h).astype(np.int8) % 6
    zeros = np.zeros_like(c)
    r = np.choose(sector, [c, x, zeros, zeros, x, c])
    g = np.choose(sector, [x, c, c, x, zeros, zeros])
    b = np.choose(sector, [zeros, zeros, x, c, c, x])
    return np.stack([r + m, g + m, b + m], axis=-1)


def weights(hue):
    """(lower index, upper index, share of the upper) of the two ranges a hue lies between."""
    lower = np.clip(np.searchsorted(_CENTRES_WRAPPED, hue, side="right") - 1, 0, len(CENTRES) - 1)
    start = _CENTRES_WRAPPED[lower]
    span = _CENTRES_WRAPPED[lower + 1] - start
    t = np.clip((hue - start) / span, 0.0, 1.0)
    share = (t * t * (3 - 2 * t)).astype(np.float32)  # smooth on both sides of each centre
    return lower, (lower + 1) % len(CENTRES), share


def _mix(values, lower, upper, share):
    values = np.asarray(values, dtype=np.float32)
    return values[lower] * (1 - share) + values[upper] * share


def has_mixer(settings):
    return any(settings.get(key) for key in HSL_KEYS)


def apply_hsl(out, settings, vibrance=0.0, saturation_map=None):
    """Vibrance and the HSL mixer, in place on `out` (h×w×3, encoded).

    `vibrance` is −100…100, or an h×w array. Grey pixels have no hue, so the
    mixer leaves them alone.
    """
    hue, value, sat = hue_of(out)
    lower, upper, share = weights(hue)
    if has_mixer(settings):
        hues = [settings.get(f"hue_{c}", 0) for c in COLOURS]
        sats = [settings.get(f"sat_{c}", 0) for c in COLOURS]
        lums = [settings.get(f"lum_{c}", 0) for c in COLOURS]
        if any(hues):
            hue = hue + _mix(hues, lower, upper, share) * (HUE_REACH / 100)
        if any(sats):
            sat = sat * (1 + _mix(sats, lower, upper, share) / 100)
        if any(lums):
            # Brighter or darker, as much as the pixel has colour.
            value = value * np.exp2(_mix(lums, lower, upper, share) / 100 * 0.8 * np.minimum(1.0, sat * 1.5))
    if np.any(vibrance):
        amount = np.asarray(vibrance, dtype=np.float32) / 100
        # Muted colours gain more; skin tones (orange, around 25°) less.
        skin = np.exp(-(((hue % 360) - 25.0) / 18.0) ** 2) * (amount > 0)
        sat = sat * (1 + amount * (1 - sat) ** 2 * (1 - 0.6 * skin) * 1.6)
    np.copyto(out, from_hsv(hue, np.clip(sat, 0.0, 1.0), value))
    return out


def apply_bw(out, settings):
    """Black and white, in place: each colour range brighter or darker in grey as its slider says."""
    grey = out @ LUMA
    mix = [settings.get(key, 0) for key in BW_KEYS]
    if any(mix):
        hue, _value, sat = hue_of(out)
        lower, upper, share = weights(hue)
        grey = grey * np.exp2(_mix(mix, lower, upper, share) / 100 * 1.2 * np.minimum(1.0, sat * 1.5))
    out[...] = np.clip(grey, 0.0, 1.0)[..., None]
    return out


def tint(hue, amount):
    """The colour shift of a wheel set to `hue` (degrees) and `amount` (0–1): an RGB offset of no brightness."""
    angle = math.radians(hue)
    rgb = np.array(
        [math.cos(angle), math.cos(angle - 2 * math.pi / 3), math.cos(angle + 2 * math.pi / 3)], dtype=np.float32
    )
    rgb -= float(rgb @ LUMA)
    return rgb * np.float32(amount)


def has_grading(settings):
    return any(settings.get(key) for key in GRADE_KEYS if not key.endswith("_hue"))


def apply_grading(out, settings):
    """Colour grading, in place: a colour and a brightness for the shadows, midtones and highlights, and one for all."""
    luma = np.clip(out @ LUMA, 0.0, 1.0)
    balance = settings.get("grade_balance", 0) / 100
    blending = settings.get("grade_blending", 50) / 100
    width = 0.15 + 0.5 * blending  # how far each range spreads into its neighbours
    middle = 0.5 - 0.3 * balance  # positive balance: more of the picture counts as highlights
    shadows = np.clip(1 - luma / max(1e-3, middle * (0.5 + width)), 0.0, 1.0) ** 1.5
    highlights = np.clip((luma - middle * (1.5 - width)) / max(1e-3, 1 - middle * (1.5 - width)), 0.0, 1.0) ** 1.5
    midtones = np.clip(1 - shadows - highlights, 0.0, 1.0)
    for grade, weight in (("shadows", shadows), ("midtones", midtones), ("highlights", highlights), ("global", None)):
        sat = settings.get(f"grade_{grade}_sat", 0) / 100
        lum = settings.get(f"grade_{grade}_lum", 0) / 100
        if not sat and not lum:
            continue
        shift = np.zeros(3, dtype=np.float32)
        if sat:
            shift += tint(settings.get(f"grade_{grade}_hue", 0), sat * 0.25)
        if lum:
            shift += np.float32(lum * 0.2)
        if weight is None:
            out += shift
        else:
            out += weight[..., None] * shift
    np.clip(out, 0.0, 1.0, out=out)
    return out
