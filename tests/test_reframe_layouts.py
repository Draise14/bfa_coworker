# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Reframe's layouts (#606), bpy-free.

1. The geometry (``wand/reframe_layout``): every layout for 16:9 into 9:16, 4:3 into 9:16, 16:9 into 1:1
   and at odd sizes: the parts tile the frame exactly (no gap, no overlap), each strip's visible area
   covers its rectangle, the seam sits on the rectangle's edge, nothing is stretched.
2. The replace logic (``wand/reframe_apply``) on a stand-in Sequencer: every layout to every other one,
   through all of them and back, nothing selected, a partner selected, captions untouched, a strip
   reference never used after it was freed, Swap, Reset Framing, the partner following a cut.
3. The tool (params, result, the lists it is in), the busy-scene gate, the layout tooltips, the icon
   files, Auto Captions not captioning a split twice.

Run with::

    python -m unittest tests.test_reframe_layouts -v
"""

__all__ = ()

import importlib
import importlib.util
import itertools
import os
import re
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"
_TOOLS = _REPO / "mcp" / "blmcp" / "tools"
_PKG = "bfacw_layouts_pkg"


def _import(name: str):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + name)


rl = _import("wand.reframe_layout")
ra = _import("wand.reframe_apply")

SOURCES = ((1920, 1080), (1440, 1080), (1000, 1000), (3840, 1600), (1080, 1920))
FRAMES = ((1080, 1920), (1080, 1080), (720, 1280), (1920, 1080), (1081, 1919), (541, 961))
LAYOUT_IDS = rl.buildable()
SPLITS = ("split_half", "split_top", "split_bottom")


class TestLayoutTable(unittest.TestCase):

    def test_ids_and_order(self):
        self.assertEqual([item.id for item in rl.LAYOUTS],
                         ["full", "full_blur", "full_tracked", "split_half", "split_top", "split_bottom",
                          "track_all"])
        self.assertEqual(rl.buildable(), ("full", "full_blur", "full_tracked", "split_half", "split_top",
                                          "split_bottom"))
        self.assertFalse(rl.layout("track_all").ready)

    def test_tooltips_say_what_they_do_and_that_no_agent_is_needed(self):
        for item in rl.LAYOUTS:
            self.assertGreater(len(item.description), 40, item.id)
            self.assertTrue(item.short, item.id)
        for item in rl.LAYOUTS:
            if item.id in ("full_tracked", "track_all"):
                continue
            self.assertTrue(item.description.endswith("\nWorks without the agent started."), item.id)
        tracked = rl.layout("full_tracked").description
        self.assertTrue(tracked.endswith("\nWorks without the agent started, except the tracking, which needs a "
                                          "connected vision-capable model."))
        self.assertTrue(rl.layout("track_all").description.endswith("\nNeeds subject finding (coming)."))

    def test_every_layout_has_its_icon(self):
        icons = _import("wand.icons")
        self.assertEqual(sorted(item.icon for item in rl.LAYOUTS), sorted(icons.LAYOUT_ICON_NAMES))
        for name in icons.LAYOUT_ICON_NAMES:
            for theme in icons.THEMES:
                for size in icons.SIZES:
                    path = icons.png_path(name, theme, size)
                    self.assertTrue(os.path.isfile(path), path)
                    data = Path(path).read_bytes()
                    self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                    self.assertEqual(int.from_bytes(data[16:20], "big"), size)
                    self.assertEqual(data[25], 6, "RGBA")   # full colour with alpha
            # one palette for both themes: the files of the two themes are the same picture
            self.assertEqual(Path(icons.png_path(name, "dark", 64)).read_bytes(),
                             Path(icons.png_path(name, "light", 64)).read_bytes())


class TestGeometry(unittest.TestCase):

    def test_rects_tile_the_frame_exactly(self):
        for layout_id in LAYOUT_IDS:
            for fw, fh in FRAMES:
                rects = rl.part_rects(layout_id, fw, fh)
                self.assertEqual(len(rects), len(rl.layout(layout_id).parts), layout_id)
                top = fh
                for x0, y0, x1, y1 in rects:         # top first: each starts where the one above ended
                    self.assertEqual((x0, x1, y1), (0, fw, top), (layout_id, fw, fh))
                    self.assertLess(y0, y1)
                    top = y0
                self.assertEqual(top, 0, (layout_id, fw, fh))
                self.assertEqual(sum(r[3] - r[1] for r in rects), fh)

    def test_shares(self):
        self.assertEqual([r[3] - r[1] for r in rl.part_rects("split_half", 1080, 1920)], [960, 960])
        self.assertEqual([r[3] - r[1] for r in rl.part_rects("split_top", 1080, 1920)], [1280, 640])
        self.assertEqual([r[3] - r[1] for r in rl.part_rects("split_bottom", 1080, 1920)], [640, 1280])
        self.assertEqual([r[3] - r[1] for r in rl.part_rects("split_half", 1081, 1919)], [960, 959])
        self.assertEqual([r[3] - r[1] for r in rl.part_rects("split_top", 1080, 1080)], [720, 360])

    def test_each_part_covers_its_rectangle_with_the_seam_exact(self):
        for layout_id in LAYOUT_IDS:
            if rl.layout(layout_id).modifier == "BLUR_FILL":
                continue
            for sw, sh in SOURCES:
                for fw, fh in FRAMES:
                    plan = rl.plan(layout_id, sw, sh, fw, fh)
                    for part in plan.parts:
                        x0, y0, x1, y1 = part.rect
                        vx0, vy0, vx1, vy1 = rl.visible_rect(part, sw, sh, fw, fh)
                        where = (layout_id, (sw, sh), (fw, fh), part.index)
                        eps = 1e-6
                        self.assertLessEqual(vx0, x0 + eps, where)
                        self.assertGreaterEqual(vx1, x1 - eps, where)
                        self.assertLessEqual(vy0, y0 + eps, where)
                        self.assertGreaterEqual(vy1, y1 - eps, where)
                        if y0 > 0:                          # the seam to the part below
                            self.assertAlmostEqual(vy0, y0, 6, where)
                        if y1 < fh:                         # the seam to the part above
                            self.assertAlmostEqual(vy1, y1, 6, where)
                        # Never more than one source pixel of the picture past an edge that is not a seam.
                        self.assertLess(x0 - vx0, part.scale + eps, where)
                        self.assertLess(vx1 - x1, part.scale + eps, where)
                        # One scale for both axes (nothing stretched), crop edges whole source pixels, >= 0.
                        self.assertTrue(all(isinstance(c, int) and c >= 0 for c in part.crop), where)
                        self.assertLess(part.crop[0] + part.crop[1], sw, where)
                        self.assertLess(part.crop[2] + part.crop[3], sh, where)
                        self.assertGreater(part.scale, 0)

    def test_cover_is_the_smallest_scale_that_covers(self):
        for sw, sh in SOURCES:
            for fw, fh in FRAMES:
                for part in rl.plan("split_half", sw, sh, fw, fh).parts:
                    x0, y0, x1, y1 = part.rect
                    rw, rh = x1 - x0, y1 - y0
                    self.assertAlmostEqual(part.scale, max(rw / sw, rh / sh), 9)
                    self.assertGreaterEqual(sw * part.scale + 1e-6, rw)
                    self.assertGreaterEqual(sh * part.scale + 1e-6, rh)

    def test_16x9_into_9x16_numbers(self):
        top, bottom = rl.plan("split_half", 1920, 1080, 1080, 1920).parts
        self.assertEqual((top.rect, bottom.rect), ((0, 960, 1080, 1920), (0, 0, 1080, 960)))
        self.assertAlmostEqual(top.scale, 960 / 1080)
        self.assertEqual(top.crop, (352, 352, 0, 0))                   # (1920 - 1080 / s) / 2 = 352.5, rounded out
        self.assertEqual(top.offset, (0.0, 480.0))
        self.assertEqual(bottom.offset, (0.0, -480.0))
        (full,) = rl.plan("full", 1920, 1080, 1080, 1920).parts
        self.assertAlmostEqual(full.scale, 1920 / 1080)
        self.assertEqual(full.crop[2:], (0, 0))
        self.assertEqual(full.offset, (0.0, 0.0))

    def test_4x3_and_square_frames(self):
        top, _bottom = rl.plan("split_half", 1440, 1080, 1080, 1920).parts
        self.assertAlmostEqual(top.scale, 960 / 1080)
        self.assertEqual(top.crop[:2], (112, 112))
        top, bottom = rl.plan("split_top", 1920, 1080, 1080, 1080).parts
        self.assertEqual((top.rect, bottom.rect), ((0, 360, 1080, 1080), (0, 0, 1080, 360)))
        self.assertAlmostEqual(top.scale, 720 / 1080)

    def test_blur_fill_fits_the_whole_picture(self):
        for sw, sh in SOURCES:
            for fw, fh in FRAMES:
                (part,) = rl.plan("full_blur", sw, sh, fw, fh).parts
                self.assertAlmostEqual(part.scale, min(fw / sw, fh / sh), 9)
                self.assertEqual(part.crop, (0, 0, 0, 0))
                self.assertLessEqual(sw * part.scale, fw + 1e-6)
                self.assertLessEqual(sh * part.scale, fh + 1e-6)

    def test_tracked_starts_as_full(self):
        self.assertEqual(rl.plan("full_tracked", 1920, 1080, 1080, 1920).parts,
                         rl.plan("full", 1920, 1080, 1080, 1920).parts)

    def test_framing_round_trips_and_keeps_the_focus_inside_the_picture(self):
        for sw, sh in SOURCES:
            for fw, fh in FRAMES:
                for layout_id in SPLITS:
                    for rect in rl.part_rects(layout_id, fw, fh):
                        for framing in (rl.DEFAULT_FRAMING, rl.Framing(0.3, 0.5, 1.0), rl.Framing(0.7, 0.4, 1.8)):
                            part = rl.part_for(0, rect, sw, sh, fw, fh, framing)
                            back = rl.framing_of(rect, part.scale, part.offset, sw, sh, fw, fh)
                            # (the seam move shifts the picture by less than one source pixel)
                            self.assertLess(abs(back.u - part.framing.u) * sw, 1.0 + 1e-6)
                            self.assertLess(abs(back.v - part.framing.v) * sh, 1.0 + 1e-6)
                            self.assertAlmostEqual(back.zoom, part.framing.zoom, 5)
                            # The visible area stays on the picture
                            vx0, vy0, vx1, vy1 = rl.visible_rect(part, sw, sh, fw, fh)
                            ox, oy = part.offset
                            left = fw / 2 + ox - sw * part.scale / 2
                            self.assertGreaterEqual(vx0, left - 1e-6)
                            self.assertLessEqual(vx1, fw / 2 + ox + sw * part.scale / 2 + 1e-6)

    def test_bad_sizes(self):
        with self.assertRaises(ValueError):
            rl.plan("split_half", 0, 1080, 1080, 1920)
        with self.assertRaises(KeyError):
            rl.plan("nine_parts", 1920, 1080, 1080, 1920)


# -- a stand-in Sequencer ------------------------------------------------------------------------

class _Freed(Exception):
    pass


class Strip:
    """A strip as the layout code reads it.  Once removed, any access raises (a freed strip crashes Blender)."""

    def __init__(self, name, kind="MOVIE", channel=1, left=1, right=76, path="clip.mp4"):
        object.__setattr__(self, "_freed", False)
        self.name, self.type, self.channel = name, kind, channel
        self.left_handle, self.right_handle = left, right
        self.content_start = float(left)
        self.lock = False
        self.retiming_keys = []
        self.select = False
        self.path = path
        self.crop = SimpleNamespace(min_x=0, max_x=0, min_y=0, max_y=0)
        self.transform = SimpleNamespace(offset_x=0.0, offset_y=0.0, scale_x=1.0, scale_y=1.0)
        self.props = {}
        self.mod = None                 # None, "CROP" or "BLUR_FILL"
        self.keyed = False

    def __getattribute__(self, key):
        if object.__getattribute__(self, "_freed") and key not in ("_freed",):
            raise _Freed("a freed strip was read: " + key)
        return object.__getattribute__(self, key)

    def get(self, key, default=None):
        return self.props.get(key, default)

    def __setitem__(self, key, value):
        self.props[key] = dict(value)

    def __delitem__(self, key):
        del self.props[key]


class Seq(list):
    """The level's strips."""


