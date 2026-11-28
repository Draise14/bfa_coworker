# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Make Vertical's plain Centre Crop (#632), bpy-free.

1. ``reframe_plain``: the strip numbers for a centred crop cover the frame for every source / frame
   pair, match what the Reframe modifier showed (the same scale, the same pan), and read back to the
   framing they were made from (so the modifier form and the plain form convert both ways).
2. Follow Subject's keys: the subject lands at the frame's middle, clamped to the picture.
3. The source: Centre Crop no longer adds a compositor modifier, Blur Fill still does, old modifier
   strips are read and converted, a keyed one is kept.

Run with::

    python -m unittest tests.test_reframe_plain_632 -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"
_PKG = "bfacw_plain_pkg"


def _import(name: str):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + name)


rp = _import("reframe_plain")
rm = _import("reframe_math")
rl = _import("wand.reframe_layout")

SOURCES = ((1920, 1080), (1440, 1080), (1000, 1000), (3840, 1600), (1080, 1920), (1280, 720))
FRAMES = ((1080, 1920), (720, 1280), (1080, 1080), (2160, 3840), (1081, 1919))


def _visible(part, sw, sh, fw, fh):
    return rl.visible_rect(part, sw, sh, fw, fh)


class TestPlainPart(unittest.TestCase):

    def test_centred_crop_covers_the_frame_and_is_centred(self):
        for (sw, sh) in SOURCES:
            for (fw, fh) in FRAMES:
                part = rp.plain_part(sw, sh, fw, fh)
                x0, y0, x1, y1 = _visible(part, sw, sh, fw, fh)
                self.assertLessEqual(x0, 1e-6, (sw, sh, fw, fh))
                self.assertLessEqual(y0, 1e-6, (sw, sh, fw, fh))
                self.assertGreaterEqual(x1, fw - 1e-6, (sw, sh, fw, fh))
                self.assertGreaterEqual(y1, fh - 1e-6, (sw, sh, fw, fh))
                self.assertAlmostEqual(part.offset[0], 0.0, places=6)
                self.assertAlmostEqual(part.offset[1], 0.0, places=6)

    def test_same_scale_as_the_modifier(self):
        for (sw, sh) in SOURCES:
            for (fw, fh) in FRAMES:
                for zoom in (1.0, 1.5, 3.0):
                    part = rp.plain_part(sw, sh, fw, fh, 0.0, 0.0, zoom)
                    self.assertAlmostEqual(part.scale, rm.mode_scale("CROP", sw, sh, fw, fh, zoom), places=9)

    def test_full_hd_on_vertical_crops_the_sides_only(self):
        part = rp.plain_part(1920, 1080, 1080, 1920)
        self.assertAlmostEqual(part.scale, 1920 / 1080 * rm.BLEED)
        self.assertLessEqual(part.crop[2], 1)       # only the hair of the's bleed top / bottom
        self.assertLessEqual(part.crop[3], 1)
        self.assertGreater(part.crop[0], 0)
        self.assertGreater(part.crop[1], 0)
        self.assertLessEqual(abs(part.crop[0] - part.crop[1]), 1)    # even on both sides

    def test_offset_pans_like_the_modifier(self):
        # Offset X = 1 shows the picture's right edge: the picture moves left by the whole pan range.
        sw, sh, fw, fh = 1920, 1080, 1080, 1920
        scale = rm.mode_scale("CROP", sw, sh, fw, fh)
        half = rm.pan_range(sw, sh, fw, fh, scale)[0]
        for ox in (-1.0, -0.4, 0.0, 0.7, 1.0):
            part = rp.plain_part(sw, sh, fw, fh, ox)
            self.assertAlmostEqual(part.offset[0], rm.offset_px(ox, half), places=6)

    def test_zoom_below_one_counts_as_one(self):
        self.assertEqual(rp.plain_part(1920, 1080, 1080, 1920, 0, 0, 0.3), rp.plain_part(1920, 1080, 1080, 1920))

    def test_offset_never_leaves_the_frame_uncovered(self):
        for (sw, sh) in SOURCES:
            for (fw, fh) in FRAMES:
                for ox, oy, zoom in ((1, 1, 1), (-1, -1, 2), (5, -5, 4), (0.3, 0.9, 1.2)):
                    part = rp.plain_part(sw, sh, fw, fh, ox, oy, zoom)
                    x0, y0, x1, y1 = _visible(part, sw, sh, fw, fh)
                    self.assertLessEqual(x0, 1e-6)
                    self.assertLessEqual(y0, 1e-6)
                    self.assertGreaterEqual(x1, fw - 1e-6)
                    self.assertGreaterEqual(y1, fh - 1e-6)


