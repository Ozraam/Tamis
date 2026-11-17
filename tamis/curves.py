"""Tone curves: the parametric curve and the point curves, turned into lookup tables.

A point curve is a list of [x, y] in 0–1, x increasing, from which a smooth
curve is drawn that never wiggles between points (monotone cubic
interpolation). The edit stores them under "curve", per channel:
{"rgb": [[0, 0], [0.5, 0.6], [1, 1]], "red": …, "green": …, "blue": …}.

The parametric curve moves four ranges of tones (shadows, darks, lights,
highlights), split at three movable points; it is drawn as a point curve too.
"""
import math

import numpy as np

CHANNELS = ("rgb", "red", "green", "blue")
MAX_POINTS = 16
PARAMETRIC = ("curve_shadows", "curve_darks", "curve_lights", "curve_highlights")
SPLITS = ("curve_split1", "curve_split2", "curve_split3")
SPLIT_DEFAULTS = (25, 50, 75)
_REACH = 0.18  # how far a parametric slider at ±100 moves its range


def clean_points(points):
    """`points` as stored: 2 to MAX_POINTS [x, y] pairs in 0–1, x strictly increasing; None if invalid or straight."""
    if not isinstance(points, (list, tuple)) or not 2 <= len(points) <= MAX_POINTS:
        return None
    result = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in point):
            return None
        x, y = (round(min(1.0, max(0.0, float(v))), 4) for v in point)
        if result and x <= result[-1][0]:
            return None
        result.append([x, y])
    if all(abs(x - y) < 1e-4 for x, y in result) and result[0][0] == 0 and result[-1][0] == 1:
        return None  # the straight line: no curve
    if len(result) == 2 and result == [[0.0, 0.0], [1.0, 1.0]]:
        return None
    return result


def clean(curve):
    """The "curve" setting as stored: {channel: points}, None when every channel is straight."""
    if not isinstance(curve, dict):
        return None
    result = {}
    for channel in CHANNELS:
        points = clean_points(curve.get(channel))
        if points:
            result[channel] = points
    return result or None


def interpolate(points, x):
    """The monotone cubic (Fritsch–Carlson) through `points`, at `x` (an array); flat beyond the ends."""
    xs = np.array([p[0] for p in points], dtype=np.float64)
    ys = np.array([p[1] for p in points], dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    if len(xs) == 1:
        return np.full_like(x, ys[0])
    h = np.diff(xs)
    delta = np.diff(ys) / h
    slopes = np.empty_like(xs)
    slopes[0], slopes[-1] = delta[0], delta[-1]
    for k in range(1, len(xs) - 1):
        if delta[k - 1] * delta[k] <= 0:
            slopes[k] = 0.0
        else:  # weighted harmonic mean: no overshoot
            w1, w2 = 2 * h[k] + h[k - 1], h[k] + 2 * h[k - 1]
            slopes[k] = (w1 + w2) / (w1 / delta[k - 1] + w2 / delta[k])
    index = np.clip(np.searchsorted(xs, x, side="right") - 1, 0, len(xs) - 2)
    t = (np.clip(x, xs[0], xs[-1]) - xs[index]) / h[index]
    t2, t3 = t * t, t * t * t
    y = (
        (2 * t3 - 3 * t2 + 1) * ys[index]
        + (t3 - 2 * t2 + t) * h[index] * slopes[index]
        + (-2 * t3 + 3 * t2) * ys[index + 1]
        + (t3 - t2) * h[index] * slopes[index + 1]
    )
    return y


def parametric_points(amounts, splits):
    """The points of the parametric curve: `amounts` (shadows, darks, lights, highlights, −100…100), `splits` (0–100)."""
    s1, s2, s3 = (v / 100 for v in splits)
    centres = (s1 / 2, (s1 + s2) / 2, (s2 + s3) / 2, (s3 + 1) / 2)
    points = [[0.0, 0.0]]
    for centre, amount in zip(centres, amounts, strict=False):
        room = min(centre, 1 - centre)
        points.append([centre, min(1.0, max(0.0, centre + amount / 100 * _REACH * min(1.0, room * 2.5)))])
    points.append([1.0, 1.0])
    # The curve stays a curve: each point at least as high as the one before.
    for i in range(1, len(points)):
        points[i][1] = max(points[i][1], points[i - 1][1])
    return points


def is_parametric(settings):
    return any(settings.get(key) for key in PARAMETRIC)


def channel_tables(settings, x):
    """The curves of `settings` applied to `x` (encoded values): (rgb, red, green, blue), each None if straight.

    The parametric curve comes first, then the RGB point curve; the channel curves follow these.
    """
    y = None
    if is_parametric(settings):
        amounts = [settings.get(key, 0) for key in PARAMETRIC]
        splits = [settings.get(key, default) for key, default in zip(SPLITS, SPLIT_DEFAULTS, strict=False)]
        y = interpolate(parametric_points(amounts, splits), x)
    curve = settings.get("curve") or {}
    if curve.get("rgb"):
        y = interpolate(curve["rgb"], x if y is None else y)
    channels = [interpolate(curve[name], x if y is None else y) if curve.get(name) else None for name in CHANNELS[1:]]
    return y, *channels