class Stand(ra.Backend):
    """The Blender side, on the stand-in."""

    def __init__(self, source=(1920, 1080), canvas=(1080, 1920), tracking="started"):
        self.source, self.canvas, self.tracking = source, canvas, tracking
        self.log = []

    def source_size(self, strip):
        return self.source

    def canvas_size(self, scene):
        return self.canvas

    def duplicate(self, strips, strip, name, channel):
        assert name not in {s.name for s in strips}, "name taken"
        assert not any(s.channel == channel and s.left_handle < strip.right_handle and strip.left_handle < s.right_handle
                       for s in strips), "channel not free"
        assert not any(s.type == "TEXT" and s.channel < channel and s.left_handle < strip.right_handle
                       and strip.left_handle < s.right_handle for s in strips), "the part would hide a caption"
        twin = Strip(name, strip.type, channel, strip.left_handle, strip.right_handle, strip.path)
        strips.append(twin)
        self.log.append(("duplicate", name))
        return twin

    def remove(self, strips, doomed):
        for strip in doomed:
            strips.remove(strip)
            object.__setattr__(strip, "_freed", True)
            self.log.append(("remove",))

    def shift_channels(self, strips, first):
        for strip in sorted((s for s in strips if s.channel >= first), key=lambda s: -s.channel):
            strip.channel += 1
        self.log.append(("shift", first))

    def align(self, strips, top, partner):
        partner.left_handle, partner.right_handle = top.left_handle, top.right_handle

    def modifier_state(self, strip):
        return None if strip.mod is None else {"blur": strip.mod == "BLUR_FILL", "keyed": strip.keyed}

    def clear_modifier(self, scene, strip):
        strip.mod, strip.keyed = None, False

    def make_full(self, scene, strip, mode):
        strip.mod, strip.keyed = mode, False
        strip.transform.scale_x = strip.transform.scale_y = 1.0

    def reset_full(self, scene, strip):
        strip.keyed = False

    def start_tracking(self, scene, strips):
        if self.tracking != "started":
            raise ra.LayoutError(self.tracking)
        for strip in strips:
            strip.keyed = True
        return "Following the subject in {:d} shot(s)".format(len(strips))


