"""Quick, non-destructive edits: the processing pipeline.

Input is linear RGB (sRGB primaries) as float32. In order:

- linear: white balance, exposure, lens vignetting; then the settings that read
  a pixel's neighbourhood (local.py: noise reduction, dehaze, texture, clarity,
  sharpening, defringe); then local adjustments' exposure and white balance,
  shadows / highlights;
- display (sRGB-encoded): the tone table (whites / blacks, contrast, tone curves),
  saturation, vibrance and the HSL mixer, black and white, colour grading,
  film simulation LUT, vignette, grain.

Every per-pixel step depends only on a pixel and its position in the full image,
and the neighbourhood ones on rows no further than local.margin(), so a large
image can be processed in strips (export) with the same result.
An edit is a plain dict holding only the settings that differ from
DEFAULTS, which is also how it is stored in the session.

The framing (quarter turns, straightening, then the crop, which is in
fractions of the turned picture) is applied before all of this, so the vignette
and the grain follow the final frame: render() is given the framed pixels.
"""
import functools
import json
import math

import numpy as np

from . import colours, curves, lenses, local, masks, spots
from .lut import apply as apply_lut

MIN_CROP_SIZE = 0.005  # smallest stored crop side, as a fraction of the photo's

DEFAULTS = {
    "crop": None,  # [left, top, right, bottom], fractions of the turned photo's width and height
    "rotate": 0,  # quarter turns clockwise, in degrees: 0, 90, 180 or 270
    "straighten": 0.0,  # degrees clockwise; the picture is enlarged to fill the frame
    "exposure": 0.0,  # EV
    "temperature": 0,  # -100 cooler … +100 warmer
    "tint": 0,  # -100 greener … +100 more magenta
    "contrast": 0,
    "highlights": 0,
    "shadows": 0,
    "saturation": 0,
    "lut": None,  # film simulation: LUT id, see lut.scan()
    "lut_strength": 100,
    "grain": 0,
    "grain_size": 1,
    "vignette": 0,  # negative darkens the corners
    # How a RAW is developed (see PROCESS); edits made before process 2 keep 1, so they keep their look.
    "process": 2,
    # -- Advanced: what the edit panel's Advanced tab sets.
    "whites": 0,
    "blacks": 0,
    "vibrance": 0,
    **{key: 0 for key in colours.HSL_KEYS},  # hue_red, sat_red, lum_red, …
    "bw": 0,  # 1: black and white, mixed by bw_red, bw_orange, …
    **{key: 0 for key in colours.BW_KEYS},
    **{key: 0 for key in colours.GRADE_KEYS},  # grade_shadows_hue, grade_shadows_sat, grade_shadows_lum, …
    "grade_balance": 0,
    "grade_blending": 50,
    "curve_shadows": 0,
    "curve_darks": 0,
    "curve_lights": 0,
    "curve_highlights": 0,
    "curve_split1": 25,
    "curve_split2": 50,
    "curve_split3": 75,
    "curve": None,  # point curves, see curves.py
    "texture": 0,
    "clarity": 0,
    "dehaze": 0,
    "sharpen": None,  # None: local.SHARPEN_RAW for RAWs, none otherwise
    "sharpen_radius": 1.0,
    "sharpen_detail": 25,
    "sharpen_masking": 0,
    "nr_luma": 0,
    "nr_color": None,  # None: local.NR_COLOR_RAW for RAWs, none otherwise
    "defringe": 0,
    "lens_profile": 0,  # 1: corrections from the lens's profile (lenses.py)
    "lens_distortion": 100,  # how much of the profile's corrections, in %
    "lens_vignetting": 100,
    "distortion": 0,  # by hand: positive straightens barrel distortion
    "devignette": 0,  # by hand: positive brightens the corners the lens darkened
    "ca_red": 0,  # lateral chromatic aberration: red and blue scaled about the centre
    "ca_blue": 0,
    "profile": None,  # how a RAW's colours are rendered, see profiles.py; None: the standard matrix
    "masks": None,  # local adjustments, see masks.py
    "spots": None,  # removed spots, see spots.py
}
RANGES = {
    "straighten": (-15.0, 15.0),
    "exposure": (-5.0, 5.0),
    "temperature": (-100, 100),
    "tint": (-100, 100),
    "contrast": (-100, 100),
    "highlights": (-100, 100),
    "shadows": (-100, 100),
    "saturation": (-100, 100),
    "lut_strength": (0, 100),
    "grain": (0, 100),
    "grain_size": (1, 3),
    "vignette": (-100, 100),
    "process": (1, 2),
    "whites": (-100, 100),
    "blacks": (-100, 100),
    "vibrance": (-100, 100),
    **{key: (-100, 100) for key in colours.HSL_KEYS},
    "bw": (0, 1),
    **{key: (-100, 100) for key in colours.BW_KEYS},
    **{key: (0, 360) if key.endswith("_hue") else (0, 100) if key.endswith("_sat") else (-100, 100)
       for key in colours.GRADE_KEYS},
    "grade_balance": (-100, 100),
    "grade_blending": (0, 100),
    "curve_shadows": (-100, 100),
    "curve_darks": (-100, 100),
    "curve_lights": (-100, 100),
    "curve_highlights": (-100, 100),
    "curve_split1": (5, 45),
    "curve_split2": (30, 70),
    "curve_split3": (55, 95),
    "texture": (-100, 100),
    "clarity": (-100, 100),
    "dehaze": (-100, 100),
    "sharpen": (0, 150),
    "sharpen_radius": (0.5, 3.0),
    "sharpen_detail": (0, 100),
    "sharpen_masking": (0, 100),
    "nr_luma": (0, 100),
    "nr_color": (0, 100),
    "defringe": (0, 100),
    "lens_profile": (0, 1),
    "lens_distortion": (0, 200),
    "lens_vignetting": (0, 200),
    "distortion": (-100, 100),
    "devignette": (-100, 100),
    "ca_red": (-100, 100),
    "ca_blue": (-100, 100),
}
FLOATS = {"exposure": 2, "straighten": 1, "sharpen_radius": 1}  # settings with decimals, and how many
# Settings that aren't numbers, and how they are cleaned.
_CLEANERS = {"curve": curves.clean, "masks": masks.clean, "spots": spots.clean}

