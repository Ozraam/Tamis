"""Local adjustments: masks, where they are on the picture, and how much each pixel takes of them.

A mask is a dict stored in the edit's "masks" list:

    {"name": "Sky", "components": [component, …], "invert": False,
     "settings": {"exposure": -0.5, "clarity": 20, …}}

Its components add up (the first one, and those whose "mode" is "add"),
subtract or intersect:

- {"type": "linear", "x0", "y0", "x1", "y1"}: full effect up to the first point, none past the second;
- {"type": "radial", "x", "y", "rx", "ry", "angle", "feather", "invert"}: an ellipse, softened by `feather` (0–100);
- {"type": "brush", "strokes": [{"points": [[x, y], …], "size", "feather", "flow", "erase"}]};
- {"type": "subject"}, {"type": "sky"} or {"type": "person"}: found by faces.segment() (see automasks.py).

Positions are fractions of the photo's width and height before it is turned,
straightened or cropped (its "source"), so masks stay on the same part of the
picture whatever its framing. Sizes are fractions of its long edge, angles in
degrees. develop.render() asks local_maps() for the per-pixel amounts.
"""
import math

import numpy as np

# Settings a mask can carry, with their range; a mask's setting adds to the photo's.
SETTINGS = {
    "exposure": (-4.0, 4.0),
    "contrast": (-100, 100),
    "highlights": (-100, 100),
    "shadows": (-100, 100),
    "whites": (-100, 100),
    "blacks": (-100, 100),
    "temperature": (-100, 100),
    "tint": (-100, 100),
    "saturation": (-100, 100),
    "texture": (-100, 100),
    "clarity": (-100, 100),
    "dehaze": (-100, 100),
    "sharpen": (-100, 100),
}
NEIGHBOURHOOD = ("texture", "clarity", "dehaze", "sharpen")
AUTOMATIC = ("subject", "sky", "person")
TYPES = ("linear", "radial", "brush", *AUTOMATIC)
MODES = ("add", "subtract", "intersect")
MAX_MASKS = 32
MAX_COMPONENTS = 16
MAX_STROKES = 400
MAX_POINTS = 4000


def _number(value, low, high, digits=4):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return round(min(high, max(low, float(value))), digits)


def _clean_stroke(stroke):
    if not isinstance(stroke, dict) or not isinstance(stroke.get("points"), list):
        return None
    points = []
    for point in stroke["points"][:MAX_POINTS]:
        if isinstance(point, (list, tuple)) and len(point) == 2:
            x, y = _number(point[0], -0.5, 1.5), _number(point[1], -0.5, 1.5)
            if x is not None and y is not None:
                points.append([x, y])
    size = _number(stroke.get("size", 0.03), 0.001, 0.5)
    if not points or size is None:
        return None
    return {
        "points": points,
        "size": size,
        "feather": int(_number(stroke.get("feather", 50), 0, 100) or 0),
        "flow": int(_number(stroke.get("flow", 100), 1, 100) or 100),
        "erase": stroke.get("erase") is True,
    }


def clean_component(component):
    if not isinstance(component, dict) or component.get("type") not in TYPES:
        return None
    kind = component["type"]
    result = {"type": kind}
    if component.get("mode") in ("subtract", "intersect"):
        result["mode"] = component["mode"]
    if kind == "linear":
        for key in ("x0", "y0", "x1", "y1"):
            value = _number(component.get(key), -2.0, 3.0)
            if value is None:
                return None
            result[key] = value
        if (result["x0"], result["y0"]) == (result["x1"], result["y1"]):
            return None
    elif kind == "radial":
        for key, low, high in (("x", -2.0, 3.0), ("y", -2.0, 3.0), ("rx", 0.002, 3.0), ("ry", 0.002, 3.0)):
            value = _number(component.get(key), low, high)
            if value is None:
                return None
            result[key] = value
        result["angle"] = _number(component.get("angle", 0), -360, 360, 2) or 0.0
        result["feather"] = int(_number(component.get("feather", 50), 0, 100) or 0)
        if component.get("invert") is True:
            result["invert"] = True
    elif kind == "brush":
        strokes = [s for s in map(_clean_stroke, (component.get("strokes") or [])[:MAX_STROKES]) if s]
        result["strokes"] = strokes
    return result