def _edit(count=1, **kw):
    """A level with *count* movie strips side by side, a sound strip, and a caption on top."""
    strips = Seq()
    for i in range(count):
        first = 1 + i * 100
        strips.append(Strip("clip" if count == 1 else "clip{:d}".format(i + 1), left=first, right=first + 75, **kw))
    strips.append(Strip("talk", "SOUND", 2, 1, 76))
    text = Strip("Caption 1", "TEXT", 5, 1, 30)
    strips.append(text)
    return strips


def _snap(strips):
    out = []
    for s in sorted(strips, key=lambda s: s.name):
        t, c = s.transform, s.crop
        out.append((s.name, s.type, s.channel, s.left_handle, s.right_handle,
                    (round(t.offset_x, 6), round(t.offset_y, 6), round(t.scale_x, 6), round(t.scale_y, 6)),
                    (c.min_x, c.max_x, c.min_y, c.max_y), s.mod, s.keyed, repr(sorted(s.props.items()))))
    return out


def _apply(strips, layout_id, names, backend=None):
    return ra.apply_layout(None, strips, layout_id, names, backend or Stand())


class TestReplace(unittest.TestCase):

    def test_each_layout_on_a_fresh_strip(self):
        for layout_id in LAYOUT_IDS:
            strips = _edit()
            out = _apply(strips, layout_id, ["clip"])
            self.assertEqual(out["status"], "ok", out)
            self.assertEqual(out["changed"], ["clip"])
            clip = strips[0]
            # the tag never says Tracked: that is read from the keys
            self.assertEqual(ra.tag_of(clip)["layout"], layout_id if layout_id != "full_tracked" else "full")
            names = sorted(s.name for s in strips)
            if layout_id in SPLITS:
                self.assertEqual(names, ["Caption 1", "clip", "clip (bottom)", "talk"])
                twin = next(s for s in strips if s.name == "clip (bottom)")
                self.assertEqual((twin.left_handle, twin.right_handle), (1, 76))
                self.assertGreater(twin.channel, clip.channel)
                self.assertEqual(ra.tag_of(twin), {"layout": layout_id, "part": 1, "group": "clip",
                                                   "partner": "clip"})
                self.assertEqual(ra.tag_of(clip)["partner"], "clip (bottom)")
                self.assertEqual(out["created"], ["clip (bottom)"])
            else:
                self.assertEqual(names, ["Caption 1", "clip", "talk"])
                self.assertEqual(clip.mod, "BLUR_FILL" if layout_id == "full_blur" else "CROP")

    def test_split_numbers_come_from_the_geometry(self):
        strips = _edit()
        _apply(strips, "split_half", ["clip"])
        top, bottom = (rl.plan("split_half", 1920, 1080, 1080, 1920).parts)
        clip, twin = strips[0], strips[-1] if strips[-1].name == "clip (bottom)" else next(
            s for s in strips if s.name == "clip (bottom)")
        self.assertEqual((clip.transform.offset_x, clip.transform.offset_y), top.offset)
        self.assertEqual(clip.transform.scale_x, top.scale)
        self.assertEqual((clip.crop.min_x, clip.crop.max_x, clip.crop.min_y, clip.crop.max_y), top.crop)
        self.assertEqual((twin.transform.offset_x, twin.transform.offset_y), bottom.offset)

    def test_every_layout_to_every_other_leaves_the_same_as_doing_it_directly(self):
        for start, end in itertools.product((None,) + LAYOUT_IDS, LAYOUT_IDS):
            direct = _edit()
            _apply(direct, end, ["clip"])
            strips = _edit()
            if start is not None:
                _apply(strips, start, ["clip"])
            out = _apply(strips, end, ["clip"])
            self.assertEqual(out["status"], "ok")
            self.assertEqual(_snap(strips), _snap(direct), (start, end))

    def test_through_every_layout_and_back(self):
        for order in (LAYOUT_IDS, LAYOUT_IDS[::-1], LAYOUT_IDS + LAYOUT_IDS[::-1][:-1]):
            for end in LAYOUT_IDS:
                direct = _edit(count=2)
                _apply(direct, end, ["clip1", "clip2"])
                strips = _edit(count=2)
                backend = Stand()
                for layout_id in order:
                    _apply(strips, layout_id, ["clip1", "clip2"], backend)
                _apply(strips, end, ["clip1", "clip2"], backend)
                self.assertEqual(_snap(strips), _snap(direct), (order, end))

    def test_from_make_verticals_modifier(self):
        for mod, expect in (("CROP", "full"), ("BLUR_FILL", "full_blur")):
            strips = _edit()
            strips[0].mod = mod
            strips[0].transform.scale_x = 0.5625           # a leftover from the strip's own fitting
            self.assertEqual(ra.current_layout(strips[0], Stand()), expect)
            _apply(strips, "split_top", ["clip"])
            direct = _edit()
            _apply(direct, "split_top", ["clip"])
            self.assertEqual(_snap(strips), _snap(direct))

    def test_already_in_the_layout_changes_nothing(self):
        strips = _edit()
        _apply(strips, "split_half", ["clip"])
        before = _snap(strips)
        backend = Stand()
        out = _apply(strips, "split_half", ["clip"], backend)
        self.assertEqual((out["status"], out["changed"], out["unchanged"]), ("ok", [], ["clip"]))
        self.assertIn("already", out["message"])
        self.assertEqual(_snap(strips), before)
        self.assertEqual(backend.log, [])

    def test_nothing_usable_changes_nothing(self):
        strips = _edit()
        before = _snap(strips)
        for names in ([], ["talk"], ["Caption 1"], ["nope"]):
            out = _apply(strips, "split_half", names)
            self.assertEqual(out["status"], "nothing", names)
            self.assertIn("Nothing to reframe", out["message"])
            self.assertEqual(_snap(strips), before)

    def test_unknown_and_unavailable_layouts(self):
        strips = _edit()
        before = _snap(strips)
        out = _apply(strips, "thirds", ["clip"])
        self.assertEqual(out["status"], "error")
        self.assertIn("split_half", out["message"])
        out = _apply(strips, "track_all", ["clip"])
        self.assertEqual(out["status"], "error")
        self.assertIn("needs subject finding (coming)", out["message"])
        self.assertEqual(_snap(strips), before)

    def test_locked_and_retimed_strips_are_skipped_and_named(self):
        strips = _edit(count=3)
        strips[0].lock = True
        strips[1].retiming_keys = [object()]
        out = _apply(strips, "split_half", ["clip1", "clip2", "clip3"])
        self.assertEqual(out["changed"], ["clip3"])
        self.assertEqual({s["strip"] for s in out["skipped"]}, {"clip1", "clip2"})
        self.assertIsNone(ra.tag_of(strips[0]))
        self.assertIn("retimed", out["message"])

    def test_captions_sound_and_other_strips_are_never_touched(self):
        strips = _edit(count=2)
        text, sound = strips[-1], strips[-2]
        before = (_snap([text]), _snap([sound]))
        for layout_id in LAYOUT_IDS:
            _apply(strips, layout_id, ["clip1", "clip2", "talk", "Caption 1"])
        self.assertEqual((_snap([text]), _snap([sound])), before)
        self.assertEqual(sum(s.type == "SOUND" for s in strips), 1, "the audio is not duplicated")

    def test_a_partner_stands_for_its_group(self):
        strips = _edit()
        _apply(strips, "split_half", ["clip"])
        twin = next(s for s in strips if s.name == "clip (bottom)")
        out = _apply(strips, "split_top", ["clip (bottom)"])          # only the bottom part is asked for
        self.assertEqual(out["changed"], ["clip"])
        self.assertEqual(out["removed"], ["clip (bottom)"])
        self.assertEqual(sorted(s.name for s in strips), ["Caption 1", "clip", "clip (bottom)", "talk"])
        self.assertEqual(ra.tag_of(strips[0])["layout"], "split_top")
        self.assertIsNot(next(s for s in strips if s.name == "clip (bottom)"), twin)
        out = _apply(strips, "full", ["clip", "clip (bottom)"])      # both parts asked for: one group
        self.assertEqual(out["changed"], ["clip"])
        self.assertEqual(sorted(s.name for s in strips), ["Caption 1", "clip", "talk"])

    def test_only_the_asked_strips_change(self):
        strips = _edit(count=3)
        _apply(strips, "split_half", ["clip1", "clip2", "clip3"])
        before = {s.name: _snap([s]) for s in strips if s.name in ("clip3", "clip3 (bottom)")}
        _apply(strips, "full_blur", ["clip1"])
        self.assertEqual({s.name: _snap([s]) for s in strips if s.name in ("clip3", "clip3 (bottom)")}, before)
        self.assertEqual(ra.current_layout(next(s for s in strips if s.name == "clip1"), Stand()), "full_blur")
        self.assertFalse(any(s.name == "clip1 (bottom)" for s in strips))

    def test_the_partner_goes_on_the_next_free_channel(self):
        strips = _edit()
        strips.append(Strip("other", "MOVIE", 3, 1, 76))               # 2 is the sound's, 3 is taken as well
        _apply(strips, "split_half", ["clip"])
        twin = next(s for s in strips if s.name == "clip (bottom)")
        self.assertEqual(twin.channel, 4)
        strips = _edit()
        strips.append(Strip("later", "MOVIE", 3, 200, 260))            # channel 3 is free over these frames
        _apply(strips, "split_half", ["clip"])
        self.assertEqual(next(s for s in strips if s.name == "clip (bottom)").channel, 3)

    def test_the_name_of_the_partner_is_free_and_short_enough(self):
        strips = _edit()
        strips.append(Strip("clip (bottom)", "MOVIE", 9, 500, 600))    # the name is taken by another strip
        _apply(strips, "split_half", ["clip"])
        self.assertIn("clip (bottom).001", [s.name for s in strips])
        long_name = "x" * 63
        strips = Seq([Strip(long_name)])
        _apply(strips, "split_half", [long_name])
        self.assertTrue(all(len(s.name) <= 63 for s in strips))

    def test_tracked_and_the_tag_follows_what_started(self):
        strips = _edit()
        out = _apply(strips, "full_tracked", ["clip"], Stand())
        self.assertEqual(ra.current_layout(strips[0], Stand()), "full_tracked")
        self.assertTrue(strips[0].keyed)
        self.assertIn("Following the subject", out["message"])
        # Away from it: the keys go with the layout.
        _apply(strips, "full", ["clip"])
        self.assertFalse(strips[0].keyed)
        self.assertEqual(ra.tag_of(strips[0])["layout"], "full")
        # No vision model: the layout is made, the tag stays Full Frame, and it says why.
        strips = _edit()
        out = _apply(strips, "full_tracked", ["clip"], Stand(tracking="needs a connected vision model"))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(ra.tag_of(strips[0])["layout"], "full")
        self.assertIn("Tracking did not start (needs a connected vision model)", out["message"])
        self.assertEqual(strips[0].mod, "CROP")

    def test_no_freed_strip_is_read(self):
        # Stand.remove makes any later read raise; the whole run above and this one must never trip it.
        strips = _edit(count=3)
        backend = Stand()
        for layout_id in SPLITS * 2 + ("full_blur", "split_half", "full"):
            _apply(strips, layout_id, ["clip1", "clip2", "clip3"], backend)
        self.assertGreater(sum(1 for entry in backend.log if entry[0] == "remove"), 3)

    def test_one_undo_step_is_the_callers(self):
        steps = []
        strips = _edit(count=2)
        ra.apply_layout(None, strips, "split_half", ["clip1", "clip2"], Stand(), on_step=lambda: steps.append(1))
        self.assertEqual(len(steps), 2)           # called per group; the operator pushes one undo for the click


