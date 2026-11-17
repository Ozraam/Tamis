"""Settings of the edit that read a pixel's neighbourhood: noise reduction, dehaze, texture, clarity,
sharpening and defringe.

They run first in develop.render(), on linear RGB. Their radii are fractions of
the framed picture's long edge, so a preview and a full-size export look the same.
To be rendered in strips, a block comes with `margin()` extra rows above and
below (fewer at the picture's own edges), which are dropped once filtered: each
filter only reads that far, and mirrors the picture at its edges.
"""
import math

import numpy as np

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
REFERENCE = 6000  # long edge, in pixels, at which a sharpening radius is in pixels
SHARPEN_RAW = 25  # sharpening and colour noise reduction RAWs get unless set
NR_COLOR_RAW = 25
KEYS = ("nr_luma", "nr_color", "dehaze", "texture", "clarity", "sharpen", "defringe")


# -- filters ---------------------------------------------------------------------

def box(a, radius, axis):
    """Mean over 2 × radius + 1 values along `axis`, the array mirrored at its ends."""
    if radius < 1:
        return a
    n = a.shape[axis]
    pad = [(0, 0)] * a.ndim
    pad[axis] = (radius + 1, radius)
    padded = np.pad(a, pad, mode="symmetric" if n <= radius + 1 else "reflect")
    sums = np.cumsum(padded, axis=axis, dtype=np.float32)
    window = 2 * radius + 1
    upper = np.take(sums, np.arange(window, window + n), axis=axis)
    lower = np.take(sums, np.arange(0, n), axis=axis)
    upper -= lower
    upper /= window
    return upper


def box2d(a, radius):
    return box(box(a, radius, 0), radius, 1)


def _gaussian_kernel(sigma):
    reach = max(1, math.ceil(3 * sigma))
    x = np.arange(-reach, reach + 1, dtype=np.float32)
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    return kernel / kernel.sum(), reach


def _box_radius(sigma):
    return max(1, round((math.sqrt(4 * sigma * sigma + 1) - 1) / 2))


def gaussian_reach(sigma):
    """How many rows away gaussian() reads."""
    if sigma <= 0.05:
        return 0
    if sigma < 2:
        return _gaussian_kernel(sigma)[1]
    return 3 * _box_radius(sigma)


def gaussian(a, sigma):
    """A gaussian blur of `a` (h×w or h×w×c): exact for small radii, three box blurs otherwise."""
    if sigma <= 0.05:
        return a
    if sigma < 2:
        kernel, reach = _gaussian_kernel(sigma)
        out = a
        for axis in (0, 1):
            pad = [(0, 0)] * a.ndim
            pad[axis] = (reach, reach)
            padded = np.pad(out, pad, mode="symmetric")
            n = out.shape[axis]
            result = np.zeros_like(out, dtype=np.float32)
            for i, weight in enumerate(kernel):
                result += weight * np.take(padded, np.arange(i, i + n), axis=axis)
            out = result
        return out
    radius = _box_radius(sigma)
    out = a
    for _ in range(3):
        out = box2d(out, radius)
    return out


def guided(guide, radius, eps):
    """He's guided filter of `guide` by itself: smooths within regions and keeps their edges."""
    mean = box2d(guide, radius)
    variance = box2d(guide * guide, radius) - mean * mean
    a = variance / (variance + eps)
    b = mean - a * mean
    return box2d(a, radius) * guide + box2d(b, radius)


def soft_min(values, radius, sharpness=24.0):
    """A smooth local minimum: −log(mean(exp(−k·v)))/k over the neighbourhood."""
    return -np.log(np.maximum(box2d(np.exp(-sharpness * values), radius), 1e-30)) / sharpness


# -- the settings ---------------------------------------------------------------------

def amounts(settings, raw):
    """{key: amount} of the neighbourhood settings in use, with the RAW defaults filled in."""
    found = {}
    for key in KEYS:
        value = settings.get(key)
        if value is None:
            value = (SHARPEN_RAW if key == "sharpen" else NR_COLOR_RAW if key == "nr_color" else 0) if raw else 0
        if value:
            found[key] = value
    return found


