"""How photos are exported: format, size, quality, destination, file names, watermark and
the photographer's credits, and named profiles of these.

The settings in use and the saved profiles are kept in QSettings as JSON;
anything in them that isn't valid falls back to its default, so a hand-edited
or newer value never stops the app from exporting.
"""
import json
import os
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta

from . import stego
from .constants import EXPORT_DIR, KEEP_DIR, REJECT_DIR

QUALITY_RANGE = (1, 100)
LONG_EDGE_MAX = 20000
TOKENS = ("name", "date", "seq", "folder")  # what a name pattern may hold, as {name}
SCOPES = (("all", "Everything except rejects"), ("keeps", "Keeps only"))


@dataclass(frozen=True)
class Format:
    id: str
    title: str
    extension: str
    writer: str  # Qt's image format name
    bits: int = 8  # per channel
    quality: bool = True  # whether the quality setting applies


# HEIF, AVIF and WebP are offered where Qt has a plugin that writes them (see available_formats()).
FORMATS = (
    Format("jpeg", "JPEG", ".jpg", "jpg"),
    Format("tiff16", "TIFF, 16 bits (printing, retouching)", ".tif", "tiff", bits=16, quality=False),
    Format("png", "PNG", ".png", "png", quality=False),
    Format("heif", "HEIF", ".heic", "heic"),
    Format("avif", "AVIF", ".avif", "avif"),
    Format("webp", "WebP", ".webp", "webp"),
)
FORMATS_BY_ID = {fmt.id: fmt for fmt in FORMATS}
ALWAYS_WRITABLE = ("jpeg", "tiff16", "png")  # Qt's own plugins

WATERMARK_POSITIONS = (
    ("bottom_right", "Bottom right"),
    ("bottom_left", "Bottom left"),
    ("top_right", "Top right"),
    ("top_left", "Top left"),
    ("center", "Centre"),
)
WATERMARK_SIZE_RANGE = (1, 100)  # width, in % of the long edge
WATERMARK_MARGIN_RANGE = (0, 25)  # in % of the long edge
OPACITY_RANGE = (5, 100)


def available_formats():
    """The FORMATS this Qt can write."""
    from PyQt6.QtGui import QImageWriter  # here: exportsettings is read without a GUI

    writable = {bytes(name).decode().lower() for name in QImageWriter.supportedImageFormats()}
    return [fmt for fmt in FORMATS if fmt.id in ALWAYS_WRITABLE or fmt.writer in writable]

_EPOCH = datetime(1970, 1, 1)
_UNSAFE = re.compile(r'[/\\:\x00-\x1f]')


@dataclass(frozen=True)
class ExportSettings:
    long_edge: int = 0  # longest edge in pixels; 0 keeps the full size
    quality: int = 92  # JPEG quality, 1–100
    folder: str = EXPORT_DIR  # relative to the shoot folder, or absolute (`~` allowed)
    pattern: str = "{name}"  # file name without extension, see TOKENS
    format: str = "jpeg"  # an id of FORMATS
    # Watermark, applied after resizing: a PNG logo if set, else the text; nothing if neither.
    watermark_text: str = ""
    watermark_image: str = ""  # path (`~` allowed)
    watermark_position: str = "bottom_right"  # see WATERMARK_POSITIONS
    watermark_size: int = 20  # width, % of the long edge
    watermark_margin: int = 3  # % of the long edge
    watermark_opacity: int = 70  # %
    hidden_mark: str = ""  # written invisibly into the pixels, see stego.py
    # Credits written into rendered and copied JPEGs (XMP and IPTC), see metadata.py.
    creator: str = ""
    copyright: str = ""
    contact: str = ""  # e-mail or website
    keywords: str = ""  # comma-separated, added to each photo's own

    @classmethod
    def from_dict(cls, data):
        """Settings from a dict; what is missing or invalid takes its default."""
        if not isinstance(data, dict):
            return cls()
        values = {}
        for name, valid in _VALID.items():
            value = data.get(name)
            if isinstance(value, str):
                value = value.strip()
            if valid(value):
                values[name] = value
        return cls(**values)

    def to_dict(self):
        return asdict(self)

    def with_changes(self, **changes):
        return replace(self, **changes)

    @property
    def resizes(self):
        return self.long_edge > 0

    @property
    def file_format(self):
        return FORMATS_BY_ID[self.format]

    @property
    def extension(self):
        return self.file_format.extension

    @property
    def watermarks(self):
        return bool(self.watermark_text or self.watermark_image)

    @property
    def copies_jpegs(self):
        """Whether an unedited JPEG may be copied rather than rendered (its pixels stay as they are)."""
        return self.format == "jpeg" and not self.resizes and not self.watermarks and not self.hidden_mark

    @property
    def credits(self):
        """{creator, copyright, contact}, those that are set."""
        return {key: value for key in ("creator", "copyright", "contact") if (value := getattr(self, key))}

    def keyword_list(self):
        return split_keywords(self.keywords)

    def dest_dir(self, shoot_folder):
        """Where exports go, as an absolute path."""
        return os.path.normpath(os.path.join(shoot_folder, os.path.expanduser(self.folder)))

    def problem(self, shoot_folder):
        """Why exporting there can't be allowed, None if it can: exports must not land among the photos."""
        dest = os.path.normcase(self.dest_dir(shoot_folder))
        photo_dirs = (shoot_folder, os.path.join(shoot_folder, KEEP_DIR), os.path.join(shoot_folder, REJECT_DIR))
        if any(dest == os.path.normcase(os.path.normpath(folder)) for folder in photo_dirs):
            return f"Exports can't go to {self.folder}: they would land among the photos, and could replace one."
        return None

    def describe(self):
        """One line, for the Process Files dialog."""
        size = f"{self.long_edge} px long edge" if self.resizes else "full size"
        fmt = self.file_format
        quality = f", quality {self.quality}" if fmt.quality else ""
        parts = [f"{fmt.title.split(',')[0]}, {size}{quality}, named {self.pattern}{fmt.extension}"]
        if self.watermarks:
            parts.append("watermarked")
        if self.hidden_mark:
            parts.append("with a hidden mark")
        if self.credits or self.keywords:
            parts.append("with your credits")
        return ", ".join(parts) + "."