class TestSwapAndReset(unittest.TestCase):

    def test_swap_exchanges_the_framings(self):
        strips = _edit()
        _apply(strips, "split_top", ["clip"])
        top, bottom = strips[0], next(s for s in strips if s.name == "clip (bottom)")
        # Move the top part's picture: its framing shows the left part of the picture.
        rect_top = rl.part_rects("split_top", 1080, 1920)[0]
        moved = rl.part_for(0, rect_top, 1920, 1080, 1080, 1920, rl.Framing(0.4, 0.5, 1.4))
        top.transform.scale_x = top.transform.scale_y = moved.scale
        top.transform.offset_x, top.transform.offset_y = moved.offset
        out = ra.swap_parts(None, strips, ["clip"], Stand())
        self.assertEqual((out["status"], out["changed"]), ("ok", ["clip"]))
        rect_bottom = rl.part_rects("split_top", 1080, 1920)[1]
        shown_bottom = rl.framing_of(rect_bottom, bottom.transform.scale_x,
                                     (bottom.transform.offset_x, bottom.transform.offset_y), 1920, 1080, 1080, 1920)
        self.assertAlmostEqual(shown_bottom.u, 0.4, 3)
        self.assertAlmostEqual(shown_bottom.zoom, 1.4, 4)
        shown_top = rl.framing_of(rect_top, top.transform.scale_x, (top.transform.offset_x, top.transform.offset_y),
                                  1920, 1080, 1080, 1920)
        self.assertAlmostEqual(shown_top.u, 0.5, 4)
        # The seam still sits exactly on the rectangles' edges after a swap.
        for strip, index in ((top, 0), (bottom, 1)):
            part = rl.Part(index, rl.part_rects("split_top", 1080, 1920)[index], strip.transform.scale_x,
                           (strip.transform.offset_x, strip.transform.offset_y),
                           (strip.crop.min_x, strip.crop.max_x, strip.crop.min_y, strip.crop.max_y), rl.DEFAULT_FRAMING)
            vis = rl.visible_rect(part, 1920, 1080, 1080, 1920)
            self.assertAlmostEqual(vis[1] if index == 0 else vis[3], part.rect[1] if index == 0 else part.rect[3], 6)
        # Swapping twice is the start again.
        ra.swap_parts(None, strips, ["clip (bottom)"], Stand())          # the partner stands for the group
        self.assertAlmostEqual(top.transform.scale_x, moved.scale, 6)
        self.assertAlmostEqual(top.transform.offset_x, moved.offset[0], 2)
        self.assertAlmostEqual(top.transform.offset_y, moved.offset[1], 2)

    def test_swap_of_default_halves_changes_nothing_visible(self):
        strips = _edit()
        _apply(strips, "split_half", ["clip"])
        before = _snap(strips)
        ra.swap_parts(None, strips, ["clip"], Stand())
        after = _snap(strips)
        for a, b in zip(before, after):       # equal heights, same default framing: the same numbers
            self.assertEqual(a[5][2:], b[5][2:])
            self.assertEqual(a[6], b[6])

    def test_swap_needs_a_split(self):
        strips = _edit()
        before = _snap(strips)
        for layout_id in ("full", None):
            if layout_id:
                _apply(strips, layout_id, ["clip"])
            before = _snap(strips)
            out = ra.swap_parts(None, strips, ["clip"], Stand())
            self.assertEqual(out["status"], "nothing")
            self.assertIn("select the strips of a split", out["message"])
            self.assertEqual(_snap(strips), before)

    def test_reset_puts_a_part_back_to_the_default_crop(self):
        strips = _edit()
        _apply(strips, "split_bottom", ["clip"])
        before = _snap(strips)
        twin = next(s for s in strips if s.name == "clip (bottom)")
        twin.transform.offset_x += 80
        twin.transform.scale_x = twin.transform.scale_y = twin.transform.scale_x * 1.3
        twin.crop.min_x += 40
        strips[0].transform.offset_x = 33                    # the top part also moved, but is not asked for
        out = ra.reset_framing(None, strips, ["clip (bottom)"], Stand())
        self.assertEqual((out["status"], out["changed"]), ("ok", ["clip (bottom)"]))
        now = _snap(strips)
        self.assertEqual([r for r in now if r[0] == "clip (bottom)"], [r for r in before if r[0] == "clip (bottom)"])
        self.assertEqual(strips[0].transform.offset_x, 33)
        ra.reset_framing(None, strips, ["clip", "clip (bottom)"], Stand())
        self.assertEqual(_snap(strips), before)

    def test_reset_of_full_frame_and_tracked(self):
        strips = _edit()
        backend = Stand()
        _apply(strips, "full_tracked", ["clip"], backend)
        out = ra.reset_framing(None, strips, ["clip"], backend)
        self.assertEqual(out["status"], "ok")
        self.assertFalse(strips[0].keyed)
        self.assertEqual(ra.tag_of(strips[0])["layout"], "full")

    def test_reset_with_no_layout(self):
        strips = _edit()
        out = ra.reset_framing(None, strips, ["clip"], Stand())
        self.assertEqual(out["status"], "nothing")
        self.assertIn("select strips that have a layout", out["message"])


