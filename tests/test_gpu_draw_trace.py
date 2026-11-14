# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the chat's draw trace (ui_gpu/draw_trace.py, #387): the ring buffer keeps the last
draws, frames that look wrong get flags, the copied text says what happened, and the trace
never raises.

Run with::

    python -m unittest tests.test_gpu_draw_trace -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_draw_trace_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".ui_gpu.draw_trace")


dt = _load()


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        self.t += 0.016
        return self.t


def _frame(**kw):
    base = dict(where="sidebar", area=0xA1, region=0xB2, region_w=300, region_h=800, tab="Coworker",
                source="both", rect=(0.0, 0.0, 276.0, 700.0))
    base.update(kw)
    return dt.Frame(**base)


def _trace(size=300):
    return dt.DrawTrace(size=size, clock=_Clock())


class TestRingBuffer(unittest.TestCase):

    def test_keeps_the_last_draws(self):
        trace = _trace(size=5)
        for n in range(12):
            trace.add(_frame(turns=n))
        self.assertEqual([f.turns for f in trace.frames], [7, 8, 9, 10, 11])
        self.assertEqual(trace.dropped, 7)
        self.assertIn("5 draws", trace.text())
        self.assertIn("7 older ones dropped", trace.text())

    def test_clear(self):
        trace = _trace()
        trace.add(_frame())
        trace.clear()
        self.assertEqual(len(trace.frames), 0)
        self.assertIn("No draws recorded", trace.text())

    def test_marker_age_counts_draws_without_a_marker(self):
        trace = _trace()
        ages = [trace.add(_frame(source=s)).marker_age for s in ("view", "both", "view", "view", "none", "top")]
        self.assertEqual(ages, [-1, 0, 1, 2, 3, 0])       # -1: no marker seen yet in this region

    def test_regions_are_apart(self):
        trace = _trace()
        trace.add(_frame(region=1, region_w=300))
        f = trace.add(_frame(region=2, region_w=500))
        self.assertNotIn("resized", f.flags)              # another region's size isn't this one's


class TestFlags(unittest.TestCase):

    def flags(self, *frames):
        trace = _trace()
        return [trace.add(f).flags for f in frames][-1]

    def test_a_normal_frame_has_none(self):
        self.assertEqual(self.flags(_frame(), _frame()), ())

    def test_outside_the_region(self):
        self.assertIn("outside", self.flags(_frame(rect=(0.0, 0.0, 320.0, 700.0))))
        self.assertIn("outside", self.flags(_frame(rect=(-8.0, 0.0, 276.0, 700.0))))
        self.assertNotIn("outside", self.flags(_frame(rect=(0.0, 0.0, 300.5, 800.0))))

    def test_drawn_on_another_tab(self):
        self.assertIn("other_tab", self.flags(_frame(tab="Item")))
        self.assertNotIn("other_tab", self.flags(_frame(tab="Item", rect=None, source="none")))
        self.assertNotIn("other_tab", self.flags(_frame(where="editor", tab="", source="editor")))

    def test_stale_room(self):
        self.assertIn("stale", self.flags(_frame(source="view")))

    def test_resized_and_width_jump(self):
        self.assertIn("resized", self.flags(_frame(), _frame(region_w=360, rect=(0.0, 0.0, 336.0, 700.0))))
        # Same region size, other width: the room was taken from somewhere stale.
        jump = self.flags(_frame(), _frame(rect=(0.0, 0.0, 250.0, 700.0)))
        self.assertIn("width_jump", jump)
        self.assertNotIn("resized", jump)
        self.assertNotIn("width_jump", self.flags(_frame(), _frame(rect=(0.0, 40.0, 276.0, 660.0))))  # moved down

    def test_blip(self):
        self.assertIn("blip", self.flags(_frame(), _frame(rect=None, source="none")))
        self.assertNotIn("blip", self.flags(_frame(), _frame(rect=None, source="none", tab="Item")))   # tab switch
        self.assertNotIn("blip", self.flags(_frame(), _frame(rect=None, source="none", region_h=500)))

    def test_live_changed_slow_error(self):
        self.assertIn("live_changed", self.flags(_frame(live_before=(10, 0, 3), live_after=(14, 0, 3))))
        self.assertIn("slow", self.flags(_frame(find_ms=20.0, layout_ms=25.0, draw_ms=6.0)))
        self.assertNotIn("slow", self.flags(_frame(find_ms=2.0, layout_ms=5.0, draw_ms=1.0)))
        self.assertIn("error", self.flags(_frame(error="RuntimeError: boom")))
        self.assertEqual(set(dt.FLAGS), {"outside", "other_tab", "stale", "resized", "width_jump", "blip",
                                         "live_changed", "slow", "error"})


class TestText(unittest.TestCase):

    def test_copy_text(self):
        trace = _trace()
        trace.add(_frame())
        trace.add(_frame(tab="Item", source="top", rect=(0.0, 0.0, 276.0, 380.0),
                         live_before=(5, 2, 4), live_after=(9, 2, 4), running=True, turns=3, scroll=12.0,
                         content_h=900.0, find_ms=1.25, layout_ms=7.5, draw_ms=2.0))
        trace.add(_frame(error="ValueError: bad"))
        text = trace.text("Blender 5.2.4 | Coworker 1.1.42 | trace on")
        lines = text.splitlines()
        self.assertEqual(lines[0], "Coworker chat draw trace (#387)")
        self.assertEqual(lines[1], "Blender 5.2.4 | Coworker 1.1.42 | trace on")
        self.assertIn("3 draws", text)
        self.assertIn("other_tab x1: " + dt.FLAGS["other_tab"], text)
        self.assertIn("error x1", text)
        row = next(line for line in lines if "tab=Item" in line)
        for part in ("side", "a00a1 r00b2", "300x800", " top ", "age=0", "rect=0,0 276x380", "scroll=12/900",
                     "turns=3 run", "live=5/2/4->9/2/4", "ms=1.2+7.5+2.0", "[other_tab live_changed]"):
            self.assertIn(part, row)
        self.assertTrue(lines[-1].endswith("error: ValueError: bad"))
        self.assertTrue(text.endswith("\n"))

    def test_no_flags(self):
        trace = _trace()
        trace.add(_frame())
        self.assertIn("Flags: none", trace.text())

    def test_short_error(self):
        try:
            raise RuntimeError("line one\nline two " + "x" * 300)
        except RuntimeError as ex:
            short = dt.short_error(ex)
        self.assertTrue(short.startswith("RuntimeError: line one line two"))
        self.assertLessEqual(len(short), 120)
        self.assertNotIn("\n", short)


class TestNeverRaises(unittest.TestCase):

    def test_bad_frame_is_dropped(self):
        trace = _trace()
        self.assertIsNone(trace.add(_frame(rect=("x", None))))      # nonsense geometry
        self.assertIsNone(trace.add(None))
        self.assertEqual(len(trace.frames), 0)

    def test_bad_clock(self):
        def broken():
            raise OSError("clock")
        trace = dt.DrawTrace(clock=broken)
        self.assertIsNone(trace.add(_frame()))

    def test_text_never_raises(self):
        trace = _trace()
        trace.add(_frame())
        trace.frames.append("not a frame")
        text = trace.text()
        self.assertIn("could not be written", text)

    def test_many_regions(self):
        trace = _trace()
        for n in range(200):          # editors opened and closed: the per-region memory stays small
            trace.add(_frame(region=n))
        self.assertLessEqual(len(trace._last), 65)


if __name__ == "__main__":
    unittest.main()
