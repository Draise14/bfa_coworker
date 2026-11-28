# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for #542 Zoom To: quick presets (the centre or a corner, 1.5x or 2x) that set a strip's own crop and transform,
optionally keyed as a punch-in over the shot (made-up strips on the stand-ins of ``test_reframe_plain_632``).

1. The preset: every region at both zooms leaves the frame covered (no black areas) on landscape and vertical frames,
   reads back as the zoom it was made with, and pushes the picture to the right edge.
2. Punch In: four keys on scale and offset, the strip's first frame, the end of the ease-in, the start of the ease-out and
   its last frame; the picture covers the frame at each key and at every blend between them; the crop is zero.
3. Reset Framing takes every key, the tag, the crop and the offsets away; Zoom To again replaces a punch-in; a nudge is
   refused; a followed strip, a Blur Fill strip, a split's part and a too short strip are refused with the reason.
4. The operator, its menu, the tool arguments.

Run with::

    python -m unittest discover -s tests -p "test_zoom_to_542.py" -v
"""

__all__ = ()

import re
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import test_reframe_plain_632 as R

_ROOT = Path(__file__).resolve().parents[1]
_ADDON = _ROOT / "addon" / "bfa_coworker"
_TOOLS = _ROOT / "mcp" / "blmcp" / "tools"
rf, rl, rp = R.rf, R.rl, R.rp


def _load_zoom():
    bpy = types.ModuleType("bpy")
    props = types.ModuleType("bpy.props")
    btypes = types.ModuleType("bpy.types")
    for n in ("BoolProperty", "EnumProperty", "FloatProperty", "IntProperty"):
        setattr(props, n, lambda *a, **k: None)
    btypes.Menu = btypes.Operator = type("Base", (), {})
    bpy.props, bpy.types, bpy.app = props, btypes, mock.MagicMock()
    bpy.utils = types.SimpleNamespace(register_class=lambda c: None, unregister_class=lambda c: None)
    with mock.patch.dict(sys.modules, {"bpy": bpy, "bpy.props": props, "bpy.types": btypes}):
        return R._import("reframe_zoom")


rz = _load_zoom()


def setUpModule():
    """The lazy ``from .reframe import ...`` in reframe_zoom finds the stand-in-loaded modules (the loader's sys.modules
    patch forgot them)."""
    sys.modules[R._PKG + ".reframe"] = rf
    sys.modules[R._PKG + ".reframe_zoom"] = rz


def tearDownModule():
    sys.modules.pop(R._PKG + ".reframe", None)
    sys.modules.pop(R._PKG + ".reframe_zoom", None)


def _split_free(case):
    """The stand-in strips are not parts of a split, and the modifier helpers read the strip's own list."""
    R._fake_modifier_api(case)


def _strip(scene, left=1, right=121, size=(1920, 1080), name="Shot"):
    strip = scene.add(name, size)
    strip.left_handle, strip.right_handle, strip.type = left, right, "MOVIE"
    return strip


def _covers(strip, scene, scale, offset, crop=(0, 0, 0, 0)):
    sw, sh = rf.source_size(strip)
    fw, fh = rf.frame_size(scene)
    part = rl.Part(0, (0, 0, fw, fh), scale, offset, crop, None)
    x0, y0, x1, y1 = rl.visible_rect(part, sw, sh, fw, fh)
    return x0 <= 1e-6 and y0 <= 1e-6 and x1 >= fw - 1e-6 and y1 >= fh - 1e-6


def _curve(scene, strip, prop):
    path = strip.transform.path_from_id(prop)
    return sorted(((kp.co[0], kp.co[1]) for fc in scene.fcurves if fc.data_path == path for kp in fc.keyframe_points))


def _at(points, frame):
    """The straight blend between keys (what Bezier does is a smoother version of the same line)."""
    for (f0, v0), (f1, v1) in zip(points, points[1:]):
        if f0 <= frame <= f1:
            return v0 + (v1 - v0) * ((frame - f0) / (f1 - f0) if f1 > f0 else 0.0)
    return points[-1][1]