def _cut(strips, frame):
    """What Remove Silences' cut does to every strip at the level: the strip is split at *frame*, the
    second piece (a copy, tag and all) is named ``<name>.001``, and the pieces after it move left."""
    for strip in list(strips):
        if strip.left_handle < frame < strip.right_handle:
            piece = Strip(strip.name + ".001", strip.type, strip.channel, frame, strip.right_handle, strip.path)
            piece.transform = SimpleNamespace(**vars(strip.transform))
            piece.crop = SimpleNamespace(**vars(strip.crop))
            piece.props = {k: dict(v) for k, v in strip.props.items()}
            piece.mod = strip.mod
            strip.right_handle = frame
            strips.append(piece)


class TestCuts(unittest.TestCase):

    def test_a_cut_cuts_both_parts_alike_and_each_piece_finds_its_partner(self):
        strips = _edit()
        _apply(strips, "split_half", ["clip"])
        _cut(strips, 40)
        pieces = sorted(s.name for s in strips if s.type == "MOVIE")
        self.assertEqual(pieces, ["clip", "clip (bottom)", "clip (bottom).001", "clip.001"])
        for strip in strips:
            if strip.type != "MOVIE":
                continue
            partner = ra.find_partner(strips, strip)
            self.assertIsNotNone(partner, strip.name)
            self.assertEqual((partner.left_handle, partner.right_handle), (strip.left_handle, strip.right_handle))
            self.assertNotEqual(ra.tag_of(partner)["part"], ra.tag_of(strip)["part"])
        self.assertEqual(ra.find_partner(strips, next(s for s in strips if s.name == "clip.001")).name,
                         "clip (bottom).001")           # not by the old name the tag carries
        self.assertEqual(ra.find_partner(strips, next(s for s in strips if s.name == "clip")).name, "clip (bottom)")

    def test_a_new_layout_after_a_cut_changes_that_piece_and_its_partner_only(self):
        strips = _edit()
        _apply(strips, "split_half", ["clip"])
        _cut(strips, 40)
        out = _apply(strips, "full_blur", ["clip.001"])
        self.assertEqual((out["changed"], out["removed"]), (["clip.001"], ["clip (bottom).001"]))
        self.assertEqual(sorted(s.name for s in strips if s.type == "MOVIE"), ["clip", "clip (bottom)", "clip.001"])
        self.assertEqual(ra.tag_of(next(s for s in strips if s.name == "clip"))["layout"], "split_half")
        # and Swap / Reset still find the first pair
        self.assertEqual(ra.swap_parts(None, strips, ["clip"], Stand())["status"], "ok")
        self.assertEqual(ra.swap_parts(None, strips, ["clip.001"], Stand())["status"], "nothing")

    def test_a_trimmed_part_is_still_found_and_nothing_is_left_behind(self):
        strips = _edit()
        _apply(strips, "split_half", ["clip"])
        twin = next(s for s in strips if s.name == "clip (bottom)")
        twin.right_handle = 70                                  # the artist trimmed the bottom part by 6 frames
        self.assertIs(ra.find_partner(strips, strips[0]), twin)
        self.assertIs(ra.find_partner(strips, twin), strips[0])
        out = _apply(strips, "full", ["clip"])
        self.assertEqual(out["removed"], ["clip (bottom)"])
        self.assertEqual(sorted(s.name for s in strips), ["Caption 1", "clip", "talk"])

    def test_a_dragged_part_is_found_by_the_stored_name(self):
        strips = _edit()
        _apply(strips, "split_half", ["clip"])
        twin = next(s for s in strips if s.name == "clip (bottom)")
        twin.left_handle, twin.right_handle = 300, 375          # dragged far away: no frame in common
        self.assertIs(ra.find_partner(strips, strips[0]), twin)
        out = _apply(strips, "split_top", ["clip"])
        self.assertEqual((out["status"], out["removed"]), ("ok", ["clip (bottom)"]))
        new = next(s for s in strips if s.name == "clip (bottom)")
        self.assertEqual((new.left_handle, new.right_handle), (1, 76))
        self.assertEqual(sum(1 for s in strips if s.name.startswith("clip")), 2)

    def test_a_pair_is_not_stolen_by_another_piece(self):
        strips = _edit()
        _apply(strips, "split_half", ["clip"])
        _cut(strips, 40)
        piece = next(s for s in strips if s.name == "clip (bottom).001")
        strips.remove(piece)                                    # that piece's partner is gone for good
        lone = next(s for s in strips if s.name == "clip.001")
        self.assertIsNone(ra.find_partner(strips, lone), "clip (bottom) belongs to clip, exactly")
        self.assertEqual(ra.find_partner(strips, next(s for s in strips if s.name == "clip")).name, "clip (bottom)")

    def test_swap_puts_a_moved_partner_back_in_step_first(self):
        strips = _edit()
        _apply(strips, "split_half", ["clip"])
        twin = next(s for s in strips if s.name == "clip (bottom)")
        twin.right_handle = 70
        out = ra.swap_parts(None, strips, ["clip"], Stand())
        self.assertEqual(out["status"], "ok")
        self.assertEqual((twin.left_handle, twin.right_handle), (1, 76))

    def test_back_to_full_removes_every_other_part_over_the_clip_and_no_other_piece(self):
        strips = _edit()
        _apply(strips, "split_half", ["clip"])
        _cut(strips, 40)
        extra = Strip("stray", "MOVIE", 7, 5, 30)
        extra.props["coworker_layout"] = {"layout": "split_half", "part": 1, "group": "clip", "partner": "clip"}
        strips.append(extra)                                    # a leftover second part over the first piece
        out = _apply(strips, "full", ["clip"])
        self.assertIn("stray", out["removed"])
        self.assertIn("clip (bottom)", out["removed"])
        self.assertTrue(any(s.name == "clip (bottom).001" for s in strips), "the other piece's pair stays")
        self.assertTrue(any(s.name == "clip.001" for s in strips))


