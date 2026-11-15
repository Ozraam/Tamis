"""Time from launch to the first preview shown, for the built app and from source.

    python scripts/startup_time.py <folder of photos> [--runs 5]

Each run opens the folder with settings and caches of its own (TAMIS_STARTUP_TIME, see tamis/main.py),
so the user's are left alone and the thumbnail cache is cold. The first run also warms the disk cache.
"""
import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "dist", "Tamis.app", "Contents", "MacOS", "Tamis")


def launch_to_preview(command, folder, timeout=60):
    """Milliseconds from launch to the first preview, None if the app doesn't say (a build without the timing mode)."""
    start = time.time()
    env = {**os.environ, "TAMIS_STARTUP_TIME": os.path.abspath(folder)}
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=ROOT)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return None
    for line in output.splitlines():
        if line.startswith("first preview at "):
            return round((float(line.split()[-1]) - start) * 1000)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder")
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    commands = {"from source": [sys.executable, "-m", "tamis"]}
    if os.access(APP, os.X_OK):
        commands["dist/Tamis.app"] = [APP]
    for label, command in commands.items():
        times = [launch_to_preview(command, args.folder) for _ in range(args.runs)]
        if None in times:
            print(f"{label}: no time reported (a build from before the timing mode? rebuild it)")
            continue
        print(f"{label}: {' '.join(map(str, times))} ms, median {sorted(times)[len(times) // 2]} ms")


if __name__ == "__main__":
    main()