@unittest.skipIf(rf is None, "reframe.py could not be imported without Blender")
class TestPresets(unittest.TestCase):

    def setUp(self):
        _split_free(self)
        self.patch = mock.patch.object(rz._layouts, "is_split_part", lambda strip: bool(strip.get("is_split")))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_every_region_and_zoom_covers_the_frame(self):
        for frame in ((1080, 1920), (1920, 1080), (1080, 1080)):
            for size in ((1920, 1080), (1440, 1080), (1080, 1920), (3840, 1600)):
                for region, *_rest in rz.REGIONS:
                    for zoom in (1.5, 2.0):
                        scene = R._Scene(frame)
                        strip = _strip(scene, size=size)
                        self.assertEqual(rz.zoom_to(scene, strip, region, zoom), "zoomed")
                        c = strip.crop
                        self.assertTrue(_covers(strip, scene, strip.transform.scale_x,
                                                (strip.transform.offset_x, strip.transform.offset_y),
                                                (c.min_x, c.max_x, c.min_y, c.max_y)), (frame, size, region, zoom))

    def test_it_reads_back_as_the_zoom_and_pushes_to_the_corner(self):
        scene = R._Scene((1920, 1080))
        strip = _strip(scene, size=(3840, 2160))
        rz.zoom_to(scene, strip, "TOP_LEFT", 2.0)
        f = rf.plain_framing_of(scene, strip)
        self.assertAlmostEqual(f["Zoom"], 2.0, places=4)
        self.assertAlmostEqual(f["Offset X"], -1.0, places=4)
        self.assertAlmostEqual(f["Offset Y"], 1.0, places=4)
        rz.zoom_to(scene, strip, "BOTTOM_RIGHT", 1.5)
        f = rf.plain_framing_of(scene, strip)
        self.assertEqual([round(f[k], 4) for k in ("Zoom", "Offset X", "Offset Y")], [1.5, 1.0, -1.0])
        rz.zoom_to(scene, strip, "CENTER", 2.0)
        f = rf.plain_framing_of(scene, strip)
        self.assertEqual([round(f[k], 4) + 0.0 for k in ("Offset X", "Offset Y")], [0.0, 0.0])

    def test_the_corner_pushes_the_picture_the_right_way(self):
        scene = R._Scene((1080, 1920))
        strip = _strip(scene)
        rz.zoom_to(scene, strip, "TOP_LEFT", 2.0)
        left_x = strip.transform.offset_x
        rz.zoom_to(scene, strip, "TOP_RIGHT", 2.0)
        self.assertGreater(left_x, strip.transform.offset_x)        # the right edge is shown: the picture sits further left
        self.assertGreater(left_x, 0)                               # the left edge shows: the picture sits to the right
        top_y = strip.transform.offset_y
        self.assertLess(top_y, 0)                                   # the top edge shows: the picture sits lower
        rz.zoom_to(scene, strip, "BOTTOM_LEFT", 2.0)
        self.assertGreater(strip.transform.offset_y, top_y)

    def test_a_strip_with_no_framing_gets_the_centre_crop_first(self):
        scene = R._Scene((1080, 1920))
        strip = _strip(scene)
        self.assertIsNone(rf.plain_marker(strip))
        rz.zoom_to(scene, strip, "CENTER", 1.5)
        self.assertEqual(dict(strip[rp.MARKER]), {"w": 1080, "h": 1920})

    def test_it_goes_on_from_zoom_in_and_pan(self):
        scene = R._Scene((1080, 1920))
        strip = _strip(scene)
        rz.zoom_to(scene, strip, "TOP_LEFT", 2.0)
        rf.apply_reframe(scene, strip, "ZOOM_IN", 0.1)
        self.assertAlmostEqual(rf.plain_framing_of(scene, strip)["Zoom"], 2.2, places=3)

    def test_reset_framing_undoes_it(self):
        scene = R._Scene((1080, 1920))
        strip = _strip(scene)
        rf.reframe_strip(scene, strip, "CROP")
        plain = (strip.transform.scale_x, strip.transform.offset_x, strip.crop.min_x)
        rz.zoom_to(scene, strip, "BOTTOM_RIGHT", 2.0)
        self.assertNotEqual((strip.transform.scale_x, strip.transform.offset_x), plain[:2])
        rf.apply_reframe(scene, strip, "RESET_FRAMING")
        now = (strip.transform.scale_x, strip.transform.offset_x, strip.crop.min_x)
        for a, b in zip(now, plain):
            self.assertAlmostEqual(a, b, places=6)
        self.assertAlmostEqual(strip.transform.offset_y, 0.0, places=6)
        self.assertAlmostEqual(rf.plain_framing_of(scene, strip)["Zoom"], 1.0, places=4)

    def test_unknown_region_and_zoom_are_refused(self):
        scene = R._Scene()
        strip = _strip(scene)
        with self.assertRaises(rf.ReframeError):
            rz.zoom_to(scene, strip, "MIDDLE", 2.0)
        with self.assertRaises(rf.ReframeError):
            rz.zoom_to(scene, strip, "CENTER", 1.0)
        with self.assertRaises(rf.ReframeError):
            rz.zoom_to(scene, strip, "CENTER", 40.0)

    def test_region_ids_as_an_agent_writes_them(self):
        for text, want in (("top left", "TOP_LEFT"), ("Top-Right", "TOP_RIGHT"), ("centre", "CENTER"), ("center", "CENTER"),
                           ("BOTTOM_LEFT", "BOTTOM_LEFT"), ("bottom right", "BOTTOM_RIGHT"), ("", ""), ("middle", ""), (None, "")):
            self.assertEqual(rz.region_id(text), want, text)