def radii(settings, size):
    """{key: radius in pixels} of each filter, for a framed picture whose long edge is `size`."""
    scale = size / REFERENCE
    return {
        "nr_luma": max(1, round(0.0009 * size)),
        "nr_color": max(0.6, 0.004 * size),
        "dehaze": max(2, round(0.01 * size)),
        "texture": max(0.6, 0.0015 * size),
        "clarity": max(2, round(0.012 * size)),
        "sharpen": max(0.1, settings.get("sharpen_radius", 1.0) * scale),
        "defringe": max(1, round(0.0006 * size)),
    }


def _reach(key, radius):
    if key in ("nr_color", "texture", "sharpen"):
        return gaussian_reach(radius) + (2 if key == "sharpen" else 0)
    if key in ("nr_luma", "clarity"):
        return 2 * radius
    if key == "dehaze":
        return 3 * radius
    return 2 * radius + 1  # defringe


def spots_radius(size):
    return max(1.0, 0.003 * size)


def margin(settings, size, raw, local_keys=(), view=None):
    """Rows a block needs above and below to be filtered as part of a picture with that long edge."""
    if view == "spots":
        return gaussian_reach(spots_radius(size))
    used = set(amounts(settings, raw)) | set(local_keys) | ({"sharpen"} if view == "sharpen_mask" else set())
    if not used:
        return 0
    found = radii(settings, size)
    return sum(_reach(key, found[key]) for key in KEYS if key in used)


def _gradient(a):
    """|∂a/∂y| + |∂a/∂x|, from each pixel's previous neighbour (itself at the picture's first row and column)."""
    dy = np.abs(np.diff(a, axis=0, prepend=a[:1]))
    dy += np.abs(np.diff(a, axis=1, prepend=a[:, :1]))
    return dy


def _luma(rgb):
    return np.maximum(rgb @ LUMA, 0.0)


def _perceptual(linear):
    return np.power(np.maximum(linear, 0.0), 1 / 2.2, dtype=np.float32)


def _scale_luma(rgb, before, after):
    """`rgb` with its luminance taken from `before` to `after` (both linear), keeping its colour."""
    ratio = (after + 1e-5) / (before + 1e-5)
    rgb *= np.clip(ratio, 0.0, 16.0)[..., None]
    return rgb