def clean_settings(values):
    result = {}
    if not isinstance(values, dict):
        return result
    for key, (low, high) in SETTINGS.items():
        value = _number(values.get(key), low, high, 2)
        if value is None:
            continue
        value = round(value, 2) if key == "exposure" else int(round(value))
        if value:
            result[key] = value
    return result


def clean_mask(mask):
    if not isinstance(mask, dict):
        return None
    components = [c for c in map(clean_component, (mask.get("components") or [])[:MAX_COMPONENTS]) if c]
    if not components:
        return None
    components[0].pop("mode", None)  # the first one is what the others add to, subtract from or intersect
    result = {"components": components, "settings": clean_settings(mask.get("settings"))}
    name = mask.get("name")
    if isinstance(name, str) and name.strip():
        result["name"] = name.strip()[:80]
    if mask.get("invert") is True:
        result["invert"] = True
    if mask.get("hidden") is True:
        result["hidden"] = True  # kept, but not applied
    return result


def clean(masks):
    """The "masks" setting as stored; None when there is none."""
    if not isinstance(masks, list):
        return None
    result = [m for m in map(clean_mask, masks[:MAX_MASKS]) if m]
    return result or None


def active(masks):
    """The masks that change something."""
    return [m for m in masks or () if m.get("settings") and not m.get("hidden")]


def automatic_kinds(masks):
    """The kinds of automatic mask (subject, sky, person) that `masks` use."""
    return sorted({c["type"] for m in masks or () for c in m["components"] if c["type"] in AUTOMATIC})


# -- geometry ---------------------------------------------------------------------

class Geometry:
    """Where the pixels of a framed picture come from in its source, and back.

    Positions are worked out in "turned units": the turned, unstraightened picture
    with a height of 1 and a width of `aspect`.
    """

    def __init__(self, edit, full_size):
        full_height, full_width = full_size
        self.crop = edit.get("crop") or [0.0, 0.0, 1.0, 1.0]
        left, top, right, bottom = self.crop
        self.rotate = edit.get("rotate", 0) % 360
        self.straighten = edit.get("straighten", 0.0)
        self.height, self.width = full_height, full_width
        # The turned picture's width / height, from the framed one's and the crop's.
        self.aspect = (full_width / max(1e-6, right - left)) / (full_height / max(1e-6, bottom - top))
        angle = math.radians(self.straighten)
        self.cos, self.sin = math.cos(angle), math.sin(angle)
        self.zoom = _straighten_zoom(self.aspect, self.straighten)
        self.long = max(self.aspect, 1.0)

    # framed fractions <-> turned units

    def from_framed(self, fx, fy):
        """Turned units of framed fractions (scalars or arrays)."""
        left, top, right, bottom = self.crop
        x = (left + fx * (right - left)) * self.aspect - self.aspect / 2
        y = (top + fy * (bottom - top)) - 0.5
        if not self.straighten:
            return x + self.aspect / 2, y + 0.5
        return (self.cos * x + self.sin * y) / self.zoom + self.aspect / 2, (self.cos * y - self.sin * x) / self.zoom + 0.5

    def to_framed(self, x, y):
        """Framed fractions of turned units."""
        dx, dy = x - self.aspect / 2, y - 0.5
        if self.straighten:
            dx, dy = (self.cos * dx - self.sin * dy) * self.zoom, (self.sin * dx + self.cos * dy) * self.zoom
        left, top, right, bottom = self.crop
        fx = ((dx + self.aspect / 2) / self.aspect - left) / max(1e-6, right - left)
        fy = ((dy + 0.5) - top) / max(1e-6, bottom - top)
        return fx, fy

    # turned units <-> source fractions

    def to_source(self, x, y):
        u, v = x / self.aspect, y
        if self.rotate == 90:
            return v, 1 - u
        if self.rotate == 180:
            return 1 - u, 1 - v
        if self.rotate == 270:
            return 1 - v, u
        return u, v

    def from_source(self, us, vs):
        if self.rotate == 90:
            u, v = 1 - vs, us
        elif self.rotate == 180:
            u, v = 1 - us, 1 - vs
        elif self.rotate == 270:
            u, v = vs, 1 - us
        else:
            u, v = us, vs
        return u * self.aspect, v

    def angle_from_source(self, degrees):
        return degrees + self.rotate

    # pixels

    def pixel_coords(self, first_row, rows):
        """Turned units of the centres of framed pixels in rows [first_row, first_row + rows), as two rows × width arrays."""
        fy = (np.arange(first_row, first_row + rows, dtype=np.float32) + 0.5) / self.height
        fx = (np.arange(self.width, dtype=np.float32) + 0.5) / self.width
        x, y = self.from_framed(fx[None, :], fy[:, None])
        return np.broadcast_to(x, (rows, self.width)).astype(np.float32), np.broadcast_to(y, (rows, self.width)).astype(np.float32)

    def pixels_per_unit(self):
        """Framed pixels per turned unit."""
        left, top, right, bottom = self.crop
        return self.height / max(1e-6, bottom - top) * self.zoom

    def framed_to_source(self, fx, fy):
        return self.to_source(*self.from_framed(fx, fy))

    def source_to_framed(self, us, vs):
        return self.to_framed(*self.from_source(us, vs))


