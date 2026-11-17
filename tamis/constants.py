import os

RAW_EXTENSIONS = frozenset({'.arw', '.cr2', '.cr3', '.nef', '.dng', '.orf', '.raf', '.rw2'})
STANDARD_EXTENSIONS = frozenset({'.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.heic'})
SUPPORTED_EXTENSIONS = RAW_EXTENSIONS | STANDARD_EXTENSIONS


# A virtual copy is a photo of its own on the files of another: its path is the file's, then COPY_MARK and
# its number. No file name can hold a NUL, so the two never mix up, and opening a copy's path fails loudly.
COPY_MARK = "\0"


def file_of(path):
    """The file a photo's path is of: `path` itself, unless it is a virtual copy's."""
    return path.split(COPY_MARK, 1)[0]


def copy_number(path):
    """0 for a photo, n for its virtual copy n."""
    _file, mark, number = path.partition(COPY_MARK)
    return int(number) if mark and number.isdigit() else 0


def copy_path(path, number):
    """The path of virtual copy `number` (0: the photo itself) of the photo whose file is `path`."""
    return f"{file_of(path)}{COPY_MARK}{number}" if number else file_of(path)


def is_raw(path):
    return os.path.splitext(file_of(path))[1].lower() in RAW_EXTENSIONS


KEEP = "keep"
REJECT = "reject"

KEEP_DIR = "_KEEPS"
REJECT_DIR = "_REJECTS"
EXPORT_DIR = "_EXPORTS"

APP_NAME = "Tamis"
SESSION_FILENAME = "tamis_session.json"
FALLBACK_SESSIONS_DIR = "sessions"
PLUGINS_DIR = "plugins"
PRESETS_FILENAME = "presets.json"

# The app used to be called CullSpeed: what it left behind is still read.
LEGACY_APP_NAME = "CullSpeed"
LEGACY_SESSION_FILENAME = "cullspeed_session.json"
LEGACY_GLOBAL_CONFIG_FILENAME = ".cullspeed_global.json"

# Longest edge, in pixels, of decoded images.
PREVIEW_MAX_SIZE = 3000
THUMBNAIL_MAX_SIZE = 240

# How many decoded previews to keep in memory, and how far to prefetch.
PREVIEW_CACHE_SIZE = 8
PREFETCH_AHEAD = 3
PREFETCH_BEHIND = 1

# Memory for cached thumbnails. They are decoded at THUMBNAIL_MAX_SIZE times
# the screen's pixel ratio (~600 KB each on Retina), so this holds a full
# screen of the smallest grid size on large displays.
THUMBNAIL_CACHE_BYTES = 384 * 2**20

# Thumbnails kept on disk, and the longest edge photos are analysed at.
THUMBNAIL_DISK_CACHE_BYTES = 512 * 2**20
ANALYSIS_MAX_SIZE = 1024

# Shots this close together (seconds) are a burst.
BURST_GAP_SECONDS = 2.0

UNDO_LIMIT = 500

# Colors
COLOR_BG = "#121212"
COLOR_PANEL = "#1a1a1a"
COLOR_CARD = "#1f1f1f"
COLOR_CARD_HOVER = "#2a2a2a"
COLOR_BORDER = "#2c2c2c"
COLOR_TEXT = "#e5e5e5"
COLOR_MUTED = "#9ca3af"
COLOR_ACCENT = "#3b82f6"
COLOR_KEEP = "#4ade80"
COLOR_REJECT = "#f87171"
COLOR_STAR = "#facc15"

# Lightroom's colour labels (xmp:Label values)
LABEL_COLORS = {
    "Red": "#ef4444",
    "Yellow": "#eab308",
    "Green": "#22c55e",
    "Blue": "#3b82f6",
    "Purple": "#a855f7",
}

CULL_TAB, GALLERY_TAB = 0, 1  # the main window's tabs