class TestChannels(unittest.TestCase):

    def _check_below_text(self, strips):
        twin = next(s for s in strips if s.name == "clip (bottom)")
        for s in strips:
            if s.type == "TEXT" and s.left_handle < twin.right_handle and twin.left_handle < s.right_handle:
                self.assertLess(twin.channel, s.channel, s.name)

    def test_a_caption_right_above_the_picture_stays_on_top(self):
        strips = Seq([Strip("clip", channel=1), Strip("talk", "SOUND", 2), Strip("Caption 1", "TEXT", 3, 1, 40)])
        _apply(strips, "split_half", ["clip"])
        self._check_below_text(strips)
        text = next(s for s in strips if s.name == "Caption 1")
        self.assertEqual(text.channel, 4, "the captions moved up one, with everything above the picture")
        self.assertEqual(next(s for s in strips if s.name == "talk").channel, 3)
        self.assertEqual(next(s for s in strips if s.name == "clip (bottom)").channel, 2)

    def test_a_word_motion_block_above_the_caption(self):
        strips = Seq([Strip("clip", channel=1), Strip("talk", "SOUND", 2)])
        strips += [Strip("Caption 1", "TEXT", 3, 1, 40), Strip("w1", "TEXT", 4, 1, 10), Strip("w2", "TEXT", 5, 10, 20),
                   Strip("w3", "TEXT", 6, 20, 30)]
        _apply(strips, "split_bottom", ["clip"])
        self._check_below_text(strips)
        self.assertEqual(sorted(s.channel for s in strips if s.type == "TEXT"), [4, 5, 6, 7], "the block kept its order")
        self.assertEqual(next(s for s in strips if s.name == "clip (bottom)").channel, 2)

    def test_a_free_channel_below_the_text_is_used_before_anything_moves(self):
        strips = Seq([Strip("clip", channel=1), Strip("Caption 1", "TEXT", 4, 1, 40)])
        backend = Stand()
        _apply(strips, "split_half", ["clip"], backend)
        self.assertEqual(next(s for s in strips if s.name == "clip (bottom)").channel, 2)
        self.assertEqual(next(s for s in strips if s.name == "Caption 1").channel, 4)
        self.assertNotIn("shift", [e[0] for e in backend.log])

    def test_a_text_on_a_later_stretch_does_not_matter(self):
        strips = Seq([Strip("clip", channel=1), Strip("Caption 9", "TEXT", 2, 200, 240)])
        _apply(strips, "split_half", ["clip"])
        self.assertEqual(next(s for s in strips if s.name == "clip (bottom)").channel, 2)

    def test_the_picture_above_the_captions_gets_a_channel_below_them(self):
        strips = Seq([Strip("Caption 1", "TEXT", 2, 1, 40), Strip("clip", channel=3), Strip("a", "SOUND", 1, 1, 76)])
        _apply(strips, "split_half", ["clip"])
        self._check_below_text(strips)

    def test_no_room_at_all_changes_nothing(self):
        strips = Seq([Strip("clip", channel=1), Strip("Caption 1", "TEXT", 2, 1, 40)] +
                     [Strip("s{:d}".format(c), "MOVIE", c, 1, 76) for c in range(3, 129)])
        before = _snap(strips)
        out = _apply(strips, "split_half", ["clip"])
        self.assertEqual(out["status"], "error")
        self.assertIn("no room", out["message"])
        self.assertEqual(_snap(strips), before)

    def test_free_channel_never_returns_a_taken_one(self):
        strips = Seq([Strip("clip", channel=1)] + [Strip("a{:d}".format(c), "MOVIE", c, 1, 76) for c in range(2, 129)])
        with self.assertRaises(ra.LayoutError):
            ra.free_channel(strips, strips[0])
        strips = Seq([Strip("clip", channel=128)])
        channel, shift = ra.plan_channel(strips, strips[0])
        self.assertEqual((channel, shift), (127, False))

    def test_a_locked_strip_in_the_way_refuses(self):
        strips = Seq([Strip("clip", channel=1), Strip("Caption 1", "TEXT", 2, 1, 40)])
        strips[1].lock = True
        before = _snap(strips)
        out = _apply(strips, "split_half", ["clip"])
        self.assertEqual(out["status"], "error")
        self.assertIn("locked", out["message"])
        self.assertEqual(_snap(strips), before)


