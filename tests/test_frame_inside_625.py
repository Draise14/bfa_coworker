# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Frame Inside (#625), bpy-free.

1. The numbers (``wand/reframe_inside``): sliders to framing and back, the picture's borders, the room
   an axis has, the bake of the artist's keys (why a zoom between two keys needs every frame).
2. The logic (``wand/reframe_apply.set_inside`` / swap / reset / read) on the stand-in Sequencer of
   ``test_reframe_layouts``, with keys.

Run with::

    python -m unittest tests.test_frame_inside_625 -v
"""

__all__ = ()

import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.test_reframe_layouts import FRAMES, SOURCES, SPLITS, Stand, _apply, _edit, ra, rl, _import

ri = _import("wand.reframe_inside")


class KeyedStand(Stand):
    """The stand-in with Frame Inside's keys: ``strip.keys`` is ``{frame: (scale, ox, oy, crop)}``."""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.frame = 1

    def anchors(self, strip):          # stored relative to the strip's start, like the Blender backend
        origin = getattr(strip, "origin", 0)
        return {int(k) + origin: tuple(v) for k, v in dict(strip.props.get("inside", {})).items()}

    def has_keys(self, scene, strip):
        return bool(getattr(strip, "keys", None))

    def key_part(self, scene, strip, anchors, parts):
        strip.keys = {frame: part for frame, part in parts}
        strip.props["inside"] = {str(f - getattr(strip, "origin", 0)): list(v) for f, v in anchors.items()}
        ra.set_part(strip, min(parts, key=lambda row: abs(row[0] - self.frame))[1])

    def current_frame(self, scene):
        return self.frame

    def clear_keys(self, scene, strip):
        super().clear_keys(scene, strip)
        strip.keys = {}
        strip.props.pop("inside", None)


def _move(strip, delta):
    """Move a strip in time the way Blender does: its keys go with it, the custom properties stay."""
    strip.keys = {frame + delta: part for frame, part in strip.keys.items()}
    strip.origin = getattr(strip, "origin", 0) + delta


def _split(layout_id="split_top", **kw):
    strips = _edit(**kw)
    backend = KeyedStand()
    _apply(strips, layout_id, ["clip"], backend)
    top = strips[0]
    bottom = next(s for s in strips if s.name == "clip (bottom)")
    return strips, top, bottom, backend


def _visible(strip, index, layout_id, sw=1920, sh=1080, fw=1080, fh=1920):
    part = rl.Part(index, rl.part_rects(layout_id, fw, fh)[index], strip.transform.scale_x,
                   (strip.transform.offset_x, strip.transform.offset_y),
                   (strip.crop.min_x, strip.crop.max_x, strip.crop.min_y, strip.crop.max_y), rl.DEFAULT_FRAMING)
    return rl.visible_rect(part, sw, sh, fw, fh)


