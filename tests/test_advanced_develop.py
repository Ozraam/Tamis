"""The settings of the edit panel's Advanced tab, in the pipeline: develop.render() and the modules it uses."""
import math
import struct

import numpy as np
import pytest

from tamis import autoadjust, automasks, colours, curves, develop, lenses, local, masks, profiles, spots
from tamis.develop import clean, render


def grey(value, size=(8, 8)):
    return np.full((*size, 3), value, dtype=np.float32)


def noisy(height=120, width=160, seed=1):
    rng = np.random.default_rng(seed)
    base = np.full((height, width, 3), 0.18, dtype=np.float32)
    base[:, width // 2:] = 0.05
    return np.clip(base + rng.normal(0, 0.01, base.shape).astype(np.float32), 0, 1)


def colour(rgb, size=(4, 4)):
    return np.broadcast_to(np.array(rgb, dtype=np.float32), (*size, 3)).copy()


def luma(pixels):
    return pixels.astype(np.float32) @ develop.LUMA


# -- cleaning ----------------------------------------------------------------------

def test_clean_keeps_advanced_settings_and_drops_their_defaults():
    edit = clean({"whites": 20, "blacks": 0, "hue_red": 200, "grade_blending": 50, "sharpen_radius": 1.234, "bw": True})
    assert edit == {"whites": 20, "hue_red": 100, "sharpen_radius": 1.2}  # True is not a number
    assert clean({"sharpen": 0}) == {"sharpen": 0}  # 0 is not the RAW's automatic amount
    assert clean({"sharpen": None}) == {}
    assert develop.advanced({"whites": 5}) and not develop.advanced({"exposure": 1, "process": 1})


def test_curves_are_cleaned_and_straight_ones_dropped():
    assert curves.clean({"rgb": [[0, 0], [1, 1]]}) is None
    assert curves.clean({"rgb": [[0, 0], [0.5, 0.7], [1, 1]], "red": [[0.5, 0], [0.4, 1]]}) == {
        "rgb": [[0.0, 0.0], [0.5, 0.7], [1.0, 1.0]]  # red's points go backwards: dropped
    }
    assert clean({"curve": {"blue": [[0, 0.1], [1, 1]]}}) == {"curve": {"blue": [[0.0, 0.1], [1.0, 1.0]]}}


def test_masks_and_spots_are_cleaned():
    found = clean({"masks": [
        {"components": [{"type": "linear", "x0": 0, "y0": 0, "x1": 0, "y1": 1, "mode": "subtract"}],
         "settings": {"exposure": 9, "nope": 3, "clarity": 0}},
        {"components": [{"type": "unknown"}]},
    ], "spots": [{"points": [[0.5, 0.5]], "r": 0.01, "dx": 0.1, "dy": 0}, {"points": [], "r": 0.1}]})
    assert found["masks"] == [{
        "components": [{"type": "linear", "x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 1.0}],  # the first one has no mode
        "settings": {"exposure": 4.0},
    }]
    assert found["spots"] == [{"points": [[0.5, 0.5]], "r": 0.01, "dx": 0.1, "dy": 0.0}]


def test_masks_and_spots_belong_to_their_photo():
    edit = {"masks": [{"components": [{"type": "sky"}], "settings": {"exposure": -1}}], "spots": [1], "whites": 5,
            "crop": [0, 0, .5, .5]}
    assert develop.look(edit) == {"whites": 5}
    assert develop.unedited(edit) == {"crop": [0, 0, .5, .5]}


# -- per pixel -----------------------------------------------------------------------

def test_whites_and_blacks_move_the_ends():
    assert render(grey(0.9), {"whites": 80})[0, 0, 0] > render(grey(0.9), {})[0, 0, 0]
    assert render(grey(0.005), {"blacks": -80})[0, 0, 0] < render(grey(0.005), {})[0, 0, 0]
    assert abs(int(render(grey(0.2), {"whites": 30})[0, 0, 0]) - int(render(grey(0.2), {})[0, 0, 0])) <= 3


def test_tone_curves_change_their_channels():
    lifted = render(grey(0.2), {"curve": {"rgb": [[0, 0], [0.5, 0.65], [1, 1]]}})[0, 0]
    plain = render(grey(0.2), {})[0, 0]
    assert (lifted > plain).all()
    red = render(grey(0.2), {"curve": {"red": [[0, 0], [0.5, 0.7], [1, 1]]}})[0, 0]
    assert red[0] > plain[0] and red[1] == plain[1] and red[2] == plain[2]
    darker = render(grey(0.05), {"curve_shadows": -100})[0, 0, 0]
    assert darker < plain[0] or darker < render(grey(0.05), {})[0, 0, 0]


def test_monotone_curves_never_overshoot():
    x = np.linspace(0, 1, 101)
    y = curves.interpolate([[0, 0], [0.3, 0.6], [0.35, 0.6], [1, 1]], x)
    assert (np.diff(y) >= -1e-9).all() and y.max() <= 1.0


def test_vibrance_lifts_muted_colours_more_than_saturated_ones():
    def sat(pixel):
        pixel = pixel.astype(float)
        return (pixel.max() - pixel.min()) / max(pixel.max(), 1)

    muted, vivid = colour([0.2, 0.25, 0.4]), colour([0.02, 0.05, 0.6])
    gain_muted = sat(render(muted, {"vibrance": 80})[0, 0]) - sat(render(muted, {})[0, 0])
    gain_vivid = sat(render(vivid, {"vibrance": 80})[0, 0]) - sat(render(vivid, {})[0, 0])
    assert gain_muted > gain_vivid >= -0.01


def test_hsl_mixer_changes_only_its_colours():
    red, blue = colour([0.6, 0.02, 0.02]), colour([0.02, 0.05, 0.6])
    shifted = render(red, {"hue_red": 100})[0, 0].astype(int)
    assert shifted[1] > render(red, {})[0, 0, 1] + 20  # towards orange
    assert (render(blue, {"hue_red": 100}) == render(blue, {})).all()
    assert luma(render(blue, {"lum_blue": -80})).mean() < luma(render(blue, {})).mean()
    assert (render(grey(0.2), {"sat_red": 100, "lum_red": 100, "hue_red": 50}) == render(grey(0.2), {})).all()


def test_black_and_white_mixes_colours_into_grey():
    red = colour([0.6, 0.02, 0.02])
    plain = render(red, {"bw": 1})[0, 0]
    assert plain[0] == plain[1] == plain[2]
    assert render(red, {"bw": 1, "bw_red": 80})[0, 0, 0] > plain[0]


def test_colour_grading_tints_the_shadows_not_the_highlights():
    dark, bright = grey(0.01), grey(0.8)
    graded = {"grade_shadows_hue": 240, "grade_shadows_sat": 80}
    tinted = render(dark, graded)[0, 0].astype(int)
    assert tinted[2] > tinted[0]
    assert abs(render(bright, graded)[0, 0].astype(int) - render(bright, {})[0, 0].astype(int)).max() <= 2
    assert colours.has_grading(graded) and not colours.has_grading({"grade_shadows_hue": 240})


# -- neighbourhood ------------------------------------------------------------------------

@pytest.mark.parametrize("edit", [
    {"texture": 70}, {"clarity": 60}, {"dehaze": 50}, {"clarity": -60, "texture": -50}, {"sharpen": 120, "sharpen_masking": 30},
    {"nr_luma": 60, "nr_color": 70}, {"defringe": 60},
    {"masks": [{"components": [{"type": "radial", "x": 0.4, "y": 0.5, "rx": 0.2, "ry": 0.3}], "settings": {"clarity": 80}}]},
])
def test_neighbourhood_settings_render_the_same_in_strips(edit):
    linear = noisy(150, 120)
    whole = render(linear, edit)
    extra = develop.margin(edit, linear.shape[:2])
    assert extra > 0
    strips = np.concatenate([
        develop.render_rows(linear, top, min(150, top + 20), edit, extra=extra) for top in range(0, 150, 20)
    ])
    assert np.abs(whole.astype(int) - strips.astype(int)).max() <= 1


def test_radii_follow_the_picture_size():
    small, large = local.radii({}, 2048), local.radii({}, 6000)
    assert large["clarity"] / small["clarity"] == pytest.approx(6000 / 2048, rel=0.05)


def test_texture_and_clarity_add_detail_and_take_it_away():
    linear = noisy()
    plain = luma(render(linear, {}))
    assert luma(render(linear, {"texture": 80})).std() > plain.std()
    assert luma(render(linear, {"texture": -80}))[:, :70].std() < plain[:, :70].std()


def test_noise_reduction_smooths_noise():
    linear = noisy()
    plain = luma(render(linear, {}))[:, 10:70]
    assert luma(render(linear, {"nr_luma": 90}))[:, 10:70].std() < plain.std() * 0.8


def test_sharpening_raises_edge_contrast_and_raws_get_some_by_default():
    linear = np.zeros((40, 40, 3), dtype=np.float32) + 0.05
    linear[:, 20:] = 0.4
    plain = render(linear, {})[20].astype(int)[:, 0]
    # Radii are of the whole picture: at 6000 pixels across, a radius of 1 is a pixel.
    sharp = render(linear, {"sharpen": 150, "sharpen_detail": 100}, full_size=(6000, 6000))[20].astype(int)[:, 0]
    assert sharp[21] > plain[21] and sharp[18] < plain[18]
    assert local.amounts({"sharpen": None}, raw=True)["sharpen"] == local.SHARPEN_RAW
    assert "sharpen" not in local.amounts({"sharpen": None}, raw=False)


def test_views_show_the_sharpening_mask_and_spots():
    linear = noisy()
    mask = render(linear, {"sharpen_masking": 50, "_view": "sharpen_mask"})
    assert mask[..., 0].tolist() == mask[..., 1].tolist()
    spotted = render(linear, {"_view": "spots"})
    assert spotted[..., 0].tolist() == spotted[..., 2].tolist()


def test_dehaze_adds_contrast_to_a_hazy_picture():
    hazy = noisy() * 0.3 + 0.5
    assert luma(render(hazy, {"dehaze": 80})).std() > luma(render(hazy, {})).std()


# -- masks ----------------------------------------------------------------------------------

def test_geometry_goes_from_the_frame_to_the_source_and_back():
    edit = {"rotate": 90, "straighten": 4.0, "crop": [0.1, 0.2, 0.8, 0.9]}
    geometry = masks.Geometry(edit, (300, 200))
    for fx, fy in ((0.2, 0.3), (0.9, 0.1), (0.5, 0.5)):
        sx, sy = geometry.framed_to_source(fx, fy)
        assert geometry.source_to_framed(sx, sy) == pytest.approx((fx, fy), abs=1e-6)
    # A quarter turn clockwise: the top left of the turned picture is the source's bottom left.
    turned = masks.Geometry({"rotate": 90}, (300, 200))
    assert turned.framed_to_source(0, 0) == pytest.approx((0, 1))


def test_a_linear_gradient_masks_the_top_of_the_photo():
    edit = {"masks": [{"components": [{"type": "linear", "x0": 0.5, "y0": 0.2, "x1": 0.5, "y1": 0.6}],
                       "settings": {"exposure": -2}}]}
    out = render(grey(0.18, (100, 60)), edit)
    assert out[5, 30, 0] < out[95, 30, 0] == render(grey(0.18), {})[0, 0, 0]


def test_a_mask_follows_its_photo_through_a_turn():
    edit = {"masks": [{"components": [{"type": "radial", "x": 0.2, "y": 0.5, "rx": 0.1, "ry": 0.1, "feather": 0}],
                       "settings": {"exposure": 2}}]}
    source = grey(0.1, (60, 100))
    out = render(source, edit)
    assert out[30, 20, 0] > out[30, 80, 0]
    turned = render(np.rot90(source, -1).copy(), {**edit, "rotate": 90})  # the source's left is now the top
    assert turned[20, 30, 0] > turned[80, 30, 0]


def test_brush_strokes_add_and_erase():
    stroke = {"points": [[0.1, 0.5], [0.9, 0.5]], "size": 0.05, "feather": 0, "flow": 100}
    component = {"type": "brush", "strokes": [stroke, {**stroke, "points": [[0.5, 0.2], [0.5, 0.8]], "erase": True}]}
    mask = masks.clean_mask({"components": [component], "settings": {"exposure": 1}})
    weight = masks.overlay([mask], 0, {}, (100, 100))
    assert weight[50, 20] > 0.9 and weight[50, 50] < 0.1 and weight[10, 20] == 0


def test_masks_combine():
    left = {"type": "linear", "x0": 0.4, "y0": 0.5, "x1": 0.6, "y1": 0.5}
    top = {"type": "linear", "x0": 0.5, "y0": 0.4, "x1": 0.5, "y1": 0.6}
    both = masks.clean_mask({"components": [left, {**top, "mode": "intersect"}], "settings": {"exposure": 1}})
    weight = masks.overlay([both], 0, {}, (100, 100))
    assert weight[10, 10] > 0.9 and weight[10, 90] < 0.1 and weight[90, 10] < 0.1
    inverted = masks.overlay([{**both, "invert": True}], 0, {}, (100, 100))
    assert inverted[10, 10] < 0.1


def test_automatic_masks_are_sampled_from_their_raster():
    raster = np.zeros((10, 10), dtype=np.float32)
    raster[:, :5] = 1
    edit = {"masks": [{"components": [{"type": "subject"}], "settings": {"exposure": 2}}]}
    out = render(grey(0.1, (20, 20)), edit, rasters={"subject": raster})
    assert out[10, 2, 0] > out[10, 17, 0]
    assert (render(grey(0.1, (20, 20)), edit) == render(grey(0.1, (20, 20)), {})).all()  # not found yet: no effect


# -- spots ------------------------------------------------------------------------------------

def test_a_spot_is_healed_from_elsewhere():
    pixels = np.full((80, 80, 3), 0.5, dtype=np.float32)
    pixels[38:43, 38:43] = 0.05  # dust
    spot = {"points": [[0.5, 0.5]], "r": 0.06, "dx": 0.25, "dy": 0.0}
    healed = spots.apply(pixels, [spot])
    assert abs(healed[40, 40, 0] - 0.5) < 0.02 and pixels[40, 40, 0] == pytest.approx(0.05)
    dx, dy = spots.find_source(pixels, 0.5, 0.5, 0.06)
    assert math.hypot(dx * 80, dy * 80) > 0.06 * 80 * 2


def test_spots_work_on_integer_pixels_and_clone():
    pixels = np.full((60, 60, 3), 30000, dtype=np.uint16)
    pixels[:, 40:] = 50000
    cloned = spots.apply(pixels, [{"points": [[0.3, 0.5]], "r": 0.05, "dx": 0.5, "dy": 0.0, "mode": "clone"}])
    assert cloned.dtype == np.uint16 and cloned[30, 18, 0] == 50000


# -- lenses ---------------------------------------------------------------------------------------

LENSFUN = """<lensdatabase version="2">
  <camera><maker>Panasonic</maker><model>DC-G9</model><mount>Micro 4/3 System</mount><cropfactor>2.0</cropfactor></camera>
  <lens><maker>Panasonic</maker><model>Lumix G Vario 12-60mm f/3.5-5.6 Asph. Power OIS</model>
    <mount>Micro 4/3 System</mount><cropfactor>2.0</cropfactor>
    <calibration>
      <distortion model="ptlens" focal="12" a="0.01" b="-0.05" c="0.0"/>
      <distortion model="ptlens" focal="60" a="0" b="0.01" c="0"/>
      <tca model="poly3" focal="12" vr="1.0003" vb="0.9997"/>
      <vignetting model="pa" focal="12" aperture="3.5" distance="10" k1="-0.4" k2="0.1" k3="0"/>
      <vignetting model="pa" focal="12" aperture="8" distance="10" k1="-0.1" k2="0" k3="0"/>
    </calibration>
  </lens>
</lensdatabase>"""


def test_lens_profiles_are_read_from_the_lensfun_database(tmp_path):
    (tmp_path / "mil-panasonic.xml").write_text(LENSFUN)
    info = {"make": "Panasonic", "model": "DC-G9", "lens": "LUMIX G VARIO 12-60/F3.5-5.6", "focal": 36.0, "aperture": 4.0}
    found = lenses.find(info, str(tmp_path))
    assert found["name"].startswith("Lumix G Vario 12-60mm")
    assert found["distortion"][0] == "ptlens"
    assert found["distortion"][1][2:4] == pytest.approx([0.005, -0.02], abs=1e-6)  # halfway between 12 and 60 mm
    assert found["vignetting"] == [-0.4, 0.1, 0.0]  # the nearest aperture
    assert lenses.find({**info, "lens": "Something Else 50mm"}, str(tmp_path)) is None


def test_lens_corrections_move_pixels_and_brighten_corners():
    pixels = np.random.default_rng(0).random((60, 90, 3), dtype=np.float32)
    assert np.allclose(lenses.warp_rows(pixels, {}, None, 0, 60), pixels, atol=1e-5)
    warped = lenses.warp_rows(pixels, {"distortion": 60}, None, 0, 60)
    assert not np.allclose(warped, pixels) and np.allclose(warped[30, 45], pixels[30, 45], atol=1e-4)
    geometry = masks.Geometry({}, (60, 90))
    x, y = geometry.pixel_coords(0, 60)
    gain = lenses.vignette_gain({"devignette": 50}, None, geometry, x, y)
    assert gain[0, 0] > 1.3 and gain[30, 45] == pytest.approx(1.0, abs=0.01)
    profile = {"vignetting": [-0.4, 0.1, 0.0], "scale": 1.0}
    assert lenses.vignette_gain({"lens_profile": 1, "lens_vignetting": 100}, profile, geometry, x, y)[0, 0] > 1.2


# -- profiles ---------------------------------------------------------------------------------------

def write_dcp(path, camera="Panasonic DC-G9", name="Test Standard", hue_shift=0.0, tone=None):
    """A minimal DCP: forward matrix (identity to ProPhoto's XYZ), a 2×2×1 hue / saturation map, maybe a tone curve."""
    entries = []
    forward = (profiles._PROPHOTO_TO_XYZ).reshape(-1)
    entries.append((0xC614, 2, camera.encode() + b"\0"))
    entries.append((0xC6F8, 2, name.encode() + b"\0"))
    entries.append((0xC65A, 3, struct.pack("<H", 21)))
    entries.append((0xC714, 10, b"".join(struct.pack("<ii", round(v * 1e6), 1000000) for v in forward)))
    entries.append((0xC6F9, 4, struct.pack("<III", 2, 2, 1)))
    entries.append((0xC6FA, 11, struct.pack("<12f", *([hue_shift, 1.0, 1.0] * 4))))
    if tone is not None:
        entries.append((0xC6FC, 11, struct.pack(f"<{len(tone) * 2}f", *[v for p in tone for v in p])))
    counts = {2: 1, 3: 2, 4: 4, 10: 8, 11: 4}
    header = b"IIRC" + struct.pack("<I", 8)
    directory = struct.pack("<H", len(entries))
    data_offset = 8 + 2 + 12 * len(entries) + 4
    blobs = b""
    for tag, kind, blob in entries:
        number = len(blob) // counts[kind]
        if len(blob) <= 4:
            directory += struct.pack("<HHI", tag, kind, number) + blob.ljust(4, b"\0")
        else:
            directory += struct.pack("<HHII", tag, kind, number, data_offset + len(blobs))
            blobs += blob
    path.write_bytes(header + directory + struct.pack("<I", 0) + blobs)


def test_dcp_profiles_are_read_and_keep_grey_grey(tmp_path):
    write_dcp(tmp_path / "test.dcp")
    found = profiles.scan([str(tmp_path)])
    assert list(found) == ["Panasonic DC-G9/Test Standard"]
    profile = profiles.load(str(tmp_path / "test.dcp"))
    block = np.full((2, 2, 3), 10000, dtype=np.uint16)
    out = profile.linearize(block)
    assert out[0, 0] == pytest.approx(out[0, 0, 1], rel=1e-3)
    assert profiles.matches(profile.camera, "Panasonic", "DC-G9") and not profiles.matches(profile.camera, "Canon", "EOS R5")


def test_dcp_hue_maps_and_tone_curves_change_colours(tmp_path):
    write_dcp(tmp_path / "shift.dcp", hue_shift=60.0, tone=[[0, 0], [0.1, 0.3], [1, 1]])
    profile = profiles.load(str(tmp_path / "shift.dcp"))
    red = np.zeros((1, 1, 3), dtype=np.uint16)
    red[..., 0] = 8000
    out = profile.linearize(red)[0, 0]
    assert out[1] > out[2]  # red turned towards yellow
    assert profile.has_tone


# -- automatic settings and masks ----------------------------------------------------------------------

def test_white_balance_from_a_pixel_makes_it_grey():
    pixel = np.array([0.3, 0.25, 0.15], dtype=np.float32)
    values = autoadjust.white_balance_for(pixel)
    balanced = render(np.broadcast_to(pixel, (2, 2, 3)).copy(), values)[0, 0].astype(int)
    assert balanced.max() - balanced.min() <= 3


def test_auto_tone_brightens_a_dark_photo():
    dark = noisy() * 0.15
    edit = autoadjust.auto_tone(dark, {"temperature": 10})
    assert edit["exposure"] > 1 and edit["temperature"] == 10
    assert luma(render(dark, edit)).mean() > luma(render(dark, {})).mean() * 2


def test_the_sky_is_found_where_it_is():
    picture = np.full((60, 80, 3), 0.3, dtype=np.float32)
    picture[:25] = [0.45, 0.65, 0.95]
    found = automasks.find("sky", picture)
    assert found[5].mean() > 0.8 and found[50].mean() < 0.1


def test_automatic_masks_come_back_from_disk(tmp_path):
    photo = tmp_path / "IMG_1.jpg"
    photo.write_bytes(b"x")
    store = automasks.MaskStore(str(tmp_path / "masks"))
    mask = np.zeros((20, 30), dtype=np.float32)
    mask[:10] = 1
    store.save(str(photo), "subject", mask)
    again = automasks.MaskStore(str(tmp_path / "masks")).load(str(photo), ["subject", "sky"])
    assert list(again) == ["subject"] and again["subject"][:10].mean() == pytest.approx(1.0)
    found = automasks.find("subject", np.zeros((10, 10, 3), np.float32), vision=lambda kind, png: np.ones((5, 5), np.float32))
    assert found.shape == (10, 10)


def test_a_raw_is_developed_through_its_dcp_profile(tmp_path):
    from rawfiles import write_dng

    from tamis import editing

    path = str(write_dng(tmp_path / "IMG_1.dng", color=(0.3, 0.3, 0.3), marker=False))
    write_dcp(tmp_path / "test.dcp", camera="Test Camera", tone=[[0, 0], [0.2, 0.5], [1, 1]])
    context = editing.CONTEXT
    saved = context.profiles, context.camera_profiles
    try:
        context.profiles = profiles.scan([str(tmp_path)])
        context.camera_profiles = {}
        edit = {"profile": "Test Camera/Test Standard"}
        assert editing.process_of(path, edit) == (develop.PROCESS, "Test Camera/Test Standard")
        assert editing.raw_mode(path, edit) == "profiled"
        standard = editing.develop_base(path, 64)
        profiled = editing.develop_base(path, 64, editing.process_of(path, edit))
        assert profiled.shape == standard.shape and not np.allclose(profiled, standard)
        assert editing.raw_mode(path, {"profile": profiles.NEUTRAL}) == profiles.NEUTRAL
        assert editing.CONTEXT.profile(path, {"profile": "Gone/Profile"}) is None  # missing: Standard
    finally:
        context.profiles, context.camera_profiles = saved
