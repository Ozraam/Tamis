"""Automatic settings, as starting points the user adjusts: white balance from a neutral point or the
whole photo, and tone from the histogram. Each returns slider values, so applying one is an
ordinary (undoable) edit.

They read the photo as developed (linear RGB, before any edit).
"""
import math

import numpy as np

from . import develop

LUMA = develop.LUMA


def white_balance_for(rgb):
    """{"temperature", "tint"} that turn the linear colour `rgb` grey (develop._white_balance()'s inverse)."""
    r, g, b = (max(float(v), 1e-6) for v in rgb)
    warm = 0.5 * math.log2(b / r)  # red × 2^warm = blue × 2^-warm
    magenta = math.log2(g / (r * 2 ** warm))  # green × 2^-magenta = red × 2^warm
    return {
        "temperature": int(round(min(100, max(-100, warm / 0.6 * 100)))),
        "tint": int(round(min(100, max(-100, magenta / 0.4 * 100)))),
    }


def auto_white_balance(linear):
    """{"temperature", "tint"} that take away the photo's colour cast: the average of its midtones made grey."""
    pixels = linear.reshape(-1, 3)[:: max(1, linear.shape[0] * linear.shape[1] // 200_000)]
    luma = pixels @ LUMA
    low, high = np.percentile(luma, (20, 95)) if len(luma) else (0, 1)
    middle = pixels[(luma >= low) & (luma <= high) & (pixels.max(axis=1) < 0.95)]
    if len(middle) < 16:
        middle = pixels
    return white_balance_for(np.exp(np.log(np.maximum(middle, 1e-5)).mean(axis=0)))


def auto_tone(linear, edit=None, raw=False):
    """{"exposure", "contrast", "highlights", "shadows", "whites", "blacks"} that make good use of the tones.

    Worked out with the photo's white balance; the other settings are replaced.
    """
    edit = develop.clean(edit or {})
    base = {key: edit[key] for key in ("temperature", "tint", "process") if key in edit}
    sample = linear[:: max(1, linear.shape[0] // 400), :: max(1, linear.shape[1] // 400)]
    luma = np.maximum(sample.reshape(-1, 3) @ LUMA, 1e-6)
    # Exposure: the log-average brightness to a middle grey, within reason.
    key = float(np.exp(np.mean(np.log(luma))))
    exposure = float(np.clip(math.log2(0.16 / key), -3.0, 3.0))
    result = {"exposure": round(exposure, 2)}
    rendered = develop.render(sample, {**base, **result}, raw=raw).reshape(-1, 3) @ LUMA / 255
    high, low = np.percentile(rendered, (99.5, 1.0))
    clipped = float(np.mean(rendered > 0.985))
    dark = float(np.mean(rendered < 0.02))
    result["highlights"] = int(np.clip(-clipped * 600 - max(0.0, high - 0.93) * 300, -80, 0))
    result["shadows"] = int(np.clip(dark * 400 + max(0.0, 0.08 - low) * 300, 0, 60))
    rendered = develop.render(sample, {**base, **result}, raw=raw).reshape(-1, 3) @ LUMA / 255
    high, low = np.percentile(rendered, (99.8, 0.2))
    # Whites and blacks stretch what is left to the ends; contrast follows the spread of the midtones.
    result["whites"] = int(np.clip((0.97 - high) * 400, -40, 60))
    result["blacks"] = int(np.clip(-(low - 0.015) * 400, -40, 30))
    spread = float(np.std(rendered))
    result["contrast"] = int(np.clip((0.22 - spread) * 250, -20, 40))
    return develop.clean({**edit, **result})
