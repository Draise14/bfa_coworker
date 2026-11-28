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


class TestWiring(unittest.TestCase):
    """The Blender side is checked live; here that the code is wired the way the issue says."""

    @classmethod
    def setUpClass(cls):
        cls.text = (_ADDON / "reframe.py").read_text(encoding="utf-8")

    def _body(self, name, end):
        start = self.text.index("def " + name)
        return self.text[start:self.text.index(end, start)]

    def test_centre_crop_adds_no_modifier(self):
        body = self._body("reframe_strip", "def _copy_scene")
        crop = body[:body.index("ng = ensure_group()")]
        self.assertIn("build_plain", crop)
        self.assertNotIn("modifiers.new", crop)
        self.assertNotIn("ensure_group", crop)

    def test_blur_fill_still_uses_the_modifier(self):
        body = self._body("reframe_strip", "def _copy_scene")
        self.assertIn("modifiers.new(MOD_NAME, type='COMPOSITOR')", body.split("ng = ensure_group()")[1])

    def test_old_modifier_strips_are_converted_unless_blur_or_keyed(self):
        body = self._body("to_plain", "def reframe_strip")
        self.assertIn('get_inputs(mod).get("Blur Fill")', body)
        self.assertIn("_key_free", body)
        self.assertIn("modifiers.remove(mod)", body)

    def test_make_vertical_reports_converted(self):
        make = self._body("make_vertical", "# -- Nudge / mode / reset")
        self.assertIn('"converted"', make)

    def test_follow_subject_reads_both_forms(self):
        self.assertIn("plain_marker(s) is not None", self._body("_follow_targets", "def start_follow"))
        self.assertIn("_follow_plain", self._body("apply_follow", "def _follow_poll"))

    def test_the_reframe_menu_works_on_a_plain_strip(self):
        body = self._body("apply_reframe", "# -- Follow subject")
        self.assertIn("_nudge_plain", body)
        self.assertIn("plain_marker(strip) is None", body)


if __name__ == "__main__":
    unittest.main()