class TestNumbers(unittest.TestCase):

    def test_the_sliders_round_trip(self):
        for layout_id in SPLITS:
            for sw, sh in SOURCES:
                for fw, fh in FRAMES:
                    for rect in rl.part_rects(layout_id, fw, fh):
                        for zoom in (1.0, 1.7, 4.0):
                            for x, y in ((0, 0), (1, -1), (-0.5, 0.25)):
                                framing = ri.framing_from_inside(ri.Inside(x, y, zoom), rect, sw, sh)
                                back = ri.inside_from_framing(framing, rect, sw, sh)
                                half_u, half_v = ri._halves(rect, sw, sh, zoom)           # pylint: disable=protected-access
                                self.assertAlmostEqual(back.zoom, zoom, 9)
                                if 0.5 - half_u > 1e-9:
                                    self.assertAlmostEqual(back.x, x, 6)
                                else:
                                    self.assertEqual(back.x, 0.0)
                                if 0.5 - half_v > 1e-9:
                                    self.assertAlmostEqual(back.y, y, 6)
                                else:
                                    self.assertEqual(back.y, 0.0)

    def test_the_extremes_put_the_window_on_the_pictures_border(self):
        # 16:9 into the top half of a 9:16 frame: the picture is wider than the window, so X has room; at zoom 1 it
        # fills the height, so Y has none.
        rect = rl.part_rects("split_half", 1080, 1920)[0]
        left = ri.framing_from_inside(ri.Inside(-1, 0, 1), rect, 1920, 1080)
        right = ri.framing_from_inside(ri.Inside(1, 0, 1), rect, 1920, 1080)
        self.assertAlmostEqual(left.u, ri._halves(rect, 1920, 1080, 1)[0], 9)  # pylint: disable=protected-access
        self.assertAlmostEqual(right.u, 1 - left.u, 9)
        up = ri.framing_from_inside(ri.Inside(0, 1, 1), rect, 1920, 1080)
        self.assertAlmostEqual(up.v, 0.5, 9)                      # no room: the same as centred
        zoomed = ri.framing_from_inside(ri.Inside(0, 1, 2), rect, 1920, 1080)
        self.assertGreater(zoomed.v, 0.5)

    def test_every_pan_and_zoom_still_covers_the_window_inside_the_picture(self):
        for layout_id in SPLITS:
            for sw, sh in SOURCES:
                fw, fh = 1080, 1920
                for index, rect in enumerate(rl.part_rects(layout_id, fw, fh)):
                    for zoom in (1.0, 2.5, 4.0):
                        for x, y in ((-1, -1), (1, 1), (-1, 1), (1, -1), (0.3, -0.7)):
                            framing = ri.framing_from_inside(ri.Inside(x, y, zoom), rect, sw, sh)
                            part = rl.part_for(index, rect, sw, sh, fw, fh, framing)
                            vis = rl.visible_rect(part, sw, sh, fw, fh)
                            self.assertLessEqual(vis[0], rect[0] + 1e-6, (layout_id, sw, sh, zoom, x, y))
                            self.assertLessEqual(vis[1], rect[1] + 1e-6)
                            self.assertGreaterEqual(vis[2], rect[2] - 1e-6)
                            self.assertGreaterEqual(vis[3], rect[3] - 1e-6)
                            self.assertAlmostEqual(framing.zoom, zoom, 9)

    def test_values_are_clamped(self):
        rect = rl.part_rects("split_half", 1080, 1920)[0]
        a = ri.framing_from_inside(ri.Inside(9, -9, 99), rect, 1920, 1080)
        b = ri.framing_from_inside(ri.Inside(1, -1, ri.MAX_ZOOM), rect, 1920, 1080)
        self.assertEqual(a, b)
        self.assertEqual(ri.framing_from_inside(ri.Inside(0, 0, 0.1), rect, 1920, 1080).zoom, 1.0)