class TestRoundTrip(unittest.TestCase):

    def test_framing_reads_back(self):
        for (sw, sh) in SOURCES:
            for (fw, fh) in FRAMES:
                for ox, oy, zoom in ((0.0, 0.0, 1.0), (0.5, 0.0, 1.0), (-0.8, 0.3, 2.0), (1.0, -1.0, 1.5)):
                    part = rp.plain_part(sw, sh, fw, fh, ox, oy, zoom)
                    back = rp.plain_framing(sw, sh, fw, fh, part.scale, part.offset)
                    self.assertAlmostEqual(back["Zoom"], zoom, places=6, msg=(sw, sh, fw, fh, ox, oy, zoom))
                    # A pan axis with no room (the picture just covers the frame there) reads back as 0.
                    room_x = abs(sw * part.scale - fw) > 1e-6
                    room_y = abs(sh * part.scale - fh) > 1e-6
                    self.assertAlmostEqual(back["Offset X"], max(-1, min(1, ox)) if room_x else 0.0, places=5,
                                           msg=(sw, sh, fw, fh, ox, oy, zoom))
                    self.assertAlmostEqual(back["Offset Y"], max(-1, min(1, oy)) if room_y else 0.0, places=5,
                                           msg=(sw, sh, fw, fh, ox, oy, zoom))

    def test_a_new_frame_keeps_the_framing_in_its_own_units(self):
        part = rp.plain_part(1920, 1080, 1080, 1920, 0.5, 0.0, 1.0)
        f = rp.plain_framing(1920, 1080, 1080, 1920, part.scale, part.offset)
        other = rp.plain_part(1920, 1080, 720, 1280, f["Offset X"], f["Offset Y"], f["Zoom"])
        again = rp.plain_framing(1920, 1080, 720, 1280, other.scale, other.offset)
        self.assertAlmostEqual(again["Offset X"], 0.5, places=5)

    def test_marker_canvas(self):
        self.assertEqual(rp.marker_canvas({"w": 1080, "h": 1920}, (1, 1)), (1080, 1920))
        self.assertEqual(rp.marker_canvas(None, (7, 8)), (7, 8))
        self.assertEqual(rp.marker_canvas({"w": 0, "h": 5}, (7, 8)), (7, 8))
        self.assertEqual(rp.marker_canvas({"w": "x"}, (7, 8)), (7, 8))


class TestSubjectPart(unittest.TestCase):

    def test_subject_lands_at_the_middle(self):
        sw, sh, fw, fh = 1920, 1080, 1080, 1920
        for x in (0.35, 0.5, 0.65):
            part = rp.subject_part(sw, sh, fw, fh, x)
            # The picture fraction at the frame's middle: 0.5 + (0 - offset) / (scale * sw).
            at_middle = 0.5 + (0.0 - part.offset[0]) / (part.scale * sw)
            self.assertAlmostEqual(at_middle, x, places=6)

    def test_subject_at_the_edge_is_clamped_so_the_frame_stays_covered(self):
        sw, sh, fw, fh = 1920, 1080, 1080, 1920
        for x in (0.0, 1.0, -3.0, 4.0):
            part = rp.subject_part(sw, sh, fw, fh, x)
            x0, _y0, x1, _y1 = _visible(part, sw, sh, fw, fh)
            self.assertLessEqual(x0, 1e-6)
            self.assertGreaterEqual(x1, fw - 1e-6)

    def test_no_horizontal_crop_so_one_key_pans(self):
        part = rp.subject_part(1920, 1080, 1080, 1920, 0.3)
        self.assertEqual(part.crop[:2], (0, 0))
        self.assertEqual(rp.plain_part(1920, 1080, 1080, 1920, tracked=True).crop[:2], (0, 0))

    def test_matches_the_modifiers_follow_maths(self):
        sw, sh, fw, fh = 1920, 1080, 1080, 1920
        scale = rm.mode_scale("CROP", sw, sh, fw, fh, 1.0)
        for x in (0.3, 0.45, 0.8):
            offset = rm.subject_to_offset(x, sw, fw, scale)
            modifier = rp.plain_part(sw, sh, fw, fh, offset)
            subject = rp.subject_part(sw, sh, fw, fh, x)
            self.assertAlmostEqual(modifier.offset[0], subject.offset[0], places=5)


