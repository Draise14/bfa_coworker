# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The sidebar chat finds its panel's room from invisible markers read back from the drawn
sidebar (chat_editor._find_chat_rect).  With one marker off screen it takes the room's
height it learnt while both showed.  That height was kept when the room's size changed
for another reason -- the UI scale (Resolution Scale, a monitor with another DPI) or the
chat's rows -- so with the bottom marker off screen the chat vanished: the stale height
put the room's bottom on screen, which reads as "the panel is animating", and nothing was
drawn, frame after frame (#387, reproduced live on Blender 5.2.4).  The height is now kept
with what it was learnt for, and a stale one is not used.

These tests run the real function on a fake framebuffer.

Run with::

    python -m unittest tests.test_chat_room_height -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path
from typing import Any

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
BACK = (66, 66, 66)                 # _panel_back_bytes() without a theme
TOP, BOTTOM = (66, 66, 69), (66, 69, 66)
W, H, TABS = 280, 836, 24


def _layout():
    name = "bfacw_room_height_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".ui_gpu.layout")


class _Pixels:
    def __init__(self, rows):
        self.rows = rows

    def to_list(self):
        return self.rows


class _Framebuffer:
    """A drawn sidebar: panel background, marker rows at *top* (below it) and *bottom*."""

    def __init__(self, top=None, bottom=None):
        self.top, self.bottom = top, bottom

    def _pixel(self, x, y):
        marks = (10, (W - TABS) // 2 - 4, W - TABS - 18)
        on_mark = any(m <= x < m + 8 for m in marks)
        if on_mark and self.top is not None and self.top - 4 <= y < self.top:
            return list(TOP) + [255]
        if on_mark and self.bottom is not None and self.bottom <= y < self.bottom + 4:
            return list(BOTTOM) + [255]
        return list(BACK) + [255]

    def read_color(self, x0, y0, w, h, _channels, _slot, _fmt):
        return _Pixels([[self._pixel(x0 + i, y0 + j) for i in range(w)] for j in range(h)])


class _View2D:
    def region_to_view(self, x, y):
        return (x, y)

    def view_to_region(self, x, y, clip=False):
        return (x, y)


class _Region:
    height, width = H, W
    view2d = _View2D()

    def as_pointer(self):
        return 0x1234


def _load():
    src = (_ADDON / "chat_editor.py").read_text(encoding="utf-8")
    start = src.index("_MARKERS: dict[str, Any]")
    code = src[start:src.index("def _draw_sidebar", start)]
    bpy = types.SimpleNamespace(context=None)      # no theme: the default panel colour
    ns = {"Any": Any, "bpy": bpy, "gpu_layout": _layout()}
    exec(compile(code, "chat_editor.py", "exec"), ns)   # pylint: disable=exec-used
    return ns


class TestLearntRoomHeight(unittest.TestCase):

    def setUp(self):
        self.ns = _load()
        self.fb = _Framebuffer()
        gpu = types.ModuleType("gpu")
        gpu.state = types.SimpleNamespace(active_framebuffer_get=lambda: self.fb)
        self._saved = sys.modules.get("gpu")
        sys.modules["gpu"] = gpu
        self.region = _Region()

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("gpu", None)
        else:
            sys.modules["gpu"] = self._saved

    def find(self, top, bottom, rows, ui_scale):
        self.fb.top, self.fb.bottom = top, bottom
        return self.ns["_find_chat_rect"](self.region, rows * 20.0 * ui_scale, TABS, 1.0,
                                          learnt_for=(rows, ui_scale))

    def test_both_markers_learn_the_height(self):
        rect = self.find(681, 91, 30, 1.0)
        self.assertEqual((rect.y, rect.top), (91.0, 681.0))
        self.assertEqual(self.ns["_FIND"]["source"], "both")
        self.assertEqual(self.ns["_ROOM_H"][0x1234], (590.0, (30, 1.0)))

    def test_bottom_off_screen_after_the_ui_scale_grew(self):
        self.find(681, 91, 30, 1.0)                       # learnt: 590 px at UI scale 1.0
        # UI scale 1.3: the room is ~767 px, its top at 636 and its bottom below the sidebar.
        rect = self.find(636, None, 30, 1.3)
        self.assertIsNotNone(rect)                        # was None: the chat vanished (#387)
        self.assertEqual(rect.top, 636.0)
        self.assertLess(rect.y, 0.0)
        self.assertEqual(self.ns["_FIND"]["source"], "top")

    def test_bottom_off_screen_after_the_rows_grew(self):
        self.find(681, 91, 30, 1.0)
        self.assertIsNotNone(self.find(681, None, 70, 1.0))

    def test_top_off_screen_after_the_ui_scale_grew(self):
        self.find(681, 91, 30, 1.0)
        rect = self.find(None, 120, 40, 1.3)            # taller than the sidebar, top scrolled away
        self.assertIsNotNone(rect)
        self.assertGreater(rect.top, H)

    def test_mid_animation_is_still_skipped(self):
        # Same rows and scale: the height is right, so a missing bottom marker that should be
        # on screen means the panel is opening / closing: skip the frame, as before.
        self.find(681, 91, 30, 1.0)
        self.assertIsNone(self.find(681, None, 30, 1.0))
        self.assertEqual(self.ns["_FIND"]["source"], "none")


if __name__ == "__main__":
    unittest.main()