def _straighten_zoom(aspect, degrees):
    angle = math.radians(abs(degrees))
    return math.cos(angle) + math.sin(angle) * max(aspect, 1 / aspect)


# -- weights -----------------------------------------------------------------------

def _smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _linear(component, geometry, x, y):
    x0, y0 = geometry.from_source(component["x0"], component["y0"])
    x1, y1 = geometry.from_source(component["x1"], component["y1"])
    dx, dy = x1 - x0, y1 - y0
    length = dx * dx + dy * dy
    t = ((x - x0) * dx + (y - y0) * dy) / max(length, 1e-12)
    return 1 - _smoothstep(t)


def _radial(component, geometry, x, y):
    cx, cy = geometry.from_source(component["x"], component["y"])
    angle = math.radians(geometry.angle_from_source(component.get("angle", 0)))
    rx, ry = component["rx"] * geometry.long, component["ry"] * geometry.long
    dx, dy = x - cx, y - cy
    # In the ellipse's own axes, which turn with the picture.
    along = (dx * math.cos(angle) + dy * math.sin(angle)) / rx
    across = (-dx * math.sin(angle) + dy * math.cos(angle)) / ry
    distance = np.sqrt(along * along + across * across)
    feather = component.get("feather", 50) / 100
    inner = 1 - max(feather, 0.01)
    weight = 1 - _smoothstep((distance - inner) / (1 - inner))
    return 1 - weight if component.get("invert") else weight