@unittest.skipIf(rf is None, "reframe.py could not be imported without Blender")
class TestPunchIn(unittest.TestCase):

    def setUp(self):
        _split_free(self)
        self.patch = mock.patch.object(rz._layouts, "is_split_part", lambda strip: bool(strip.get("is_split")))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.scene = R._Scene((1080, 1920))
        self.strip = _strip(self.scene, left=11, right=131)          # 120 frames at 24 fps: 5 s

    def punch(self, region="TOP_RIGHT", zoom=2.0, ease=0.5):
        return rz.zoom_to(self.scene, self.strip, region, zoom, True, ease)

    def test_the_key_frames_ease_in_hold_and_ease_out(self):
        self.assertEqual(self.punch(), "punched")
        for prop in ("scale_x", "scale_y", "offset_x", "offset_y"):
            self.assertEqual([f for f, _v in _curve(self.scene, self.strip, prop)], [11, 23, 118, 130], prop)

    def test_the_ends_are_the_plain_framing_and_the_middle_the_zoomed_one(self):
        self.punch("BOTTOM_LEFT", 2.0)
        base = rp.plain_part(1920, 1080, 1080, 1920)
        scale = _curve(self.scene, self.strip, "scale_x")
        self.assertAlmostEqual(scale[0][1], base.scale, places=6)
        self.assertAlmostEqual(scale[3][1], base.scale, places=6)
        self.assertAlmostEqual(scale[1][1], scale[2][1], places=9)
        self.assertAlmostEqual(scale[1][1], base.scale * 2.0, places=6)
        self.assertGreater(_curve(self.scene, self.strip, "offset_x")[1][1], 0)   # the left edge shows
        self.assertGreater(_curve(self.scene, self.strip, "offset_y")[1][1], 0)   # the bottom edge shows
        self.assertAlmostEqual(_curve(self.scene, self.strip, "offset_x")[0][1], 0.0, places=6)

    def test_the_frame_is_covered_at_every_key_and_every_blend(self):
        for region, *_rest in rz.REGIONS:
            for zoom in (1.5, 2.0):
                scene = R._Scene((1080, 1920))
                strip = _strip(scene, left=1, right=97, size=(1920, 1080))
                rz.zoom_to(scene, strip, region, zoom, True, 0.5)
                curves = {p: _curve(scene, strip, p) for p in ("scale_x", "offset_x", "offset_y")}
                for frame in range(1, 97):
                    self.assertTrue(_covers(strip, scene, _at(curves["scale_x"], frame),
                                            (_at(curves["offset_x"], frame), _at(curves["offset_y"], frame))),
                                    (region, zoom, frame))

    def test_there_is_no_crop_while_keyed(self):
        self.punch()
        c = self.strip.crop
        self.assertEqual((c.min_x, c.max_x, c.min_y, c.max_y), (0, 0, 0, 0))

    def test_the_keys_are_smoothed_and_the_strip_is_tagged(self):
        self.punch("CENTER", 1.5, 0.4)
        self.assertEqual(dict(self.strip[rp.PUNCH]), {"region": "CENTER", "zoom": 1.5, "ease": 0.4})
        self.assertTrue(all(kp.interpolation == "BEZIER" and kp.handle_left_type == "AUTO_CLAMPED"
                            for fc in self.scene.fcurves for kp in fc.keyframe_points))
        self.assertIsNotNone(rf.plain_marker(self.strip))

    def test_zoom_to_again_replaces_the_punch_in(self):
        self.punch("TOP_LEFT", 2.0)
        self.punch("CENTER", 1.5)
        self.assertEqual(len(self.scene.fcurves), 4)
        self.assertEqual(len(_curve(self.scene, self.strip, "scale_x")), 4)
        rz.zoom_to(self.scene, self.strip, "CENTER", 2.0)               # a static zoom takes the keys off
        self.assertEqual(self.scene.fcurves, [])
        self.assertIsNone(self.strip.get(rp.PUNCH))

    def test_reset_framing_takes_every_key_and_the_tag_away(self):
        self.punch()
        rf.apply_reframe(self.scene, self.strip, "RESET_FRAMING")
        self.assertEqual(self.scene.fcurves, [])
        self.assertIsNone(self.strip.get(rp.PUNCH))
        self.assertAlmostEqual(self.strip.transform.offset_x, 0.0, places=6)
        self.assertAlmostEqual(rf.plain_framing_of(self.scene, self.strip)["Zoom"], 1.0, places=4)
        self.assertGreater(self.strip.crop.min_x, 0)                    # the plain Centre Crop is back

    def test_a_nudge_on_a_punch_in_is_refused_with_the_way_out(self):
        self.punch()
        with self.assertRaises(rf.ReframeError) as cm:
            rf.apply_reframe(self.scene, self.strip, "ZOOM_IN")
        self.assertIn("Reset Framing", str(cm.exception))

    def test_a_short_ease_is_at_most_a_third_of_the_shot(self):
        self.assertEqual(rz.punch_frames(1, 31, 24.0, 5.0), [1, 10, 21, 30])
        self.assertEqual(rz.punch_frames(1, 121, 24.0, 0.5), [1, 13, 108, 120])
        self.assertIsNone(rz.punch_frames(1, 6, 24.0, 0.5))
        self.assertEqual(rz.punch_frames(1, 7, 24.0, 0.01), [1, 2, 5, 6])

    def test_a_too_short_strip_is_refused(self):
        strip = _strip(self.scene, left=200, right=204, name="Blip")
        with self.assertRaises(rf.ReframeError) as cm:
            rz.zoom_to(self.scene, strip, "CENTER", 2.0, True)
        self.assertIn("too short", str(cm.exception))
        self.assertEqual(self.scene.fcurves, [])