class _KP:
    def __init__(self, frame, value):
        self.co = [float(frame), float(value)]
        self.handle_left = [float(frame) - 1, float(value)]
        self.handle_right = [float(frame) + 1, float(value)]


class _FC:
    def __init__(self, path):
        self.data_path = path
        self.keyframe_points = []

    def update(self):
        self.keyframe_points.sort(key=lambda k: k.co[0])


class _Transform:
    def __init__(self, strip):
        self._strip = strip
        self.offset_x = self.offset_y = self.rotation = 0.0
        self.scale_x = self.scale_y = 1.0
        self.origin = (0.5, 0.5)

    def path_from_id(self, prop):
        return 'sequence_editor.strips_all["{:s}"].transform.{:s}'.format(self._strip.name, prop)

    def keyframe_insert(self, prop, frame):
        path = self.path_from_id(prop)
        bag = self._strip.scene.animation_data.action.layers[0].strips[0].channelbags[0]
        fc = next((f for f in bag.fcurves if f.data_path == path), None)
        if fc is None:
            fc = _FC(path)
            bag.fcurves.append(fc)
        for kp in fc.keyframe_points:
            if kp.co[0] == frame:
                kp.co[1] = float(getattr(self, prop))
                return
        fc.keyframe_points.append(_KP(frame, getattr(self, prop)))
        fc.update()


class _Crop:
    min_x = max_x = min_y = max_y = 0


class _Mods(list):
    def new(self, _name, type):  # pylint: disable=redefined-builtin
        mod = _Mod({"Offset X": 0.0, "Offset Y": 0.0, "Zoom": 1.0, "Blur Fill": False})
        mod.node_group = None
        self.append(mod)
        return mod


class _Strip(dict):
    """A picture strip: custom properties are the dict, ``modifiers`` a plain list."""

    def __init__(self, scene, name="Shot", size=(1920, 1080)):
        super().__init__()
        self.scene, self.name, self.type = scene, name, "IMAGE"
        self.elements = [types.SimpleNamespace(orig_width=size[0], orig_height=size[1])]
        self.transform = _Transform(self)
        self.crop = _Crop()
        self.modifiers = _Mods()


class _Scene:
    def __init__(self, size=(1080, 1920)):
        self.render = types.SimpleNamespace(resolution_x=size[0], resolution_y=size[1], resolution_percentage=100, fps=24, fps_base=1.0)
        self.frame_current = 1
        bag = types.SimpleNamespace(fcurves=[])
        self.animation_data = types.SimpleNamespace(action=types.SimpleNamespace(
            layers=[types.SimpleNamespace(strips=[types.SimpleNamespace(channelbags=[bag])])]))
        self.strips = {}
        self.sequence_editor = types.SimpleNamespace(strips_all=self.strips)

    def add(self, name="Shot", size=(1920, 1080)):
        strip = _Strip(self, name, size)
        self.strips[name] = strip
        return strip

    @property
    def fcurves(self):
        return self.animation_data.action.layers[0].strips[0].channelbags[0].fcurves


class _Mod:
    """A Reframe modifier stand-in: just its inputs and whether they are keyed."""

    def __init__(self, inputs, keyed=False):
        self.inputs, self.keyed = dict(inputs), keyed


