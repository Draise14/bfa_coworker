# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The prompt's highlight ring while the chat's input is resized (#532).

The prompt draws a ring in the theme's text-cursor colour: full while it has the keyboard,
faint while the mouse is over it.  Dragging the prompt's top edge (#389) sizes it in whole
lines, so its top snaps while the mouse rides the edge: the mouse was inside the box on one
frame and a few pixels above it on the next, and the faint ring went on and off frame after
frame -- caught live on Bforartists 5.2.3 (editor and sidebar chat, prompt not focused):
``hhh..h..h..hhh..h..h..hhhhhhhhhh``, one character per frame, ``h`` = ring drawn.  While the
edge is dragged the box now counts as hovered, so the ring stays.

Run with::

    python -m unittest tests.test_gpu_resize_ring -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_resize_ring_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return [importlib.import_module(name + ".ui_gpu." + m) for m in ("surface", "layout", "style")]


sf, lay, st = _load()
RECT = lay.Rect(0, 0, 500, 600)
FORTY = "\n".join("Line {:02d} of the message".format(i + 1) for i in range(40))


def _frame(surface):
    """Draw one frame; return the rings drawn around the prompt: [(alpha, rect)]."""
    canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), surface.interaction())
    surface.draw(canvas, RECT, [])
    cursor = tuple(canvas.style.cursor[:3])
    box = surface.box.rect
    return [(cmd.color[3], cmd.rect) for cmd in canvas.commands
            if cmd.kind == "outline" and cmd.rect == box and tuple(cmd.color[:3]) == cursor]


def _edge(surface):
    rect = next(r for r, w in surface._hits if w == "input_resize")
    return rect.x + rect.w / 2, rect.y + rect.h / 2


class TestRingWhileResizing(unittest.TestCase):

    def setUp(self):
        self.surface = sf.ChatSurface()
        _frame(self.surface)
        self.surface.box.model.set_text(FORTY)       # taller than the box: it can grow
        _frame(self.surface)

    def _drag(self, steps):
        """Press the top edge, move by *steps* (dy each, one frame per move); the ring per frame."""
        x, y = _edge(self.surface)
        self.surface.on_move(x, y)
        _frame(self.surface)
        self.surface.on_press(x, y)
        self.assertEqual(self.surface.pressed, "input_resize")
        frames = []
        for dy in steps:
            y += dy
            self.surface.on_move(x, y)
            frames.append((y, self.surface.box.rect.top, _frame(self.surface)))
        return x, y, frames

    def test_the_hover_ring_stays_while_the_edge_is_dragged(self):
        self.assertFalse(self.surface.box.focused)
        _x, _y, frames = self._drag([3.0] * 30 + [-3.0] * 40 + [3.0] * 10)
        tops = {round(top, 1) for _y, top, _rings in frames}
        self.assertGreater(len(tops), 3)                       # the box did resize, line by line
        missing = [(round(y, 1), round(top, 1)) for y, top, rings in frames if len(rings) != 1]
        self.assertEqual(missing, [], "frames without the ring (mouse y, box top)")
        self.assertTrue(all(rings[0][0] < 1.0 for _y, _t, rings in frames))   # the faint hover ring

    def test_the_focus_ring_stays_while_the_edge_is_dragged(self):
        self.surface.box.focused = True
        _x, _y, frames = self._drag([3.0] * 30 + [-3.0] * 30)
        self.assertTrue(all(len(rings) == 1 and rings[0][0] >= 0.99 for _y, _t, rings in frames))

    def test_after_the_drag_the_ring_follows_the_mouse_again(self):
        x, y, _frames = self._drag([3.0] * 20)
        top = self.surface.box.rect.top
        self.surface.on_release(x, top + 40.0)                # let go well above the box
        self.assertFalse(self.surface.box.resizing)
        self.assertEqual(_frame(self.surface), [])             # not hovered: no ring
        self.surface.on_move(x, top - 20.0)                   # back over the box
        self.assertEqual(len(_frame(self.surface)), 1)


if __name__ == "__main__":
    unittest.main()