class TestOtherEntries(unittest.TestCase):
    """Make Vertical again and the old Reframe entries leave a split's parts alone."""

    def test_split_parts_are_recognised(self):
        strips = _edit()
        self.assertFalse(ra.is_split_part(strips[0]))
        _apply(strips, "split_half", ["clip"])
        self.assertTrue(ra.is_split_part(strips[0]))
        self.assertTrue(ra.is_split_part(next(s for s in strips if s.name == "clip (bottom)")))
        _apply(strips, "full", ["clip"])
        self.assertFalse(ra.is_split_part(strips[0]))

    def test_every_modifier_entry_checks_for_a_split(self):
        text = (_ADDON / "reframe.py").read_text(encoding="utf-8")
        for name in ("def reframe_strip", "def apply_reframe"):
            body = text[text.index(name):]
            self.assertIn("_layouts.is_split_part(strip)", body[:900], name)
        make = text[text.index("def make_vertical"):text.index("# -- Nudge / mode / reset")]
        self.assertIn("left_alone", make)
        self.assertIn("_layouts.is_split_part(s)", make)
        operator = text[text.index("class BFACW_OT_reframe(Operator)"):text.index("class BFACW_OT_reframe_follow_subject")]
        self.assertIn("is_split_part", operator)
        self.assertIn("part of a split", operator)

    def test_the_tag_cleared_before_the_modifier_is_made(self):
        # reframe_strip refuses a tagged part, so a layout change clears the tag first.
        strips = _edit()
        _apply(strips, "split_half", ["clip"])
        seen = []

        class Checking(Stand):
            def make_full(self, scene, strip, mode):
                seen.append(ra.tag_of(strip))
                super().make_full(scene, strip, mode)
        _apply(strips, "full", ["clip"], Checking())
        self.assertEqual(seen, [None])


class TestReadAndCanvas(unittest.TestCase):

    def test_layouts_of(self):
        strips = _edit(count=2)
        _apply(strips, "split_top", ["clip1"])
        strips[1].mod = "BLUR_FILL"
        out = ra.layouts_of(None, strips, ["clip1", "clip (bottom)", "clip2", "talk", "nope"], Stand())
        self.assertEqual(out["clip1"], {"layout": "split_top", "part": 0, "partner": "clip1 (bottom)"})
        self.assertEqual(out["clip2"], {"layout": "full_blur"})
        self.assertNotIn("talk", out)
        self.assertNotIn("nope", out)

    def test_a_full_tag_without_its_modifier_is_not_already_full(self):
        strips = _edit()
        _apply(strips, "full", ["clip"])
        strips[0].mod = None                                   # the artist removed the modifier
        self.assertEqual(ra.current_layout(strips[0], Stand()), "")
        out = _apply(strips, "full", ["clip"])
        self.assertEqual((out["changed"], out["unchanged"]), (["clip"], []))
        self.assertEqual(strips[0].mod, "CROP")

    def test_tracked_reads_from_the_keys_not_the_tag(self):
        strips = _edit()
        _apply(strips, "full_tracked", ["clip"], Stand(tracking="no vision model"))
        self.assertEqual(ra.current_layout(strips[0], Stand()), "full")
        strips[0].keyed = True
        self.assertEqual(ra.current_layout(strips[0], Stand()), "full_tracked")

    def test_split_numbers_use_the_unscaled_resolution(self):
        text = (_ADDON / "reframe_layouts.py").read_text(encoding="utf-8")
        body = text[text.index("    def canvas_size"):text.index("    def split_problem")]
        self.assertIn("resolution_x", body)
        self.assertNotIn("resolution_percentage", body)
        # and the numbers for 1080 x 1920 do not depend on the percentage at all
        top, bottom = rl.plan("split_half", 1920, 1080, 1080, 1920).parts
        self.assertEqual(top.offset, (0.0, 480.0))

    def test_the_sidebar_box_leaves_the_toolshelf_icons_alone_on_bforartists(self):
        # Preview icons drawn in the sidebar blanked the toolshelf's Video buttons (measured on Bforartists 5.2.3):
        # with the toolshelf tabs on, the sidebar box takes the Blender icons and keeps its PNGs for the toolshelf.
        text = (_ADDON / "reframe_layouts.py").read_text(encoding="utf-8")
        body = text[text.index("def _draw_buttons"):text.index("class BFACW_PT_reframe_sidebar")]
        self.assertIn('getattr(context.space_data, "show_toolshelf_tabs", False)', body)
        self.assertIn("previews=previews", body)
        self.assertEqual(body.count("previews=previews"), 2)

    def test_rotation_and_origin_are_reset_with_the_rest(self):
        strips = _edit()
        strips[0].transform.rotation = 0.4
        strips[0].transform.origin = (0.2, 0.9)
        _apply(strips, "split_half", ["clip"])
        for strip in strips[:1] + [s for s in strips if s.name == "clip (bottom)"]:
            self.assertEqual(strip.transform.rotation, 0.0)
            self.assertEqual(tuple(strip.transform.origin), (0.5, 0.5))