class TestBake(unittest.TestCase):

    def test_nothing_and_one_key(self):
        self.assertEqual(ri.bake({}), [])
        self.assertEqual(ri.bake({7: (0.4, 0.5, 2.0)}), [(7, rl.Framing(0.4, 0.5, 2.0))])

    def test_the_same_framing_between_two_anchors_is_two_keys(self):
        out = ri.bake({50: (0.7, 0.5, 1.5), 1: (0.7, 0.5, 1.5)})
        self.assertEqual([f for f, _ in out], [1, 50])

    def test_a_pan_at_one_zoom_keys_every_frame(self):
        out = ri.bake({50: (0.7, 0.5, 1.5), 1: (0.3, 0.5, 1.5)})
        self.assertEqual([f for f, _ in out], list(range(1, 51)))

    def test_a_zoom_move_keys_every_frame_between(self):
        out = ri.bake({1: (0.3, 0.5, 1.0), 11: (0.7, 0.5, 3.0), 21: (0.7, 0.5, 3.0)})
        self.assertEqual([f for f, _ in out], list(range(1, 12)) + [21])
        mid = dict(out)[6]
        self.assertAlmostEqual(mid.u, 0.5, 9)
        self.assertAlmostEqual(mid.zoom, 2.0, 9)

    def _worst_gap(self, keys, rect, sw, sh, fw=1080, fh=1920):
        """How many frame pixels short of *rect* the picture falls at any frame between *keys*, with Blender's
        linear interpolation of scale and offset between them and its rounding of the (integer) crop."""
        rows = []
        for frame, framing in keys:
            part = rl.part_for(0, rect, sw, sh, fw, fh, framing)
            rows.append((frame, part))
        worst = 0.0
        for (f0, p0), (f1, p1) in zip(rows, rows[1:]):
            for frame in range(f0, f1 + 1):
                t = (frame - f0) / (f1 - f0)
                mix = lambda a, b: a + (b - a) * t                                  # noqa: E731
                part = rl.Part(0, rect, mix(p0.scale, p1.scale), (mix(p0.offset[0], p1.offset[0]),
                                                                     mix(p0.offset[1], p1.offset[1])),
                               tuple(round(mix(a, b)) for a, b in zip(p0.crop, p1.crop)), rl.DEFAULT_FRAMING)
                vis = rl.visible_rect(part, sw, sh, fw, fh)
                worst = max(worst, vis[0] - rect[0], vis[1] - rect[1], rect[2] - vis[2], rect[3] - vis[3])
        return worst

    def test_two_keys_with_a_zoom_change_leave_gaps_and_the_bake_does_not(self):
        rect = rl.part_rects("split_half", 1080, 1920)[0]
        a, b = (1, rl.Framing(0.35, 0.5, 1.0)), (21, rl.Framing(0.65, 0.5, 3.0))
        self.assertGreater(self._worst_gap([a, b], rect, 1920, 1080), 2.0)       # the reason for the bake
        baked = ri.bake({1: a[1][:3], 21: b[1][:3]})
        self.assertEqual(len(baked), 21)
        self.assertLess(self._worst_gap(baked, rect, 1920, 1080), 0.5)

    def test_a_pan_at_one_zoom_stays_covered_with_the_crop_rounded(self):
        for layout_id in SPLITS:
            for zoom in (1.0, 2.0, 4.0):
                for index, rect in enumerate(rl.part_rects(layout_id, 1080, 1920)):
                    rows = ri.bake({1: (0.2, 0.2, zoom), 40: (0.8, 0.8, zoom)})
                    keys = [(f, fr) for f, fr in rows]
                    worst = 0.0
                    for (f0, _a), (f1, _b) in zip(keys, keys[1:]):                 # every frame is keyed: no line between
                        self.assertEqual(f1 - f0, 1)
                    for _f, fr in keys:
                        part = rl.part_for(index, rect, 1920, 1080, 1080, 1920, fr)
                        part = rl.Part(index, rect, part.scale, part.offset, tuple(round(c) for c in part.crop), rl.DEFAULT_FRAMING)
                        vis = rl.visible_rect(part, 1920, 1080, 1080, 1920)
                        worst = max(worst, vis[0] - rect[0], vis[1] - rect[1], rect[2] - vis[2], rect[3] - vis[3])
                    self.assertLess(worst, 0.5, (layout_id, zoom, index))

    def test_two_keys_of_a_same_zoom_pan_leave_a_gap_with_the_crop_rounded(self):
        rect = rl.part_rects("split_half", 1080, 1920)[0]
        a, b = (1, rl.Framing(0.2, 0.2, 4.0)), (40, rl.Framing(0.8, 0.8, 4.0))
        self.assertGreater(self._worst_gap([a, b], rect, 1920, 1080), 0.5)        # why a pan is keyed every frame