# The settings of the Advanced tab: an edit using any of them says so on the tab.
BASIC = (
    "crop", "rotate", "straighten", "exposure", "temperature", "tint", "contrast", "highlights", "shadows",
    "saturation", "lut", "lut_strength", "grain", "grain_size", "vignette", "process",
)
ADVANCED = tuple(key for key in DEFAULTS if key not in BASIC)

# What belongs to one photo's framing: not part of its look, so neither copied nor kept in a preset.
FRAMING = ("crop", "rotate", "straighten")
# What belongs to one photo, its framing and how it is developed, and what is drawn on it (local adjustments,
# removed spots): what copy, paste and presets leave alone.
OWN = (*FRAMING, "process", "masks", "spots")
# What the "before" view keeps: the photo as developed and framed, without anything that changes its look.
UNEDITED = (*FRAMING, "process")

# How RAWs are developed. 1: LibRaw's automatic brightness, which clips the brightest 1%
# and differs from photo to photo. 2: no automatic brightness and blended highlights,
# then a fixed gain and, in render(), a shoulder that rolls the highlights off instead of
# clipping them, so that Exposure and Highlights can bring them back. Gain and shoulder
# were fitted to camera JPEGs (Panasonic RW2), so that a RAW looks close to its preview.
PROCESS = 2
RAW_GAIN = 2.0 ** 2.5
RAW_SHOULDER = 0.2  # linear value where the roll-off starts

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