class TestOtherWands(unittest.TestCase):

    def test_auto_captions_caption_a_split_once(self):
        cap = _import("captions")
        items = [{"name": "clip", "type": "MOVIE", "key": "k", "left": 1, "right": 76, "muted": False,
                  "retimed": False, "split_group": "clip", "split_part": 0},
                 {"name": "clip (bottom)", "type": "MOVIE", "key": "k", "left": 1, "right": 76, "muted": False,
                  "retimed": False, "split_group": "clip", "split_part": 1}]
        todo, skipped, partners = cap.plan_strips(items)
        self.assertEqual([it["name"] for it in todo], ["clip"])
        self.assertEqual(partners, {"clip (bottom)": ["clip"]})
        self.assertEqual(skipped, {})
        # The second part alone is captioned as before; so is any movie that is not a split's part.
        todo, _s, partners = cap.plan_strips(items[1:])
        self.assertEqual([it["name"] for it in todo], ["clip (bottom)"])
        self.assertEqual(partners, {})
        plain = [dict(items[0], split_group="", split_part=0), dict(items[1], split_group="", split_part=0)]
        self.assertEqual(len(cap.plan_strips(plain)[0]), 2)

    def test_remove_silences_cuts_every_strip_so_the_partner_follows(self):
        # The cut plan gives every media strip at the level the same cuts, selected or not: a split's two
        # parts (same frames) are cut at the same frames, and the movies' captions are trimmed, not cut.
        cut_plan = _import("wand.cut_plan")
        rows = [{"name": "clip", "left": 1, "right": 76, "kind": "media", "locked": False, "refuse": ""},
                {"name": "clip (bottom)", "left": 1, "right": 76, "kind": "media", "locked": False, "refuse": ""},
                {"name": "Caption 1", "left": 1, "right": 30, "kind": "text", "locked": False, "refuse": ""}]
        out = cut_plan.plan(rows, [(20, 40)], True, 25.0)
        self.assertEqual(out["refused"], [])
        self.assertEqual(out["cut"]["clip"], out["cut"]["clip (bottom)"])
        self.assertEqual(out["cut"]["clip"], [(20, 40)])
        self.assertNotIn("Caption 1", out["cut"])
        # A locked part refuses the whole edit, so the two parts never drift apart.
        rows[1]["locked"] = True
        out = cut_plan.plan(rows, [(20, 40)], True, 25.0)
        self.assertEqual([name for name, _why in out["refused"]], ["clip (bottom)"])


class TestTool(unittest.TestCase):

    def _load(self, api):
        namespace = {"__name__": "toolcode"}
        source = (_TOOLS / "reframe_strips_toolcode.py").read_text(encoding="utf-8")
        exec(compile(source, "reframe_strips_toolcode.py", "exec"), namespace)  # pylint: disable=exec-used
        namespace["_coworker_api"] = lambda: api
        return namespace

    def test_result_and_errors(self):
        calls = []

        def reframe_strips(layout, strips, swap):
            calls.append((layout, strips, swap))
            return {"status": "ok", "message": "Split, Half and Half: 1 strip", "layout": layout, "changed": ["a"],
                    "created": ["a (bottom)"], "removed": [], "skipped": [], "scene": "Edit", "extra": 1}
        ns = self._load(types.SimpleNamespace(reframe_strips=reframe_strips))
        out = ns["main"](ns["Params"](layout="split_half", strips=("a",), swap=True))._asdict()
        self.assertEqual(calls, [("split_half", ["a"], True)])
        self.assertEqual((out["status"], out["changed"], out["created"], out["scene"]), ("ok", ["a"], ["a (bottom)"], "Edit"))
        self.assertNotIn("extra", out)

        def boom(*_a):
            raise RuntimeError("Coworker isn't running")
        out = self._load(types.SimpleNamespace(reframe_strips=boom))["main"](
            self._load(None)["Params"](layout="full"))._asdict()
        self.assertEqual(out["status"], "error")

    def test_params_match_the_tool_signature(self):
        tool = (_TOOLS / "reframe_strips.py").read_text(encoding="utf-8")
        sig = re.search(r"def reframe_strips\((.*?)\) ->", tool, re.S).group(1)
        self.assertEqual(re.findall(r"^\s*(\w+):", sig, re.M), ["layout", "strips", "swap"])
        self.assertEqual(list(self._load(None)["Params"]._fields), ["layout", "strips", "swap"])

    def test_registered_in_the_vse_lists_and_the_docs(self):
        spec = importlib.util.spec_from_file_location("tool_domains_606", _REPO / "mcp" / "blmcp" / "tool_domains.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIn("reframe_strips", module.DOMAINS["vse"])
        text = (_ADDON / "agent_controller.py").read_text(encoding="utf-8")
        self.assertIn('"reframe_strips"', re.search(r'"vse": frozenset\(\{(.*?)\}\)', text, re.S).group(1))
        self.assertIn("reframe_strips", (_ADDON / "skills" / "mcp_tools.md").read_text(encoding="utf-8"))
        self.assertIn("reframe_strips", (_TOOLS / "load_tools.py").read_text(encoding="utf-8"))
        self.assertIn('"reframe_strips"', (_ADDON / "coworker_api.py").read_text(encoding="utf-8"))

    def test_the_tool_changes_the_scene_so_a_running_render_refuses_it(self):
        jobs = _import("long_jobs")
        self.assertTrue(jobs.changes_scene_while_busy("reframe_strips"))
        self.assertIn("Not run:", jobs.scene_busy_message("reframe_strips", is_running=lambda kind: kind == "RENDER"))
        self.assertEqual(jobs.scene_busy_message("reframe_strips", is_running=lambda kind: False), "")

    def test_the_tool_is_not_read_only(self):
        tool = (_TOOLS / "reframe_strips.py").read_text(encoding="utf-8")
        self.assertIn("readOnlyHint=False", tool)
        engine = _import("engine")
        self.assertNotIn("reframe_strips", engine.PLAN_SAFE_TOOLS)

    def test_layout_names_the_agent_may_use(self):
        text = (_ADDON / "reframe_layouts.py").read_text(encoding="utf-8")
        aliases = re.search(r"_ALIASES = \{(.*?)\}", text, re.S).group(1)
        for word in ("half", "blur", "tracked", "top", "bottom"):
            self.assertIn('"{:s}"'.format(word), aliases)


if __name__ == "__main__":
    unittest.main()