def _load_reframe():
    """reframe.py with a stand-in ``bpy`` (only for the import; nothing here runs Blender)."""
    from unittest import mock
    bpy = types.ModuleType("bpy")
    props = types.ModuleType("bpy.props")
    btypes = types.ModuleType("bpy.types")
    for n in ("BoolProperty", "EnumProperty", "FloatProperty", "IntProperty"):
        setattr(props, n, lambda *a, **k: None)
    btypes.Menu = btypes.Operator = type("Base", (), {})
    bpy.props, bpy.types, bpy.app = props, btypes, mock.MagicMock()
    with mock.patch.dict(sys.modules, {"bpy": bpy, "bpy.props": props, "bpy.types": btypes}):
        return _import("reframe")


try:
    rf = _load_reframe()
except Exception as _ex:  # pylint: disable=broad-exception-caught
    rf = None
    _WHY = repr(_ex)


def _fake_modifier_api(case):
    """Route reframe.py's modifier helpers to the strip's first modifier so a strip can carry a Reframe modifier."""
    from unittest import mock
    patches = [
        mock.patch.object(rf, "find_modifier", lambda strip: next(iter(strip.modifiers), None)),
        mock.patch.object(rf, "get_inputs", lambda mod: dict(mod.inputs)),
        mock.patch.object(rf, "set_inputs", lambda mod, v: mod.inputs.update(v)),
        mock.patch.object(rf, "fcurve_for", lambda scene, mod, name: object() if mod.keyed else None),
        mock.patch.object(rf, "clear_keys", lambda scene, mod, names=None: setattr(mod, "keyed", False)),
        mock.patch.object(rf, "ensure_group", lambda: object()),
    ]
    for p in patches:
        p.start()
        case.addCleanup(p.stop)


def _with_modifier(strip, keyed=False, blur=False):
    mod = _Mod({"Offset X": 0.0, "Offset Y": 0.0, "Zoom": 1.0, "Blur Fill": blur}, keyed)
    strip.modifiers.append(mod)
    return mod


def _keys(scene):
    return {round(kp.co[0]): kp.co[1] for fc in scene.fcurves for kp in fc.keyframe_points}


def _shown(strip, scene, x=None):
    """The part of the frame the strip's picture shows with its pan at *x* (default: the static value)."""
    sw, sh = rf.source_size(strip)
    fw, fh = rf.frame_size(scene)
    c = strip.crop
    part = rl.Part(0, (0, 0, fw, fh), strip.transform.scale_x,
                   (strip.transform.offset_x if x is None else x, strip.transform.offset_y),
                   (c.min_x, c.max_x, c.min_y, c.max_y), None)
    return rl.visible_rect(part, sw, sh, fw, fh)


def _evaluate(strip, scene):
    """What Blender does on a redraw: the animated pan takes the value of the key at the current frame."""
    for fc in scene.fcurves:
        pts = sorted(fc.keyframe_points, key=lambda k: abs(k.co[0] - scene.frame_current))
        if pts:
            strip.transform.offset_x = pts[0].co[1]


def _covered(strip, scene):
    """Does the picture cover the frame at every pan key (and at the static value)?"""
    fw, fh = rf.frame_size(scene)
    for x in list(_keys(scene).values()) or [None]:
        x0, y0, x1, y1 = _shown(strip, scene, x)
        if x0 > 1e-6 or y0 > 1e-6 or x1 < fw - 1e-6 or y1 < fh - 1e-6:
            return False
    return True