def split_keywords(text):
    """["a", "b"] from "a, b;b" — once each, in order."""
    if not isinstance(text, str):
        return []
    return list(dict.fromkeys(word.strip() for word in re.split(r"[,;\n]", text) if word.strip()))


def _in(low, high):
    return lambda value: _int(value) and low <= value <= high


def _text(value):
    return isinstance(value, str)


_VALID = {
    "long_edge": _in(0, LONG_EDGE_MAX),
    "quality": _in(*QUALITY_RANGE),
    "folder": lambda value: isinstance(value, str) and bool(value),
    "pattern": lambda value: isinstance(value, str) and pattern_problem(value) is None,
    "format": lambda value: value in FORMATS_BY_ID,
    "watermark_text": _text,
    "watermark_image": _text,
    "watermark_position": lambda value: value in dict(WATERMARK_POSITIONS),
    "watermark_size": _in(*WATERMARK_SIZE_RANGE),
    "watermark_margin": _in(*WATERMARK_MARGIN_RANGE),
    "watermark_opacity": _in(*OPACITY_RANGE),
    "hidden_mark": lambda value: isinstance(value, str) and stego.problem(value) is None,
    "creator": _text,
    "copyright": _text,
    "contact": _text,
    "keywords": _text,
}


def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def pattern_problem(pattern):
    """Why `pattern` can't name files, None if it can."""
    if not isinstance(pattern, str) or not pattern.strip():
        return "The name pattern is empty."
    for match in re.finditer(r'\{([^{}]*)\}', pattern):
        if match.group(1) not in TOKENS:
            return f"{{{match.group(1)}}} is not a field: use " + ", ".join(f"{{{token}}}" for token in TOKENS) + "."
    if re.sub(r'\{[^{}]*\}', "", pattern).count("{") or re.sub(r'\{[^{}]*\}', "", pattern).count("}"):
        return "The name pattern has an unmatched brace."
    return None


def date_text(seconds):
    """`YYYY-MM-DD` of a capture time (seconds on the camera's clock, see exif.capture_time())."""
    if seconds is None:
        return "undated"
    try:
        return (_EPOCH + timedelta(seconds=seconds)).strftime("%Y-%m-%d")
    except (OverflowError, ValueError):
        return "undated"


def file_stem(pattern, name, date="", seq=1, total=1, folder=""):
    """The pattern filled in, made safe as a file name; `name` alone if nothing would be left."""
    values = {
        "name": name,
        "date": date,
        "seq": str(seq).zfill(max(3, len(str(total)))),
        "folder": folder,
    }
    stem = re.sub(r'\{(' + "|".join(TOKENS) + r')\}', lambda match: values[match.group(1)], pattern)
    stem = _UNSAFE.sub("-", stem).strip().strip(".")
    return stem or name


def uses_date(pattern):
    return "{date}" in pattern


def load_profiles(text):
    """{name: ExportSettings} from JSON; {} if it can't be read."""
    try:
        data = json.loads(text) if text else {}
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {name: ExportSettings.from_dict(value) for name, value in data.items() if isinstance(name, str) and name.strip()}


def dump_profiles(profiles):
    return json.dumps({name: settings.to_dict() for name, settings in profiles.items()}, sort_keys=True)


def load_settings(text):
    try:
        return ExportSettings.from_dict(json.loads(text) if text else None)
    except (TypeError, ValueError):
        return ExportSettings()
