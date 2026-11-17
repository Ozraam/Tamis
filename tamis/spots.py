"""Removing spots and blemishes: each spot is covered with another part of the picture.

A spot is stored in the edit's "spots" list as

    {"points": [[x, y], …], "r": 0.01, "dx": 0.03, "dy": 0.0, "mode": "heal"}

`points` are the centres of the circles it is made of (one for a click, more
for a painted stroke), in fractions of the source's width and height (the
photo before it is turned, straightened or cropped); `r` is their radius, a
fraction of the long edge; (`dx`, `dy`) is where the patch comes from, as an
offset in the same fractions. "clone" copies it as it is; "heal" (the
default) also takes on the brightness and colour around the spot.

Spots are applied to the developed photo before it is framed (apply()), so
neither a turn nor a crop moves them.
"""
import math

import numpy as np

from . import local

MAX_SPOTS = 500
MAX_POINTS = 2000
FEATHER = 0.35  # share of the radius over which a spot's edge fades


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return round(min(high, max(low, float(value))), 5)


def clean_spot(spot):
    if not isinstance(spot, dict):
        return None
    points = []
    for point in (spot.get("points") or [])[:MAX_POINTS]:
        if isinstance(point, (list, tuple)) and len(point) == 2:
            x, y = _number(point[0], 0.0, 1.0), _number(point[1], 0.0, 1.0)
            if x is not None and y is not None:
                points.append([x, y])
    radius = _number(spot.get("r"), 0.0005, 0.25)
    dx, dy = _number(spot.get("dx", 0), -1.0, 1.0), _number(spot.get("dy", 0), -1.0, 1.0)
    if not points or radius is None or dx is None or dy is None or (dx, dy) == (0.0, 0.0):
        return None
    result = {"points": points, "r": radius, "dx": dx, "dy": dy}
    if spot.get("mode") == "clone":
        result["mode"] = "clone"
    return result


def clean(spots):
    """The "spots" setting as stored; None when there is none."""
    if not isinstance(spots, list):
        return None
    result = [s for s in map(clean_spot, spots[:MAX_SPOTS]) if s]
    return result or None


def _soft_disc(height, width, centres, radius):
    """The union of soft-edged discs (h×w, 0–1) at `centres` (pixels, in this patch)."""
    rows = np.arange(height, dtype=np.float32)[:, None]
    cols = np.arange(width, dtype=np.float32)[None, :]
    mask = np.zeros((height, width), dtype=np.float32)
    inner = radius * (1 - FEATHER)
    for cx, cy in centres:
        distance = np.hypot(cols - cx, rows - cy)
        t = np.clip((radius - distance) / max(radius - inner, 1e-3), 0.0, 1.0)
        np.maximum(mask, t * t * (3 - 2 * t), out=mask)
    return mask


def _spot_pixels(spot, height, width):
    """(centres in pixels, radius in pixels, offset in pixels) of `spot` on a picture of that size."""
    long = max(height, width)
    centres = [(x * width - 0.5, y * height - 0.5) for x, y in _dabs(spot, height, width)]
    return centres, max(1.0, spot["r"] * long), (spot["dx"] * width, spot["dy"] * height)


def _dabs(spot, height, width):
    """The spot's points, with more in between those of a stroke, so it has no gaps."""
    points = spot["points"]
    radius = spot["r"] * max(height, width)
    found = [points[0]]
    for (ax, ay), (bx, by) in zip(points, points[1:], strict=False):
        length = math.hypot((bx - ax) * width, (by - ay) * height)
        steps = int(length / max(1.0, radius * 0.4))
        found.extend((ax + (bx - ax) * i / (steps + 1), ay + (by - ay) * i / (steps + 1)) for i in range(1, steps + 1))
        found.append((bx, by))
    return found