@unittest.skipIf(rf is None, "reframe.py could not be imported without Blender")
class TestRefusals(unittest.TestCase):

    def setUp(self):
        _split_free(self)
        self.patch = mock.patch.object(rz._layouts, "is_split_part", lambda strip: bool(strip.get("is_split")))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.scene = R._Scene((1080, 1920))

    def test_a_followed_strip_is_refused(self):
        strip = _strip(self.scene)
        rf.reframe_strip(self.scene, strip, "CROP")
        rf.apply_follow(self.scene, {"Shot": [(1, 0.3), (25, 0.5), (49, 0.8)]})
        with self.assertRaises(rf.ReframeError) as cm:
            rz.zoom_to(self.scene, strip, "CENTER", 2.0)
        self.assertIn("Reset Framing", str(cm.exception))

    def test_a_blur_fill_strip_is_refused_and_a_plain_modifier_converted(self):
        strip = _strip(self.scene)
        R._with_modifier(strip, blur=True)
        with self.assertRaises(rf.ReframeError):
            rz.zoom_to(self.scene, strip, "CENTER", 2.0)
        other = _strip(self.scene, name="Other")
        R._with_modifier(other)
        self.assertEqual(rz.zoom_to(self.scene, other, "TOP_LEFT", 2.0), "zoomed")
        self.assertEqual(other.modifiers, [])

    def test_a_part_of_a_split_is_refused(self):
        strip = _strip(self.scene)
        strip["is_split"] = True
        with self.assertRaises(rf.ReframeError):
            rz.zoom_to(self.scene, strip, "CENTER", 2.0)

    def test_zoom_strips_says_what_it_did_and_what_it_left(self):
        a, b = _strip(self.scene, name="A"), _strip(self.scene, name="B")
        b["is_split"] = True
        out = rz.zoom_strips(self.scene, [a, b], "TOP_LEFT", 2.0)
        self.assertEqual(out["done"], ["A"])
        self.assertEqual([s["strip"] for s in out["skipped"]], ["B"])
        self.assertTrue(out["message"].startswith("Zoomed 1 strip to Top Left at 2x"))
        self.assertIn("left alone", out["message"])
        out = rz.zoom_strips(self.scene, [a], "CENTER", 1.5, True)
        self.assertEqual(out["message"], "Zoomed 1 strip to Centre at 1.5x as a punch-in (eases in, holds, eases out)")
        self.assertEqual(rz.zoom_strips(self.scene, [], "CENTER")["message"], "Nothing to zoom")