# sRGB transfer functions as lookup tables: a gather is far cheaper than pow().
_ENCODE_STEPS = 65535
_x = np.linspace(0.0, 1.0, _ENCODE_STEPS + 1, dtype=np.float64)
_ENCODE = np.where(_x <= 0.0031308, _x * 12.92, 1.055 * np.power(_x, 1 / 2.4) - 0.055).astype(np.float32)
# 16-bit sRGB -> linear, exact for 8-bit sources too (an 8-bit value v arrives as v * 257).
_v = np.arange(65536, dtype=np.float64) / 65535
DECODE_16BIT = np.where(_v <= 0.04045, _v / 12.92, np.power((_v + 0.055) / 1.055, 2.4)).astype(np.float32)
del _x, _v


def clean_crop(crop):
    """`crop` as stored: [left, top, right, bottom] in 0–1, or None if invalid or the whole photo."""
    if not isinstance(crop, (list, tuple)) or len(crop) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in crop):
        return None
    left, top, right, bottom = (round(min(1.0, max(0.0, float(v))), 5) for v in crop)
    if right - left < MIN_CROP_SIZE or bottom - top < MIN_CROP_SIZE:
        return None
    if (left, top, right, bottom) == (0.0, 0.0, 1.0, 1.0):
        return None
    return [left, top, right, bottom]


def crop_box(height, width, crop):
    """(top, bottom, left, right) in pixels of `crop` on an image of that size, at least 1×1."""
    left, top, right, bottom = crop
    x0, y0 = min(width - 1, round(left * width)), min(height - 1, round(top * height))
    return y0, max(y0 + 1, round(bottom * height)), x0, max(x0 + 1, round(right * width))


def crop_array(pixels, crop):
    """The `crop` of `pixels` (h×w×…), without copying; `pixels` itself when `crop` is None."""
    if not crop:
        return pixels
    top, bottom, left, right = crop_box(*pixels.shape[:2], crop)
    return pixels[top:bottom, left:right]


def look(edit):
    """`edit` without what belongs to its photo (see OWN): what can be carried over to another photo."""
    return {key: value for key, value in edit.items() if key not in OWN}


def own(edit):
    """Only what of `edit` belongs to its photo: its framing and how it is developed."""
    return {key: edit[key] for key in OWN if key in edit}


def unedited(edit):
    """`edit` without what changes the photo's look: its development, framed, for the "before" view."""
    return {key: edit[key] for key in UNEDITED if key in edit}


def advanced(edit):
    """Whether `edit` uses any setting of the Advanced tab."""
    return any(key in edit for key in ADVANCED if key != "process")


def framing(edit):
    """Only the framing of `edit`."""
    return {key: edit[key] for key in FRAMING if edit.get(key)}


def turned(edit, degrees):
    """`edit` after a further quarter turn (90 or -90): a crop stays on the same part of the picture."""
    crop = edit.get("crop")
    if crop:
        left, top, right, bottom = crop
        crop = [1 - bottom, left, 1 - top, right] if degrees > 0 else [top, 1 - right, bottom, 1 - left]
    return clean({**edit, "rotate": (edit.get("rotate", 0) + degrees) % 360, "crop": crop})


def framed_size(height, width, edit):
    """(height, width) of a picture of that size once `edit` has framed it."""
    if edit.get("rotate", 0) % 180:
        height, width = width, height
    if edit.get("crop"):
        top, bottom, left, right = crop_box(height, width, edit["crop"])
        return bottom - top, right - left
    return height, width


