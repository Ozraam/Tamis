"""Time develop.render() on random images, at preview and 24 Mpx sizes: `python scripts/bench_render.py`.

Prints the best of a few runs per edit and size, single thread and in parallel strips (as the preview is
rendered), plus the peak memory one render allocates (tracemalloc follows numpy's allocations).
"""
import argparse
import time
import tracemalloc

import numpy as np

from tamis import develop
from tamis.editing import render_parallel

SIZES = {"preview 2400 px": (1600, 2400), "24 Mpx": (4000, 6000)}
EDITS = {
    "exposure": {"exposure": 0.7},
    "full": {
        "exposure": 0.7, "contrast": 30, "highlights": -40, "shadows": 30,
        "saturation": 20, "vignette": -30, "grain": 25,
    },
}


def best_of(runs, work):
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        work()
        times.append(time.perf_counter() - start)
    return min(times) * 1000


def peak_mb(work):
    tracemalloc.start()
    work()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return peak / 1e6


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    rng = np.random.default_rng(0)
    for size_name, shape in SIZES.items():
        linear = rng.random((*shape, 3), dtype=np.float32)
        for edit_name, edit in EDITS.items():
            single = best_of(args.runs, lambda linear=linear, edit=edit: develop.render(linear, edit))
            strips = best_of(args.runs, lambda linear=linear, edit=edit: render_parallel(linear, edit, None, 0))
            memory = peak_mb(lambda linear=linear, edit=edit: develop.render(linear, edit))
            print(f"{size_name:16} {edit_name:9} single {single:7.0f} ms   strips {strips:6.0f} ms   peak {memory:6.0f} MB")


if __name__ == "__main__":
    main()