class TestSetInside(unittest.TestCase):

    def test_pan_and_zoom_move_one_part_only_and_keep_everything_else(self):
        strips, top, bottom, backend = _split()
        before = (bottom.transform.scale_x, bottom.transform.offset_x, bottom.crop.min_x)
        tag = dict(top.props["coworker_layout"])
        out = ra.set_inside(None, strips, ["clip"], backend, x=-1.0, zoom=2.0)
        self.assertEqual((out["status"], out["changed"]), ("ok", ["clip"]))
        now = ra.inside_of(None, top, backend)
        self.assertAlmostEqual(now["x"], -1.0, 3)
        self.assertAlmostEqual(now["zoom"], 2.0, 3)
        self.assertAlmostEqual(now["y"], 0.0, 3)
        self.assertEqual(before, (bottom.transform.scale_x, bottom.transform.offset_x, bottom.crop.min_x))
        self.assertEqual(top.props["coworker_layout"], tag)
        self.assertFalse(now["keyed"])
        vis = _visible(top, 0, "split_top")
        rect = rl.part_rects("split_top", 1080, 1920)[0]
        self.assertLessEqual(vis[0], rect[0] + 1e-6)
        self.assertGreaterEqual(vis[2], rect[2] - 1e-6)

    def test_only_the_values_given_change(self):
        strips, top, _bottom, backend = _split()
        ra.set_inside(None, strips, ["clip"], backend, x=0.5)
        ra.set_inside(None, strips, ["clip"], backend, zoom=1.5)
        now = ra.inside_of(None, top, backend)
        self.assertAlmostEqual(now["x"], 0.5, 3)
        self.assertAlmostEqual(now["zoom"], 1.5, 3)

    def test_either_part_and_every_split(self):
        for layout_id in SPLITS:
            strips, _top, bottom, backend = _split(layout_id)
            out = ra.set_inside(None, strips, ["clip (bottom)"], backend, y=1.0, zoom=3.0)
            self.assertEqual(out["status"], "ok", layout_id)
            self.assertAlmostEqual(ra.inside_of(None, bottom, backend)["zoom"], 3.0, 3)

    def test_what_is_no_split_part_is_refused_and_named(self):
        strips = _edit()
        out = ra.set_inside(None, strips, ["clip", "talk", "nope"], KeyedStand(), x=1.0)
        self.assertEqual(out["status"], "nothing")
        self.assertEqual({row["strip"] for row in out["skipped"]}, {"clip", "talk", "nope"})
        with self.assertRaises(ra.LayoutError):
            ra.inside_of(None, strips[0], KeyedStand())

    def test_locked_is_skipped(self):
        strips, top, _b, backend = _split()
        top.lock = True
        out = ra.set_inside(None, strips, ["clip"], backend, x=1.0)
        self.assertEqual(out["status"], "nothing")

    def test_a_track_all_part_is_refused_not_overwritten(self):
        strips, top, _b, backend = _split("track_all")
        top.keys = {1: None}                                     # keyed by Track All: no anchors
        out = ra.set_inside(None, strips, ["clip"], backend, x=1.0)
        self.assertEqual(out["status"], "nothing")
        self.assertIn("Track All", out["skipped"][0]["reason"])

    def test_key_makes_the_first_key_at_the_current_frame(self):
        strips, top, _b, backend = _split()
        backend.frame = 10
        ra.set_inside(None, strips, ["clip"], backend, x=-0.6, key=True)
        self.assertEqual(list(backend.anchors(top)), [10])
        self.assertTrue(ra.inside_of(None, top, backend)["keyed"])
        self.assertEqual([f for f in top.keys], [10])

    def test_a_keyed_part_is_keyed_again_by_any_change(self):
        strips, top, _b, backend = _split()
        backend.frame = 1
        ra.set_inside(None, strips, ["clip"], backend, x=-0.5, zoom=1.0, key=True)
        backend.frame = 31
        ra.set_inside(None, strips, ["clip"], backend, x=0.5, zoom=3.0)              # no key flag: it is keyed already
        self.assertEqual(sorted(backend.anchors(top)), [1, 31])
        self.assertEqual(sorted(top.keys), list(range(1, 32)))                       # a zoom move: every frame
        backend.frame = 31
        ra.set_inside(None, strips, ["clip"], backend, x=0.25)                       # same frame: replaced, not added
        self.assertEqual(sorted(backend.anchors(top)), [1, 31])
        self.assertAlmostEqual(backend.anchors(top)[31][2], 3.0, 6)

    def test_keys_never_leave_a_gap_at_any_keyed_frame(self):
        strips, top, _b, backend = _split("split_half")
        backend.frame = 1
        ra.set_inside(None, strips, ["clip"], backend, x=-1.0, zoom=1.0, key=True)
        backend.frame = 25
        ra.set_inside(None, strips, ["clip"], backend, x=1.0, zoom=4.0)
        rect = rl.part_rects("split_half", 1080, 1920)[0]
        for frame, part in top.keys.items():
            vis = rl.visible_rect(part, 1920, 1080, 1080, 1920)
            self.assertLessEqual(vis[0], rect[0] + 1e-6, frame)
            self.assertGreaterEqual(vis[2], rect[2] - 1e-6, frame)
            self.assertLessEqual(vis[1], rect[1] + 1e-6, frame)
            self.assertGreaterEqual(vis[3], rect[3] - 1e-6, frame)