@unittest.skipIf(rf is None, "reframe.py could not be imported without Blender")
class TestBehaviour(unittest.TestCase):
    """reframe.py driven against stand-in strips and scenes (no Blender)."""

    def setUp(self):
        _fake_modifier_api(self)
        self.scene = _Scene()
        self.strip = self.scene.add()

    def _follow(self, xs=(0.3, 0.5, 0.8)):
        results = {"Shot": [(1 + 24 * i, x) for i, x in enumerate(xs)]}
        return rf.apply_follow(self.scene, results)

    def test_added_builds_the_plain_crop_without_a_modifier(self):
        self.assertEqual(rf.reframe_strip(self.scene, self.strip, "CROP"), "added")
        self.assertEqual(self.strip.modifiers, [])
        self.assertEqual(dict(self.strip[rp.MARKER]), {"w": 1080, "h": 1920})
        self.assertGreater(self.strip.crop.min_x, 0)
        self.assertTrue(_covered(self.strip, self.scene))

    def test_kept_keeps_a_hand_made_framing(self):
        rf.reframe_strip(self.scene, self.strip, "CROP")
        self.strip.transform.offset_x += 100.0
        before = rf.plain_framing_of(self.scene, self.strip)
        self.assertEqual(rf.reframe_strip(self.scene, self.strip, "CROP"), "kept")
        after = rf.plain_framing_of(self.scene, self.strip)
        for k, v in before.items():
            self.assertAlmostEqual(after[k], v, places=4)
        self.assertLess(before["Offset X"], 0.0)
        self.assertTrue(_covered(self.strip, self.scene))

    def test_reset_centres_again_and_drops_keys(self):
        rf.reframe_strip(self.scene, self.strip, "CROP")
        self._follow()
        self.assertEqual(rf.reframe_strip(self.scene, self.strip, "CROP", reset=True), "reset")
        self.assertEqual(self.scene.fcurves, [])
        self.assertAlmostEqual(self.strip.transform.offset_x, 0.0, places=6)
        self.assertGreater(self.strip.crop.min_x, 0)

    def test_an_unkeyed_modifier_is_converted_at_the_same_framing(self):
        mod = _with_modifier(self.strip)
        mod.inputs["Offset X"], mod.inputs["Zoom"] = 0.4, 1.5
        self.assertEqual(rf.reframe_strip(self.scene, self.strip, "CROP"), "converted")
        self.assertEqual(self.strip.modifiers, [])
        f = rf.plain_framing_of(self.scene, self.strip)
        self.assertAlmostEqual(f["Offset X"], 0.4, places=4)
        self.assertAlmostEqual(f["Zoom"], 1.5, places=4)

    def test_a_keyed_modifier_is_kept(self):
        mod = _with_modifier(self.strip, keyed=True)
        self.assertEqual(rf.reframe_strip(self.scene, self.strip, "CROP"), "kept")
        self.assertEqual(self.strip.modifiers, [mod])
        self.assertIsNone(rf.plain_marker(self.strip))

    def test_blur_fill_modifier_converts_only_when_asked_for_centre_crop(self):
        _with_modifier(self.strip, blur=True)
        self.assertEqual(rf.reframe_strip(self.scene, self.strip, "CROP"), "kept")
        self.assertEqual(len(self.strip.modifiers), 1)

    def test_blur_fill_to_crop_to_blur_fill(self):
        rf.reframe_strip(self.scene, self.strip, "CROP")
        self.assertEqual(self.strip.modifiers, [])
        self.assertIsNotNone(rf.plain_marker(self.strip))
        # Blur Fill takes the plain numbers off (the modifier works in the picture's own pixels).
        self.assertEqual(rf.reframe_strip(self.scene, self.strip, "BLUR_FILL"), "added")
        self.assertEqual(len(self.strip.modifiers), 1)
        self.assertIsNone(rf.plain_marker(self.strip))
        self.assertEqual(self.strip.crop.min_x, 0)
        self.assertEqual(self.strip.transform.scale_x, 1.0)
        # ... and back to Centre Crop: the modifier is turned into the plain crop again.
        self.assertEqual(rf.reframe_strip(self.scene, self.strip, "CROP"), "kept")   # Blur Fill modifier stays
        self.assertEqual(len(self.strip.modifiers), 1)
        self.strip.modifiers[0].inputs["Blur Fill"] = False
        self.assertEqual(rf.reframe_strip(self.scene, self.strip, "CROP"), "converted")
        self.assertEqual(self.strip.modifiers, [])
        self.assertIsNotNone(rf.plain_marker(self.strip))
        self.assertTrue(_covered(self.strip, self.scene))

    def test_follow_keeps_the_frame_covered(self):
        rf.reframe_strip(self.scene, self.strip, "CROP")
        self.assertEqual(self._follow(), {"Shot": 3})
        self.assertEqual(len(_keys(self.scene)), 3)
        self.assertEqual(self.strip.crop.min_x, 0)
        self.assertTrue(_covered(self.strip, self.scene))

    def test_make_vertical_again_on_a_followed_strip_keeps_it_covered(self):
        rf.reframe_strip(self.scene, self.strip, "CROP")
        self._follow()
        before = _keys(self.scene)
        self.assertEqual(rf.reframe_strip(self.scene, self.strip, "CROP"), "kept")
        self.assertEqual(self.strip.crop.min_x, 0)            # still no horizontal crop
        self.assertTrue(_covered(self.strip, self.scene))
        for frame, value in before.items():
            self.assertAlmostEqual(_keys(self.scene)[frame], value, places=2)

    def test_make_vertical_again_at_another_size_rescales_the_keys(self):
        rf.reframe_strip(self.scene, self.strip, "CROP")
        self._follow()
        sw, _sh = rf.source_size(self.strip)
        fractions = {f: 0.5 - v / (sw * self.strip.transform.scale_x) for f, v in _keys(self.scene).items()}
        self.scene.render.resolution_x, self.scene.render.resolution_y = 720, 1280
        self.assertEqual(rf.reframe_strip(self.scene, self.strip, "CROP"), "kept")
        self.assertEqual(dict(self.strip[rp.MARKER]), {"w": 720, "h": 1280})
        self.assertTrue(_covered(self.strip, self.scene))
        for frame, u in fractions.items():            # the subject is still where the shot says
            now = 0.5 - _keys(self.scene)[frame] / (sw * self.strip.transform.scale_x)
            self.assertAlmostEqual(now, u, places=4)

    def test_zoom_on_a_followed_strip_moves_every_key(self):
        rf.reframe_strip(self.scene, self.strip, "CROP")
        self._follow()
        sw, _sh = rf.source_size(self.strip)
        fractions = {f: 0.5 - v / (sw * self.strip.transform.scale_x) for f, v in _keys(self.scene).items()}
        for action in ("ZOOM_IN", "ZOOM_IN", "ZOOM_OUT", "ZOOM_OUT", "ZOOM_OUT"):
            _evaluate(self.strip, self.scene)
            rf.apply_reframe(self.scene, self.strip, action)
            self.assertTrue(_covered(self.strip, self.scene), action)
        self.assertEqual(len(_keys(self.scene)), 3)
        _evaluate(self.strip, self.scene)
        zoomed = rf.apply_reframe(self.scene, self.strip, "ZOOM_IN")
        self.assertEqual(zoomed, "zoomed")
        for frame, u in fractions.items():
            now = 0.5 - _keys(self.scene)[frame] / (sw * self.strip.transform.scale_x)
            self.assertAlmostEqual(now, u, places=3)

    def test_a_pan_after_a_resolution_change_updates_the_marker(self):
        rf.reframe_strip(self.scene, self.strip, "CROP")
        self.scene.render.resolution_x, self.scene.render.resolution_y = 720, 1280
        rf.apply_reframe(self.scene, self.strip, "ZOOM_IN")
        self.assertEqual(dict(self.strip[rp.MARKER]), {"w": 720, "h": 1280})
        self.assertAlmostEqual(rf.plain_framing_of(self.scene, self.strip)["Zoom"], 1.1, places=4)

    def test_a_follow_after_a_resolution_change_updates_the_marker(self):
        rf.reframe_strip(self.scene, self.strip, "CROP")
        self.scene.render.resolution_x, self.scene.render.resolution_y = 720, 1280
        self._follow()
        self.assertEqual(dict(self.strip[rp.MARKER]), {"w": 720, "h": 1280})
        self.assertTrue(_covered(self.strip, self.scene))


class TestWiring(unittest.TestCase):
    """Source-level checks that need no stand-ins."""

    @classmethod
    def setUpClass(cls):
        cls.text = (_ADDON / "reframe.py").read_text(encoding="utf-8")

    def _body(self, name, end):
        start = self.text.index("def " + name)
        return self.text[start:self.text.index(end, start)]

    def test_centre_crop_adds_no_modifier(self):
        body = self._body("reframe_strip", "def _copy_scene")
        crop = body[:body.index("ng = ensure_group()")]
        self.assertNotIn("modifiers.new", crop)
        self.assertNotIn("ensure_group", crop)

    def test_make_vertical_reports_converted(self):
        make = self._body("make_vertical", "# -- Nudge / mode / reset")
        self.assertIn('"converted"', make)


if __name__ == "__main__":
    unittest.main()
