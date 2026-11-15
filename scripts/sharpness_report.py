"""Score a culled shoot's sharpness the way Tamis does, to tune its thresholds against your own marks.

    python scripts/sharpness_report.py <folder> [--out folder] [--ratios 0.2 0.3 0.4 0.5 0.6]

Writes, into `--out` (default: `sharpness_report/<shoot name>` in the current folder, never into the shoot):

- `scores.csv`: file, score, score / folder median, the median, burst (id, position / size, sharpest),
  Tamis's suggestion (`soft`, `sharpest` or nothing) and your mark;
- `contact_sheet.jpg`: the photos sorted by score, each with its score and mark;
- `summary.txt`: what is printed (see below), and `source.txt`, the shoot's folder.

Reports of different shoots sit side by side; running it again on the same shoot replaces its report,
and another shoot with the same name gets `<name> (2)`.

and prints how well `soft` would have matched your rejects for each ratio of `--ratios` (the app uses
models.SOFT_RATIO), and how often the sharpest frame of a burst is one you kept. Marks are read from
the session and the `_KEEPS` / `_REJECTS` folders, as the app reads them. Nothing in the shoot is changed.
"""
import argparse
import csv
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QRect, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter  # noqa: E402

from tamis import bursts  # noqa: E402
from tamis.analysis import _score  # noqa: E402
from tamis.constants import KEEP, REJECT  # noqa: E402
from tamis.exif import capture_time  # noqa: E402
from tamis.imaging import load_image  # noqa: E402
from tamis.models import SOFT_RATIO, scan_folder  # noqa: E402

TILE = 240  # contact sheet thumbnails, longest edge
COLUMNS = 8
MARKS = {KEEP: "keep", REJECT: "reject", None: ""}


def score_photos(folder):
    photos = scan_folder(folder)
    for done, photo in enumerate(photos, 1):
        photo.capture_time = capture_time(photo.files)
        scores = _score(photo.path)
        photo.sharpness, photo.image_hash = scores.get("sharpness"), scores.get("image_hash")
        print(f"\rScored {done} of {len(photos)}", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    bursts.assign(photos)
    return photos


def suggestion(photo, median, ratio=SOFT_RATIO):
    if photo.sharpness is not None and median and photo.sharpness < median * ratio:
        return "soft"
    if photo.burst and photo.burst.sharpest:
        return "sharpest"
    return ""


def write_csv(path, photos, median):
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["file", "score", "ratio_to_median", "folder_median", "burst", "burst_position", "sharpest",
                         "suggestion", "mark"])
        for photo in photos:
            score = photo.sharpness
            burst = photo.burst
            writer.writerow([
                os.path.basename(photo.path),
                "" if score is None else f"{score:.2f}",
                "" if score is None or not median else f"{score / median:.3f}",
                f"{median:.2f}" if median else "",
                "" if burst is None else burst.id,
                "" if burst is None else f"{burst.position + 1}/{burst.size}",
                "" if burst is None else int(burst.sharpest),
                suggestion(photo, median),
                MARKS.get(photo.status, ""),
            ])