class TestTimeMoves(unittest.TestCase):

    def test_a_keyed_part_moved_in_time_is_edited_where_it_now_is(self):
        strips, top, _b, backend = _split()
        backend.frame = 1
        ra.set_inside(None, strips, ["clip"], backend, x=-0.5, zoom=1.0, key=True)
        backend.frame = 11
        ra.set_inside(None, strips, ["clip"], backend, x=0.5, zoom=2.0)
        _move(top, 20)                                                             # Remove Silences, a drag
        self.assertEqual(sorted(backend.anchors(top)), [21, 31])
        backend.frame = 31
        ra.set_inside(None, strips, ["clip"], backend, x=0.1)
        self.assertEqual(sorted(backend.anchors(top)), [21, 31])
        self.assertEqual(sorted(top.keys), list(range(21, 32)))                    # re-keyed where the strip is now
        self.assertAlmostEqual(backend.anchors(top)[31][2], 2.0, 6)

    def test_anchors_outside_the_strip_after_a_cut_survive_a_swap(self):
        strips, top, bottom, backend = _split()
        backend.frame = 1
        ra.set_inside(None, strips, ["clip"], backend, x=-0.5, zoom=1.0, key=True)
        backend.frame = 200                                                        # later than the strip's last frame
        ra.set_inside(None, strips, ["clip"], backend, x=0.5, zoom=2.0)
        a_top = backend.anchors(top)
        out = ra.swap_parts(None, strips, ["clip"], backend)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(backend.anchors(bottom), a_top)
        self.assertEqual(max(bottom.keys), 200)


def _fake_modules():
    """A bpy that is only good enough to import ``reframe_layouts`` and call its tool."""
    base = type("Base", (), {})
    bpy_types = types.ModuleType("bpy.types")
    for name in ("Menu", "Operator", "Panel", "PropertyGroup", "AddonPreferences", "Header", "UIList"):
        setattr(bpy_types, name, type(name, (base,), {}))
    bpy_types.__getattr__ = lambda _name: mock.MagicMock()
    bpy = mock.MagicMock(name="bpy")
    bpy.types = bpy_types
    props = mock.MagicMock()
    bpy.props = props
    return {"bpy": bpy, "bpy.types": bpy_types, "bpy.props": props, "bpy.utils": bpy.utils,
            "mathutils": mock.MagicMock(), "gpu": mock.MagicMock(), "blf": mock.MagicMock(),
            "bpy_extras": mock.MagicMock()}


class TestTool(unittest.TestCase):
    """``tool_reframe_strips`` with the Frame Inside arguments, on the stand-in Sequencer."""

    @classmethod
    def setUpClass(cls):
        cls._patch = mock.patch.dict(sys.modules, _fake_modules())
        cls._patch.start()
        cls.mod = _import("reframe_layouts")

    @classmethod
    def tearDownClass(cls):
        cls._patch.stop()

    def _run(self, level, backend, **kw):
        mod = self.mod
        scene = SimpleNamespace(name="Scene", sequence_editor=object())
        with mock.patch.object(mod._wand, "_tool_scene", return_value=scene),                 mock.patch.object(mod, "_level_strips", return_value=level),                 mock.patch.object(mod, "BlenderBackend", lambda _scene: backend),                 mock.patch.object(mod._wand, "_push_undo") as undo:
            out = mod.tool_reframe_strips(**kw)
        return out, undo

    def test_pan_zoom_and_key_frame_the_part_with_one_undo_step(self):
        strips, top, _b, backend = _split()
        backend.frame = 4
        out, undo = self._run(strips, backend, strips=["clip"], pan_x=-1, zoom=2, key=True)
        self.assertEqual((out["status"], out["framed"]), ("ok", ["clip"]))
        self.assertEqual(undo.call_count, 1)
        self.assertEqual(list(backend.anchors(top)), [4])
        self.assertAlmostEqual(ra.inside_of(None, top, backend)["zoom"], 2.0, 3)

    def test_read_mode_gives_the_inside_framing(self):
        strips, _top, _b, backend = _split()
        ra.set_inside(None, strips, ["clip"], backend, zoom=3.0)
        out, undo = self._run(strips, backend, strips=["clip", "clip (bottom)"])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["layouts"]["clip"]["inside"]["zoom"], 3.0)
        self.assertEqual(out["layouts"]["clip (bottom)"]["inside"]["zoom"], 1.0)
        undo.assert_not_called()

    def test_a_layout_and_a_pan_in_one_call_is_one_undo_step(self):
        strips = _edit()
        backend = KeyedStand()
        out, undo = self._run(strips, backend, layout="split_half", strips=["clip"], pan_x=0.5, zoom=1.5)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["framed"], ["clip"])
        self.assertTrue(out["changed"])
        self.assertEqual(undo.call_count, 1)
        top = strips[0]
        self.assertAlmostEqual(ra.inside_of(None, top, backend)["zoom"], 1.5, 3)

    def test_a_pan_on_a_strip_with_no_split_is_not_an_ok(self):
        strips = _edit()
        out, undo = self._run(strips, KeyedStand(), strips=["clip"], pan_x=0.5)
        self.assertEqual(out["status"], "nothing")
        undo.assert_not_called()


