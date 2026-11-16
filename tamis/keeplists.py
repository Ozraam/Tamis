"""Lists of photo names for other editors: what was kept, as they can search or import it.

Lightroom's pick and reject flags are not in XMP, so other editors only see
Tamis's marks through a list like these (or the opt-in colour labels).
"""
import csv
import io
import os

from .constants import KEEP, REJECT

# (id, title, file extension when saved)
FORMATS = (
    ("lightroom", "Lightroom Classic: names to paste in the Library filter (Filename contains any)", ".txt"),
    ("lines", "One file name per line (Capture One, Photo Mechanic…)", ".txt"),
    ("csv", "CSV: name, mark, stars, label, folder", ".csv"),
)
EXTENSIONS = {format_id: ext for format_id, _title, ext in FORMATS}
MARK_NAMES = {KEEP: "keep", REJECT: "reject", None: ""}


def _stems(photos):
    """The photos' names without extension, once each: a RAW and its JPEG share one."""
    return list(dict.fromkeys(os.path.splitext(photo.name)[0] for photo in photos))


def _file_names(photos):
    return list(dict.fromkeys(os.path.basename(path) for photo in photos for path in photo.files))


def text(photos, format_id, folder=""):
    """The list of `photos` in a format of FORMATS.

    Lightroom gets the names without extension, separated by spaces, which its
    "Filename contains any" search treats as alternatives, so a RAW and its JPEG
    both match; a name with a space is quoted (it would otherwise match
    more than it should). The others list every file, companions included.
    """
    if format_id == "lightroom":
        return " ".join(f'"{stem}"' if " " in stem else stem for stem in _stems(photos))
    if format_id == "lines":
        return "".join(name + "\n" for name in _file_names(photos))
    if format_id == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["name", "mark", "stars", "label", "folder"])
        for photo in photos:
            location = os.path.relpath(os.path.dirname(photo.path), folder) if folder else os.path.dirname(photo.path)
            for path in photo.files:
                writer.writerow([os.path.basename(path), MARK_NAMES.get(photo.status, ""), photo.rating, photo.label or "",
                                 "" if location == "." else location])
        return buffer.getvalue()
    raise ValueError(f"unknown list format: {format_id}")


def default_name(folder, format_id, which="keeps"):
    """`<shoot> keeps.txt`, next to the session."""
    return f"{os.path.basename(os.path.normpath(folder)) or 'Tamis'} {which}{EXTENSIONS[format_id]}"