def contact_sheet(path, photos, median):
    ranked = sorted((photo for photo in photos if photo.sharpness is not None), key=lambda photo: photo.sharpness)
    if not ranked:
        return
    rows = -(-len(ranked) // COLUMNS)
    caption = 34
    sheet = QImage(COLUMNS * TILE, rows * (TILE + caption), QImage.Format.Format_RGB32)
    sheet.fill(QColor(30, 30, 30))
    painter = QPainter(sheet)
    font = QFont()
    font.setPixelSize(12)
    painter.setFont(font)
    colours = {KEEP: QColor(90, 200, 120), REJECT: QColor(230, 90, 80)}
    for index, photo in enumerate(ranked):
        x, y = index % COLUMNS * TILE, index // COLUMNS * (TILE + caption)
        try:
            image, _native = load_image(photo.path, TILE - 8)
        except Exception as exc:  # one unreadable photo must not stop the report
            print(f"{os.path.basename(photo.path)}: {exc}", file=sys.stderr)
            continue
        painter.drawImage(x + (TILE - image.width()) // 2, y + (TILE - image.height()) // 2, image)
        painter.setPen(colours.get(photo.status, QColor(220, 220, 220)))
        text = f"{os.path.basename(photo.path)}  {MARKS.get(photo.status, '')}\n" \
               f"{photo.sharpness:.0f} ({photo.sharpness / median:.2f}× median) {suggestion(photo, median)}"
        painter.drawText(QRect(x + 4, y + TILE, TILE - 8, caption), Qt.AlignmentFlag.AlignLeft, text)
    painter.end()
    sheet.save(path, "JPG", 85)


def summary(photos, median, ratios):
    """What the scores say against the marks, as text."""
    lines = []
    marked = [photo for photo in photos if photo.status in (KEEP, REJECT) and photo.sharpness is not None]
    rejects = sum(photo.status == REJECT for photo in marked)
    lines.append(f"{len(photos)} photos, {len(marked)} marked ({rejects} rejects), median score {median:.1f}")
    if marked:
        lines.append("ratio  flagged  rejects among them  rejects found")
        for ratio in ratios:
            flagged = [photo for photo in marked if photo.sharpness < median * ratio]
            hits = sum(photo.status == REJECT for photo in flagged)
            precision = f"{hits / len(flagged):.0%}" if flagged else "-"
            recall = f"{hits / rejects:.0%}" if rejects else "-"
            current = "  (current)" if ratio == SOFT_RATIO else ""
            lines.append(f"{ratio:5.2f}  {len(flagged):7}  {precision:>18}  {recall:>13}{current}")
    groups = {}
    for photo in photos:
        if photo.burst:
            groups.setdefault(photo.burst.id, []).append(photo)
    judged = [run for run in groups.values() if any(p.burst.sharpest for p in run) and any(p.status == KEEP for p in run)]
    if judged:
        agreed = sum(any(p.burst.sharpest and p.status == KEEP for p in run) for run in judged)
        share = f"{agreed / len(judged):.0%}"
        lines.append(f"Bursts with a keep: {len(judged)}; the sharpest frame was one of the keeps in {agreed} ({share})")
    return "\n".join(lines)


def default_out(folder, root="sharpness_report"):
    """sharpness_report/<shoot name>: the same shoot's earlier report, or `<name> (n)` beside another shoot's."""
    name = os.path.basename(folder.rstrip(os.sep)) or "shoot"
    candidate, n = name, 2
    while True:
        out = os.path.abspath(os.path.join(root, candidate))
        try:
            with open(os.path.join(out, "source.txt"), encoding="utf-8") as file:
                source = file.read().strip()
        except OSError:
            source = None
        if source == folder or (source is None and not (os.path.isdir(out) and os.listdir(out))):
            return out  # this shoot's earlier report, or a free place
        candidate, n = f"{name} ({n})", n + 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder")
    parser.add_argument("--out", help="where to write the report (default: sharpness_report/<shoot name>)")
    parser.add_argument("--ratios", type=float, nargs="+", default=[0.2, 0.3, 0.4, 0.5, 0.6])
    args = parser.parse_args()
    folder = os.path.abspath(args.folder)
    out = os.path.abspath(args.out) if args.out else default_out(folder)
    if out == folder or out.startswith(folder + os.sep):
        parser.error("--out must be outside the shoot: nothing in it is changed")
    app = QGuiApplication(sys.argv)  # noqa: F841 - QPainter needs it for text
    photos = score_photos(folder)
    scores = sorted(photo.sharpness for photo in photos if photo.sharpness is not None)
    if not scores:
        sys.exit("No photo could be scored")
    median = scores[len(scores) // 2]  # as PhotoModel.analysis_changed() takes it
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "source.txt"), "w", encoding="utf-8") as file:
        file.write(folder + "\n")
    write_csv(os.path.join(out, "scores.csv"), photos, median)
    contact_sheet(os.path.join(out, "contact_sheet.jpg"), photos, median)
    text = summary(photos, median, args.ratios)
    print(text)
    with open(os.path.join(out, "summary.txt"), "w", encoding="utf-8") as file:
        file.write(text + "\n")
    print(f"Written to {out}")


if __name__ == "__main__":
    main()