def apply(pixels, spots):
    """`pixels` (h×w×3, a developed source) with `spots` covered; a copy, or `pixels` itself when there is none."""
    if not spots:
        return pixels
    out = np.array(pixels, copy=True)
    height, width = out.shape[:2]
    integer = np.issubdtype(out.dtype, np.integer)
    for spot in spots:
        centres, radius, (offset_x, offset_y) = _spot_pixels(spot, height, width)
        reach = radius + 2
        heal = spot.get("mode") != "clone"
        blur = max(1.0, radius * 0.5)
        border = int(math.ceil(local.gaussian_reach(blur))) if heal else 0
        x0 = int(min(cx for cx, _ in centres) - reach) - border
        x1 = int(max(cx for cx, _ in centres) + reach) + 1 + border
        y0 = int(min(cy for _, cy in centres) - reach) - border
        y1 = int(max(cy for _, cy in centres) + reach) + 1 + border
        # The patch must come from inside the picture: the offset is shortened if it doesn't.
        ox = int(round(min(max(offset_x, -x0), width - x1)))
        oy = int(round(min(max(offset_y, -y0), height - y1)))
        dx0, dx1, dy0, dy1 = max(0, x0), min(width, x1), max(0, y0), min(height, y1)
        sx0, sy0 = dx0 + ox, dy0 + oy
        sx1, sy1 = dx1 + ox, dy1 + oy
        if sx0 < 0 or sy0 < 0 or sx1 > width or sy1 > height or dx0 >= dx1 or dy0 >= dy1:
            continue
        target = out[dy0:dy1, dx0:dx1].astype(np.float32)
        source = out[sy0:sy1, sx0:sx1].astype(np.float32)
        mask = _soft_disc(dy1 - dy0, dx1 - dx0, [(cx - dx0, cy - dy0) for cx, cy in centres], radius)
        if heal:
            # The patch's details, on the brightness and colour of what surrounds the spot.
            outside = (1 - mask)[..., None]
            weights = local.gaussian(outside, blur) + 1e-4
            surround = local.gaussian(target * outside, blur) / weights
            patch_surround = local.gaussian(source * outside, blur) / weights
            fill_mask = mask > 0.001
            replacement = source + surround - patch_surround
            replacement[~fill_mask] = target[~fill_mask]
        else:
            replacement = source
        blended = target + (replacement - target) * mask[..., None]
        if integer:
            info = np.iinfo(out.dtype)
            blended = np.clip(blended + 0.5, info.min, info.max)
        out[dy0:dy1, dx0:dx1] = blended.astype(out.dtype)
    return out


def find_source(pixels, x, y, radius):
    """A good offset (dx, dy, in fractions) to cover the spot at (x, y) with radius `radius` (fractions, as stored).

    Candidates around the spot are compared by the ring just outside each: the one
    that looks most like the spot's own surroundings, nearest first on a tie.
    """
    height, width = pixels.shape[:2]
    long = max(height, width)
    r = max(2.0, radius * long)
    cx, cy = x * width, y * height
    grey = pixels.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

    def ring(px, py):
        x0, x1 = int(px - r * 1.6), int(px + r * 1.6) + 1
        y0, y1 = int(py - r * 1.6), int(py + r * 1.6) + 1
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
            return None
        patch = grey[y0:y1, x0:x1]
        rows = np.arange(y0, y1, dtype=np.float32)[:, None] - py
        cols = np.arange(x0, x1, dtype=np.float32)[None, :] - px
        distance = np.hypot(rows, cols)
        return patch, (distance > r * 1.05) & (distance < r * 1.6), distance < r

    found = ring(cx, cy)
    best, best_score = None, math.inf
    for scale in (2.2, 3.0, 4.2):
        for step in range(16):
            angle = step / 16 * 2 * math.pi
            px, py = cx + math.cos(angle) * r * scale, cy + math.sin(angle) * r * scale
            candidate = ring(px, py)
            if candidate is None:
                continue
            if found is None:
                score = scale
            else:
                patch, band, _inside = found
                other, other_band, other_inside = candidate
                size = min(patch.shape[0], other.shape[0]), min(patch.shape[1], other.shape[1])
                a, b = patch[:size[0], :size[1]], other[:size[0], :size[1]]
                use = band[:size[0], :size[1]]
                score = float(np.mean((a[use] - b[use]) ** 2)) if use.any() else math.inf
                # A candidate with a spot of its own is worse: penalise detail inside it.
                inner = other[:size[0], :size[1]][other_inside[:size[0], :size[1]]]
                if inner.size:
                    score += float(np.var(inner)) * 0.5
                score *= 1 + 0.05 * scale
            if score < best_score:
                best, best_score = (px - cx, py - cy), score
    if best is None:
        best = (r * 2.5 if cx + r * 2.5 < width else -r * 2.5, 0.0)
    return best[0] / width, best[1] / height