class TestWhereItIs(unittest.TestCase):

    def test_the_menu_entries_all_have_icons_and_only_the_dialog_one_has_dots(self):
        src = (_ADDON / "reframe_zoom.py").read_text(encoding="utf-8")
        menu = src[src.index("class BFACW_MT_reframe_zoom_to"):src.index("_CLASSES =")]
        self.assertEqual(len(re.findall(r"layout\.operator\(", menu)), 2)
        self.assertEqual(len(re.findall(r"icon=icon\(", menu)), 2)
        self.assertEqual(menu.count("Zoom To..."), 1)

    def test_every_region_and_zoom_has_a_tooltip_and_a_blender_icon(self):
        icons = (_ROOT / "tests" / "data" / "blender_icons.txt").read_text(encoding="utf-8").split()
        for ident, label, tip, icon in rz.REGIONS + rz.ZOOMS:
            self.assertTrue(label and tip and icon in icons, (ident, icon))

    def test_the_reframe_menu_lists_zoom_to_before_reset_framing(self):
        src = (_ADDON / "reframe.py").read_text(encoding="utf-8")
        menu = src[src.index("class BFACW_MT_reframe(Menu)"):src.index("_CLASSES = (BFACW_OT_make_vertical")]
        self.assertLess(menu.index("BFACW_MT_reframe_zoom_to"), menu.index("Reset Framing"))

    def test_registered_with_reframe(self):
        src = (_ADDON / "reframe.py").read_text(encoding="utf-8")
        self.assertIn("reframe_zoom.register()", src)
        self.assertIn("reframe_zoom.unregister()", src)

    def test_the_tool_takes_the_arguments_and_the_docs_say_so(self):
        tool = (_TOOLS / "reframe_strips.py").read_text(encoding="utf-8")
        for name in ("zoom_to", "zoom", "punch_in"):
            self.assertIn("            {:s}:".format(name), tool)
        docs = (_ADDON / "skills" / "mcp_tools.md").read_text(encoding="utf-8")
        self.assertIn("zoom_to", docs)
        self.assertIn("reframe_zoom_to", docs)


if __name__ == "__main__":
    unittest.main()