def turn_array(pixels, rotate):
    """`pixels` turned clockwise by `rotate` degrees, without copying."""
    return np.rot90(pixels, -(rotate // 90)) if rotate else pixels


def straighten_zoom(height, width, degrees):
    """How much a picture is enlarged so that, turned by `degrees`, it still fills its frame."""
    angle = math.radians(abs(degrees))
    return math.cos(angle) + math.sin(angle) * max(height / width, width / height)


def straighten_rows(pixels, degrees, top, bottom):
    """Rows `top` to `bottom` of `pixels` straightened by `degrees` clockwise, about the centre (bilinear)."""
    height, width = pixels.shape[:2]
    angle = math.radians(degrees)
    cos, sin = math.cos(angle), math.sin(angle)
    zoom = straighten_zoom(height, width, degrees)
    centre_x, centre_y = (width - 1) / 2, (height - 1) / 2
    dx = np.arange(width, dtype=np.float32) - centre_x
    dy = (np.arange(top, bottom, dtype=np.float32) - centre_y)[:, None]
    # Where each pixel of the result comes from: the turn and the enlargement, inverted.
    x = np.clip((cos * dx + sin * dy) / zoom + centre_x, 0, width - 1)
    y = np.clip((cos * dy - sin * dx) / zoom + centre_y, 0, height - 1)
    x0, y0 = x.astype(np.intp), y.astype(np.intp)
    x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    upper = pixels[y0, x0] * (1 - fx) + pixels[y0, x1] * fx
    lower = pixels[y1, x0] * (1 - fx) + pixels[y1, x1] * fx
    out = upper * (1 - fy) + lower * fy
    return (out + 0.5).astype(pixels.dtype) if np.issubdtype(pixels.dtype, np.integer) else out


def clean(edit):
    """`edit` without default or invalid values: what is stored. {} means no edit."""
    result = {}
    if not isinstance(edit, dict):
        return result
    for key, default in DEFAULTS.items():
        value = edit.get(key, default)
        if key == "crop":
            crop = clean_crop(value)
            if crop:
                result[key] = crop
            continue
        if key == "rotate":
            valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
            if valid and value % 90 == 0 and value % 360:
                result[key] = int(value % 360)
            continue
        if key in ("lut", "profile"):
            if isinstance(value, str) and value:
                result[key] = value
            continue
        if key in _CLEANERS:
            cleaned = _CLEANERS[key](value)
            if cleaned:
                result[key] = cleaned
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        low, high = RANGES[key]
        value = min(high, max(low, value))
        value = round(float(value), FLOATS[key]) if key in FLOATS else int(round(value))
        if value != default:
            result[key] = value
    if set(result) == {"process"}:
        return {}  # nothing else is edited: the photo isn't, and gets the current process when it is
    return result


def settings(edit):
    """Every setting of `edit`, defaults included."""
    return {**DEFAULTS, **clean(edit)}


def encode(linear):
    """Linear [0, 1] -> sRGB-encoded [0, 1] (values outside are clipped)."""
    index = (np.clip(linear, 0.0, 1.0) * _ENCODE_STEPS).astype(np.uint16)
    return _ENCODE[index]


def shoulder(rgb, out=None, start=RAW_SHOULDER):
    """The highlight roll-off of RAWs (process 2): unchanged below `start`, then towards 1, never reaching it.

    With `out` (which may be `rgb` itself) the result is written there.
    """
    t = np.float32(start)
    above = rgb > t
    if out is None:
        out = rgb.copy()
    elif out is not rgb:
        np.copyto(out, rgb)
    if not above.any():
        return out
    # t + (1 - t) * -expm1(-(max(rgb, t) - t) / (1 - t)), with a single temporary array.
    rolled = np.maximum(rgb, t)
    rolled -= t
    np.negative(rolled, out=rolled)
    rolled /= 1 - t
    np.expm1(rolled, out=rolled)
    np.negative(rolled, out=rolled)
    rolled *= 1 - t
    rolled += t
    np.copyto(out, rolled, where=above)
    return out


def _smoothstep(low, high, x):
    t = np.clip((x - low) / (high - low), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _white_balance(temperature, tint):
    """Per-channel gains for a relative white balance shift, at constant brightness."""
    warm = temperature / 100 * 0.6
    magenta = tint / 100 * 0.4
    gains = np.exp2(np.array([warm, -magenta, -warm], dtype=np.float32))
    return gains / float(gains @ LUMA)


def _white_balance_map(temperature, tint):
    """_white_balance() per pixel: `temperature` and `tint` are h×w arrays (or numbers); returns h×w×3 gains."""
    warm = np.asarray(temperature, dtype=np.float32) / 100 * 0.6
    magenta = np.asarray(tint, dtype=np.float32) / 100 * 0.4
    gains = np.stack(np.broadcast_arrays(np.exp2(warm), np.exp2(-magenta), np.exp2(-warm)), axis=-1)
    gains /= (gains @ LUMA)[..., None]
    return gains


def _contrast_curve(x, contrast):
    """The contrast S-curve on encoded values; `contrast` may be an array of the same shape."""
    power = np.exp2(np.asarray(contrast, dtype=np.float32) / 100)  # 0.5 (flat) … 2 (punchy); 0, 0.5 and 1 stay put
    return np.where(x < 0.5, 0.5 * np.power(2 * x, power), 1 - 0.5 * np.power(np.maximum(2 - 2 * x, 0), power))


def _ends(x, whites, blacks):
    """Whites and blacks on encoded values: they move the brightest and darkest tones, and the ends with them."""
    if np.any(blacks):
        x = x + np.asarray(blacks, dtype=np.float32) / 100 * 0.15 * (1 - np.clip(x, 0, 1)) ** 4
    if np.any(whites):
        x = x + np.asarray(whites, dtype=np.float32) / 100 * 0.15 * np.clip(x, 0, 1) ** 4
    return x


_TONE_KEYS = ("contrast", "whites", "blacks", *curves.PARAMETRIC, *curves.SPLITS, "curve")


def _tone_table(contrast):
    """encode() followed by the contrast S-curve, as one table indexed like encode()."""
    return _tone_tables({"contrast": contrast})[0]


def _tone_tables(s):
    """encode() followed by whites / blacks, contrast and the tone curves: (red, green, blue) tables indexed like encode().

    The three are the same table when no channel has a curve of its own.
    """
    key = json.dumps({k: s.get(k) for k in _TONE_KEYS}, sort_keys=True)
    return _cached_tables(key)


@functools.lru_cache(maxsize=8)
def _cached_tables(key):
    s = json.loads(key)
    if not any(s.get(k) for k in ("contrast", "whites", "blacks", "curve", *curves.PARAMETRIC)):
        return _ENCODE, _ENCODE, _ENCODE
    x = _ENCODE.astype(np.float64)
    x = np.clip(_ends(x, s.get("whites") or 0, s.get("blacks") or 0), 0.0, 1.0)
    if s.get("contrast"):
        x = _contrast_curve(x, s["contrast"])
    rgb, *channels = curves.channel_tables(s, x)
    base = x if rgb is None else rgb
    tables = [np.clip(c if c is not None else base, 0.0, 1.0).astype(np.float32) for c in channels]
    if all(c is None for c in channels):
        return tables[0], tables[0], tables[0]
    return tuple(tables)


def _grain(shape, origin, full_width, amount, size, seed):
    """Gaussian grain of `shape` (h, w), in cells that scale with the full image width."""
    height, width = shape
    cell = max(1, round(full_width / 2000 * size))
    top, left = origin
    first_row, row_offset = divmod(top, cell)
    first_col, col_offset = divmod(left, cell)
    rows = -(-(height + row_offset) // cell)
    cols = -(-(width + col_offset) // cell)
    rng = np.random.default_rng([seed, first_row, first_col])
    noise = rng.standard_normal((rows, cols), dtype=np.float32)
    if cell > 1:
        noise = np.repeat(np.repeat(noise, cell, axis=0), cell, axis=1)
    return noise[row_offset:row_offset + height, col_offset:col_offset + width] * (amount / 100 * 0.12)


def margin(edit, full_size, raw=False):
    """Rows above and below a block that render() needs to give it as part of a picture of `full_size`."""
    s = settings(edit)
    local_keys = {key for mask in masks.active(s["masks"]) for key in mask["settings"] if key in masks.NEIGHBOURHOOD}
    return local.margin(s, max(full_size), raw, local_keys, edit.get("_view"))


def render(linear, edit, table=None, seed=0, origin=(0, 0), full_size=None, raw=False, bits=8, pad=(0, 0),
           rasters=None, lens=None):
    """Apply `edit` to `linear` (float32, h×w×3); returns sRGB uint8 h×w×3 (uint16 with `bits` 16).

    `raw` says that `linear` is a RAW's development, which process 2 rolls off
    in the highlights. `table` is the film simulation LUT from lut.build_table() (None: no LUT).
    `origin` (row, column) and `full_size` (height, width) place this block
    in the full image when rendering in strips. `pad` (rows above, rows below) are
    extra rows of `linear` around the block, for the settings that read a
    neighbourhood (see margin()); they are not part of the result. `rasters`
    are the automatic masks the edit uses ({kind: h×w array over the source}),
    and `lens` the lens's profile (lenses.find()).
    """
    s = settings(edit)
    pad_top, pad_bottom = pad
    height, width = linear.shape[0] - pad_top - pad_bottom, linear.shape[1]
    full_height, full_width = full_size or (height, width)
    view = edit.get("_view")  # what to show instead of the photo, see local.apply()

    # Every step works in place on `rgb`, then `out`, so a large image is not copied at each step.
    rgb = np.multiply(linear, _white_balance(s["temperature"], s["tint"]) * np.float32(2.0 ** s["exposure"]))

    if lenses.has_vignetting(s, lens):
        geometry = masks.Geometry(edit, (full_height, full_width))
        x, y = geometry.pixel_coords(origin[0] - pad_top, rgb.shape[0])
        rgb *= lenses.vignette_gain(s, lens, geometry, x, y)[..., None]
        del x, y

    maps = masks.local_maps(s["masks"], edit, (full_height, full_width), origin[0], height, rasters)
    neighbourhood = {key: maps[key] for key in masks.NEIGHBOURHOOD if key in maps}
    if neighbourhood or local.amounts(s, raw) or view in ("sharpen_mask", "spots"):
        rgb = local.apply(rgb, s, max(full_height, full_width), raw, pad, neighbourhood, view)
        if view in ("sharpen_mask", "spots"):
            encoded = _encode_in_place(rgb)
            return _to_uint16(encoded) if bits == 16 else _to_uint8(encoded)
    elif pad_top or pad_bottom:
        rgb = rgb[pad_top:pad_top + height]

    if "exposure" in maps or "temperature" in maps or "tint" in maps:
        gains = _white_balance_map(maps.get("temperature", 0), maps.get("tint", 0))
        if "exposure" in maps:
            gains = gains * np.exp2(maps["exposure"])[..., None]
        rgb *= gains
        del gains

    highlights = s["highlights"] + maps.get("highlights", 0)
    shadows = s["shadows"] + maps.get("shadows", 0)
    if np.any(highlights) or np.any(shadows):
        perceived = _encode_in_place(rgb @ LUMA)
        stops = np.zeros((height, width), dtype=np.float32)
        if np.any(highlights):
            stops += highlights / 100 * _smoothstep(0.45, 1.0, perceived)
        if np.any(shadows):
            stops += shadows / 100 * 1.5 * (1 - _smoothstep(0.0, 0.5, perceived))
        del perceived
        np.exp2(stops, out=stops)
        rgb *= stops[..., None]
        del stops

    if raw and raw != "profiled" and edit.get("process", PROCESS) >= 2:  # (not settings(): clean() drops a process left alone)
        if raw == "neutral":  # flatter: a little darker, rolled off from lower down
            rgb *= np.float32(0.85)
            shoulder(rgb, out=rgb, start=RAW_SHOULDER * 0.6)
        else:
            shoulder(rgb, out=rgb)

    tones = _tone_tables(s)
    np.clip(rgb, 0.0, 1.0, out=rgb)
    rgb *= _ENCODE_STEPS
    index = rgb.astype(np.uint16)
    del rgb  # freed before the result is allocated (np.take(out=) would copy the index to 64 bits)
    if tones[0] is tones[1] is tones[2]:
        out = tones[0][index]
    else:
        out = np.empty(index.shape, dtype=np.float32)
        for channel in range(3):
            out[..., channel] = tones[channel][index[..., channel]]
    del index

    if "whites" in maps or "blacks" in maps:
        out = _ends(out, maps.get("whites", 0)[..., None] if "whites" in maps else 0,
                    maps.get("blacks", 0)[..., None] if "blacks" in maps else 0).astype(np.float32)
        np.clip(out, 0.0, 1.0, out=out)
    if "contrast" in maps:
        out = _contrast_curve(np.clip(out, 0.0, 1.0), maps["contrast"][..., None]).astype(np.float32)

    saturation = s["saturation"] + maps.get("saturation", 0)
    if np.any(saturation):
        grey = (out @ LUMA)[..., None]
        out -= grey
        out *= (1 + np.asarray(saturation, dtype=np.float32) / 100)[..., None] if np.ndim(saturation) else np.float32(
            1 + saturation / 100)
        out += grey
        np.clip(out, 0.0, 1.0, out=out)
        del grey

    if s["vibrance"] or colours.has_mixer(s):
        colours.apply_hsl(out, s, s["vibrance"])
        np.clip(out, 0.0, 1.0, out=out)

    if s["bw"]:
        colours.apply_bw(out, s)

    if colours.has_grading(s):
        colours.apply_grading(out, s)

    if table is not None and s["lut_strength"]:
        graded = apply_lut(_to_uint8(out.copy()), table).astype(np.float32)
        graded /= 255
        strength = s["lut_strength"] / 100
        if strength >= 1:
            out = graded
        else:
            graded -= out
            graded *= np.float32(strength)
            out += graded
        del graded

    if s["vignette"]:
        rows = (np.arange(origin[0], origin[0] + height, dtype=np.float32) + 0.5) / full_height * 2 - 1
        cols = (np.arange(origin[1], origin[1] + width, dtype=np.float32) + 0.5) / full_width * 2 - 1
        radius = (rows[:, None] ** 2 + cols[None, :] ** 2) / 2  # 0 at the centre, 1 in the corners
        out *= (1 + s["vignette"] / 100 * 0.9 * _smoothstep(0.1, 1.0, radius))[..., None]

    if s["grain"]:
        noise = _grain((height, width), origin, full_width, s["grain"], s["grain_size"], seed)
        luma = np.clip(out @ LUMA, 0.0, 1.0)
        out += (noise * (0.35 + 2.6 * luma * (1 - luma)))[..., None]  # strongest in the midtones

    np.clip(out, 0.0, 1.0, out=out)
    return _to_uint16(out) if bits == 16 else _to_uint8(out)


def render_rows(linear, top, bottom, edit, table=None, seed=0, full_size=None, raw=False, bits=8, rasters=None,
                lens=None, extra=None):
    """render() of rows `top` to `bottom` of the whole picture `linear`, with the margin they need around them.

    `extra` is the margin, when already known.
    """
    full_size = full_size or linear.shape[:2]
    extra = margin(edit, full_size, raw) if extra is None else extra
    first, last = max(0, top - extra), min(linear.shape[0], bottom + extra)
    return render(linear[first:last], edit, table, seed, (top, 0), full_size, raw, bits, (top - first, last - bottom),
                  rasters, lens)


def _encode_in_place(linear):
    """encode(), overwriting `linear` (float32) on the way."""
    np.clip(linear, 0.0, 1.0, out=linear)
    linear *= _ENCODE_STEPS
    return _ENCODE[linear.astype(np.uint16)]


def _to_uint16(values):
    """[0, 1] floats -> uint16, rounded; overwrites `values` on the way."""
    values *= 65535
    values += 0.5
    return values.astype(np.uint16)


def _to_uint8(values):
    """[0, 1] floats -> uint8, rounded; overwrites `values` on the way."""
    values *= 255
    values += 0.5
    return values.astype(np.uint8)