def apply(rgb, settings, size, raw, pad, maps=None, view=None):
    """The neighbourhood settings on `rgb` (linear, with `pad` = (rows above, rows below) of margin).

    Returns the block without its margin. `maps` gives per-pixel extra amounts
    for the block's own rows ({key: h×w}, from local adjustments). With `view`
    "sharpen_mask", returns the sharpening mask as a grey picture instead.
    """
    maps = maps or {}
    found = amounts(settings, raw)
    used = [key for key in KEYS if key in found or key in maps or (key == "sharpen" and view == "sharpen_mask")]
    top, bottom = pad
    rows = rgb.shape[0] - top - bottom
    radius = radii(settings, size)
    if view == "spots":
        # The details alone, much enhanced, in grey: dust and blemishes stand out as dark dots.
        encoded = _perceptual(_luma(rgb))
        detail = encoded - gaussian(encoded, spots_radius(size))
        grey = np.clip(0.5 + detail * 6, 0.0, 1.0)[top:top + rows]
        return np.repeat(np.power(grey, 2.2, dtype=np.float32)[..., None], 3, axis=2)

    def amount(key, cropped=True):
        """The amount of `key` for the rows filtered: per pixel where masks change it."""
        value = np.float32(found.get(key, 0) / 100)
        extra = maps.get(key)
        if extra is None:
            return value
        if cropped:
            return value + extra / 100
        full = np.full((rgb.shape[0], rgb.shape[1]), value, dtype=np.float32)
        full[top:top + rows] += extra / 100
        return full

    for key in used:
        if key == "nr_luma":
            luma = _luma(rgb)
            smooth_in = _perceptual(luma)
            k = amount(key, cropped=False)
            eps = np.float32(0.0004) + 0.006 * np.maximum(np.max(k), 0) ** 2
            smoothed = guided(smooth_in, radius[key], eps)
            target = smooth_in + (smoothed - smooth_in) * np.clip(k, 0.0, 1.0)
            rgb = _scale_luma(rgb, luma, np.power(np.maximum(target, 0.0), 2.2))
        elif key == "nr_color":
            luma = _luma(rgb)[..., None]
            ratio = rgb / (luma + 0.01)
            blurred = gaussian(ratio, radius[key])
            k = np.clip(amount(key, cropped=False), 0.0, 1.0)
            k = k[..., None] if np.ndim(k) else k
            rgb = (ratio + (blurred - ratio) * k) * (luma + 0.01)
        elif key == "dehaze":
            encoded = _perceptual(rgb)
            dark = soft_min(encoded.min(axis=2), radius[key])
            dark = box2d(dark, radius[key])
            k = amount(key, cropped=False)
            k = k[..., None] if np.ndim(k) else k
            atmosphere = np.float32(0.95)
            dark3 = dark[..., None]
            transmission = np.clip(1 - 0.9 * np.maximum(k, 0) * dark3 / atmosphere, 0.2, 1.0)
            cleared = (encoded - atmosphere) / transmission + atmosphere
            hazed = encoded + (atmosphere - encoded) * 0.6 * np.maximum(-k, 0) * (1 - dark3 * 0.5)
            encoded = np.where(k >= 0, cleared, hazed) if np.ndim(k) else (cleared if k >= 0 else hazed)
            rgb = np.power(np.clip(encoded, 0.0, None), 2.2, dtype=np.float32)
        elif key in ("texture", "clarity"):
            luma = _luma(rgb)
            log = np.log2(luma + 1e-3)
            if key == "texture":
                base = gaussian(log, radius[key])
                strength = 0.9
            else:
                base = guided(log, radius[key], np.float32(0.25))
                strength = 0.8
            detail = log - base
            k = amount(key, cropped=False)
            encoded = _perceptual(luma)
            # Clarity works on the midtones, leaving the deepest shadows and brightest highlights alone.
            weight = np.clip(4 * encoded * (1 - encoded), 0.0, 1.0) if key == "clarity" else 1.0
            boosted = log + detail * k * strength * weight
            rgb = _scale_luma(rgb, luma, np.exp2(boosted) - 1e-3)
        elif key == "sharpen":
            luma = _luma(rgb)
            encoded = _perceptual(luma)
            blurred = gaussian(encoded, radius[key])
            detail = encoded - blurred
            # Detail: low values hold back the smallest variations (noise), high ones keep everything.
            low = (1 - settings.get("sharpen_detail", 25) / 100) * 0.02
            if low > 0:
                detail = detail * np.clip(np.abs(detail) / low, 0.0, 1.0)
            mask = 1.0
            masking = settings.get("sharpen_masking", 0)
            if masking or view == "sharpen_mask":
                edges = box2d(_gradient(blurred), 1)
                threshold = masking / 100 * 0.04
                mask = np.clip((edges - threshold * 0.5) / max(threshold, 1e-4), 0.0, 1.0) if masking else np.ones_like(edges)
                if view == "sharpen_mask":
                    grey = mask[top:top + rows]
                    return np.repeat(np.power(grey, 2.2, dtype=np.float32)[..., None], 3, axis=2)
            k = amount(key, cropped=False)
            detail = np.clip(detail, -0.08, 0.08)  # holds back halos
            sharpened = encoded + detail * k * 1.8 * mask
            rgb = _scale_luma(rgb, luma, np.power(np.maximum(sharpened, 0.0), 2.2))
        elif key == "defringe":
            luma = _luma(rgb)
            encoded = _perceptual(luma)
            edges = np.abs(encoded - box2d(encoded, radius[key]))
            edges = box2d(edges, radius[key] + 1) * 12
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
            purple = (b > g) & (r > g * 0.9) & (b > r * 0.6)
            green = (g > r) & (g > b)
            fringe = np.clip(edges, 0.0, 1.0) * (purple | green)
            k = np.clip(amount(key, cropped=False), 0.0, 1.0)
            keep = (1 - fringe * k)[..., None]
            grey = luma[..., None]
            rgb = grey + (rgb - grey) * keep
        rgb = rgb.astype(np.float32, copy=False)
    if view == "sharpen_mask":
        return np.ones((rows, rgb.shape[1], 3), dtype=np.float32)
    return np.ascontiguousarray(rgb[top:top + rows])