class TestSwapResetRead(unittest.TestCase):

    def test_swap_takes_the_inside_framing_along(self):
        strips, top, bottom, backend = _split()
        ra.set_inside(None, strips, ["clip"], backend, x=-1.0, zoom=2.0)
        ra.swap_parts(None, strips, ["clip"], backend)
        self.assertAlmostEqual(ra.inside_of(None, bottom, backend)["zoom"], 2.0, 3)
        self.assertAlmostEqual(ra.inside_of(None, bottom, backend)["x"], -1.0, 3)
        self.assertAlmostEqual(ra.inside_of(None, top, backend)["zoom"], 1.0, 3)

    def test_swap_exchanges_the_keys_too(self):
        strips, top, bottom, backend = _split()
        backend.frame = 1
        ra.set_inside(None, strips, ["clip"], backend, x=-0.5, zoom=1.0, key=True)
        backend.frame = 21
        ra.set_inside(None, strips, ["clip"], backend, x=0.5, zoom=2.0)
        a_top = backend.anchors(top)
        ra.swap_parts(None, strips, ["clip"], backend)
        self.assertEqual(backend.anchors(bottom), a_top)
        self.assertEqual(sorted(bottom.keys), list(range(1, 22)))
        self.assertTrue(backend.has_keys(None, top))                       # the unkeyed side holds its framing from the first key
        self.assertEqual(sorted(backend.anchors(top)), [1])
        ra.swap_parts(None, strips, ["clip"], backend)                     # twice: the keys are back
        self.assertEqual(backend.anchors(top), a_top)

    def test_reset_clears_keys_and_framing(self):
        strips, top, _b, backend = _split()
        backend.frame = 1
        ra.set_inside(None, strips, ["clip"], backend, x=-0.5, zoom=2.0, key=True)
        out = ra.reset_framing(None, strips, ["clip"], backend)
        self.assertEqual(out["status"], "ok")
        now = ra.inside_of(None, top, backend)
        self.assertEqual((round(now["x"], 4), round(now["y"], 4), round(now["zoom"], 4), now["keyed"]), (0.0, 0.0, 1.0, False))
        self.assertEqual(backend.anchors(top), {})

    def test_a_new_layout_drops_the_keys_with_the_rest(self):
        strips, top, _b, backend = _split()
        ra.set_inside(None, strips, ["clip"], backend, x=-0.5, zoom=2.0, key=True)
        _apply(strips, "split_half", ["clip"], backend)
        self.assertEqual(backend.anchors(top), {})
        self.assertFalse(backend.has_keys(None, top))

    def test_read_gives_each_parts_framing(self):
        strips, _top, _b, backend = _split()
        ra.set_inside(None, strips, ["clip (bottom)"], backend, zoom=2.0)
        out = ra.layouts_of(None, strips, ["clip", "clip (bottom)"], backend)
        self.assertEqual(out["clip"]["inside"]["zoom"], 1.0)
        self.assertEqual(out["clip (bottom)"]["inside"]["zoom"], 2.0)
        self.assertFalse(out["clip (bottom)"]["inside"]["keyed"])


if __name__ == "__main__":
    unittest.main()