def _brush(component, geometry, x, y, first_row):
    """Strokes as round dabs along their points, each touching only its own patch of the block."""
    weight = np.zeros(x.shape, dtype=np.float32)
    rows, width = x.shape
    per_unit = geometry.pixels_per_unit()
    for stroke in component.get("strokes", ()):
        radius = stroke["size"] * geometry.long
        feather = stroke["feather"] / 100
        inner = 1 - max(feather, 0.01)
        flow = stroke["flow"] / 100
        points = [geometry.from_source(px, py) for px, py in stroke["points"]]
        dabs = [points[0]]
        spacing = radius * 0.25
        for (ax, ay), (bx, by) in zip(points, points[1:], strict=False):
            length = math.hypot(bx - ax, by - ay)
            steps = int(length / spacing)
            dabs.extend((ax + (bx - ax) * i / (steps + 1), ay + (by - ay) * i / (steps + 1)) for i in range(1, steps + 1))
            dabs.append((bx, by))
        reach = radius * per_unit + 2
        for dab_x, dab_y in dabs:
            fx, fy = geometry.to_framed(dab_x, dab_y)
            col, row = fx * geometry.width, fy * geometry.height - first_row
            r0, r1 = max(0, int(row - reach)), min(rows, int(row + reach) + 1)
            c0, c1 = max(0, int(col - reach)), min(width, int(col + reach) + 1)
            if r0 >= r1 or c0 >= c1:
                continue
            distance = np.hypot(x[r0:r1, c0:c1] - dab_x, y[r0:r1, c0:c1] - dab_y) / radius
            dab = (1 - _smoothstep((distance - inner) / (1 - inner))) * flow
            patch = weight[r0:r1, c0:c1]
            if stroke["erase"]:
                patch *= 1 - dab
            else:
                patch += (1 - patch) * dab
    return weight


def sample(raster, us, vs):
    """Bilinear samples of `raster` (h×w, over the source) at source fractions; 0 outside it."""
    height, width = raster.shape
    px = us * width - 0.5
    py = vs * height - 0.5
    inside = (px > -1) & (px < width) & (py > -1) & (py < height)
    px, py = np.clip(px, 0, width - 1), np.clip(py, 0, height - 1)
    x0, y0 = px.astype(np.intp), py.astype(np.intp)
    x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
    fx, fy = px - x0, py - y0
    top = raster[y0, x0] * (1 - fx) + raster[y0, x1] * fx
    bottom = raster[y1, x0] * (1 - fx) + raster[y1, x1] * fx
    return np.where(inside, top * (1 - fy) + bottom * fy, 0.0).astype(np.float32)


def component_weight(component, geometry, x, y, first_row, rasters=None):
    kind = component["type"]
    if kind == "linear":
        return _linear(component, geometry, x, y)
    if kind == "radial":
        return _radial(component, geometry, x, y)
    if kind == "brush":
        return _brush(component, geometry, x, y, first_row)
    raster = (rasters or {}).get(kind)
    if raster is None:
        return np.zeros(x.shape, dtype=np.float32)
    return sample(raster, *geometry.to_source(x, y))


def weight(mask, geometry, x, y, first_row, rasters=None):
    """How much each pixel takes of `mask`, 0–1."""
    total = None
    for component in mask["components"]:
        w = component_weight(component, geometry, x, y, first_row, rasters)
        mode = component.get("mode", "add")
        if total is None:
            total = np.asarray(w, dtype=np.float32)
        elif mode == "subtract":
            total = total * (1 - w)
        elif mode == "intersect":
            total = total * w
        else:
            total = np.maximum(total, w)
    total = np.clip(total, 0.0, 1.0).astype(np.float32)
    return 1 - total if mask.get("invert") else total


def local_maps(masks, edit, full_size, first_row, rows, rasters=None):
    """{setting: rows × width extra amount} that `masks` add to the photo's settings, for those rows of the framed picture."""
    found = active(masks)
    if not found:
        return {}
    geometry = Geometry(edit, full_size)
    x, y = geometry.pixel_coords(first_row, rows)
    maps = {}
    for mask in found:
        w = weight(mask, geometry, x, y, first_row, rasters)
        for key, value in mask["settings"].items():
            if key in maps:
                maps[key] += w * np.float32(value)
            else:
                maps[key] = w * np.float32(value)
    return maps


def overlay(masks, index, edit, size, rasters=None):
    """The weight of mask `index` over a framed picture of `size` (height, width), to show it."""
    geometry = Geometry(edit, size)
    x, y = geometry.pixel_coords(0, size[0])
    return weight(masks[index], geometry, x, y, 0, rasters)
