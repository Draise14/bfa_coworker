# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for long messages in the chat's input (#389): ui_gpu/input_box.py, surface.py
and input_capture.py, with a fixed-width stand-in font (and a stand-in ``bpy`` for
the input modal).

1. Scrolling: text taller than the box scrolls inside it; wrapped lines keep their
   offsets at any scroll; the caret is brought into view after typing, moving,
   selecting with the keyboard, pasting, undo and a drag past the top or bottom
   edge, and nothing else moves the view a wheel left.
2. The wheel: over the input it scrolls the input while its text is taller than
   the box (even at an end), else the chat; Ctrl+wheel still zooms.
3. The top edge: a hit strip with an up-down cursor; a drag sets the most lines
   shown, between the minimum and about half the chat; the box grows with its text
   up to that; a click without a drag changes nothing; Ctrl+Up / Ctrl+Down step it.
4. Remembered: every change reaches the host (``on_lines``), once per new value,
   and the box shows what the host gives it back.
5. Mouse selection in the prompt (#451) still works.

Run with::

    python -m unittest discover -s tests -p test_input_resize.py -v
"""

__all__ = ()

import importlib
import types
import unittest

from test_autosave_gap import _fake_bpy, _load
from test_text_selection import _Clock, _Context, _event, _Region

_PKG = "bfacw_autosave_gap_pkg"


def _modules():
    _load(_fake_bpy())          # makes the package (and input_capture, with a stand-in bpy)
    return [importlib.import_module(_PKG + ".ui_gpu." + m) for m in ("surface", "layout", "style", "input_box")]


sf, lay, st, ib = _modules()
RECT = lay.Rect(0, 0, 500, 600)
FORTY = "\n".join("Line {:02d} of the message".format(i + 1) for i in range(40))


def _canvas(surface):
    return lay.Canvas(st.Style(), lay.fixed_measure(0.55), surface.interaction())


def _draw(surface, history=None):
    surface.draw(_canvas(surface), RECT, history if history is not None else [])


def _line_no(box, offset):
    return box.model.text[:offset].count("\n") + 1


def _shown(box):
    """The text lines (1-based, by newline) on screen in the last draw."""
    return [_line_no(box, row[3]) for row in box._drawn]


def _caret_shown(box):
    return _line_no(box, box.model.cursor) in _shown(box)


def _key(box, kind, **kw):
    return box.handle_key(ib.Key(kind, **kw))


def _hit(surface, widget):
    return next(r for r, w in surface._hits if w == widget)


class _Surface(unittest.TestCase):

    def setUp(self):
        self.surface = sf.ChatSurface(clock=_Clock())
        self.box = self.surface.box
        self.box.focused = True
        self.remembered = []
        self.box.on_lines = self.remembered.append
        _draw(self.surface)

    def fill(self, text=FORTY):
        self.box.model.set_text(text)
        _draw(self.surface)


class TestScroll(_Surface):

    def test_a_long_message_scrolls_inside_the_box(self):
        self.fill()
        self.assertEqual(len(self.box._drawn), ib._MAX_VISIBLE_LINES)
        self.assertEqual(_shown(self.box)[-1], 40)                  # the caret's end
        self.assertTrue(self.box.can_scroll())
        self.box.scroll_by(-100)
        _draw(self.surface)
        self.assertEqual(_shown(self.box)[0], 1)                    # all the way up: line 1 is reachable
        self.assertFalse(_caret_shown(self.box))                    # the wheel leaves the caret where it is
        _draw(self.surface)
        self.assertEqual(_shown(self.box)[0], 1)                    # ... and another frame doesn't jump back

    def test_wrapped_lines_keep_their_offsets_when_scrolled(self):
        self.fill(" ".join("word{:d}".format(i) for i in range(400)))
        self.assertTrue(self.box.can_scroll())
        for lines in (-3, -7, -100):
            self.box.scroll_by(lines)
            _draw(self.surface)
            for _top, _bottom, _x0, start, piece in self.box._drawn:
                self.assertEqual(self.box.model.text[start:start + len(piece)], piece)

    def test_scroll_stays_in_range(self):
        self.fill()
        self.box.scroll_by(1000)
        self.assertEqual(self.box.scroll, 40 - ib._MAX_VISIBLE_LINES)
        self.box.scroll_by(-1000)
        self.assertEqual(self.box.scroll, 0)
        self.fill("short")
        self.assertEqual(self.box.scroll, 0)
        self.assertFalse(self.box.scroll_by(-3))                    # nothing to scroll

    def test_caret_comes_into_view_after_each_kind_of_edit(self):
        self.fill()
        box = self.box

        def away():                       # the wheel takes the view away from the caret first
            box.scroll_by(-100 if _line_no(box, box.model.cursor) > 20 else 100)
            _draw(self.surface)
            self.assertFalse(_caret_shown(box))

        away()
        _key(box, "A", unicode="a")                                  # typing
        _draw(self.surface)
        self.assertTrue(_caret_shown(box))
        away()
        _key(box, "HOME", ctrl=True)                                 # moving
        _draw(self.surface)
        self.assertEqual(_shown(box)[0], 1)
        away()
        for _ in range(9):
            _key(box, "DOWN_ARROW", shift=True)                      # selecting with the keyboard
        _draw(self.surface)
        self.assertTrue(_caret_shown(box))
        self.assertEqual(_line_no(box, box.model.cursor), 10)
        away()
        box.paste("pasted\ntext")                                    # pasting
        _draw(self.surface)
        self.assertTrue(_caret_shown(box))
        away()
        _key(box, "Z", ctrl=True)                                    # undo
        _draw(self.surface)
        self.assertTrue(_caret_shown(box))
        away()
        _key(box, "BACK_SPACE")                                      # deleting
        _draw(self.surface)
        self.assertTrue(_caret_shown(box))

    def test_moving_inside_the_view_doesnt_scroll(self):
        self.fill()
        before = self.box.scroll
        _key(self.box, "UP_ARROW")
        _draw(self.surface)
        self.assertEqual(self.box.scroll, before)                    # still on screen: no jump
        for _ in range(ib._MAX_VISIBLE_LINES - 1):
            _key(self.box, "UP_ARROW")
        _draw(self.surface)
        self.assertEqual(self.box.scroll, before - 1)                # one line past the top: one line

    def test_a_drag_past_the_top_edge_scrolls_up(self):
        self.fill()
        s, box = self.surface, self.box
        top, bottom, x0, _start, _piece = box._drawn[-1]
        s.on_press(x0 + 30, (top + bottom) / 2)
        first = _shown(box)[0]
        y = box.rect.top + 10
        for i in range(5):
            s.on_move(x0 + 30, y + i)
            _draw(s)
        self.assertLess(_shown(box)[0], first)                       # the view followed up
        self.assertTrue(_caret_shown(box))
        s.on_release(x0 + 30, y + 4)
        sel = box.model.selection
        self.assertIsNotNone(sel)
        self.assertEqual(_line_no(box, sel[1]), 40)                  # anchored where the press was
        self.assertEqual(_line_no(box, sel[0]), _shown(box)[0])

    def test_a_drag_below_the_text_scrolls_down(self):
        self.fill()
        s, box = self.surface, self.box
        box.scroll_by(-100)
        _draw(s)
        top, bottom, x0, _start, _piece = box._drawn[0]
        s.on_press(x0 + 30, (top + bottom) / 2)
        for i in range(5):
            s.on_move(x0 + 30, box.rect.y - 10 - i)
            _draw(s)
        self.assertGreater(_shown(box)[0], 1)
        self.assertTrue(_caret_shown(box))
        s.on_release(x0 + 30, box.rect.y - 15)
        self.assertEqual(_line_no(box, box.model.selection[0]), 1)

    def test_scroll_hint_only_when_it_scrolls(self):
        def hints():
            canvas = _canvas(self.surface)
            self.surface.draw(canvas, RECT, [])
            item = canvas.style.scroll.item
            return [c for c in canvas.commands if c.kind == "rect" and c.color[:3] == item[:3]
                    and self.box.rect.contains(c.rect.x, c.rect.y)]
        self.fill("two\nlines")
        self.assertEqual(hints(), [])
        self.fill()
        self.assertEqual(len(hints()), 1)


class TestWheel(_Surface):

    def wheel(self, up, ctrl=False):
        rect = self.box.rect
        return self.surface.on_wheel(up, ctrl, rect.x + rect.w / 2, rect.top - 5)

    def long_chat(self):
        return [{"role": "user", "content": "message {:d}\n\nwith more".format(i)} for i in range(60)]

    def test_over_a_long_input_the_input_scrolls(self):
        history = self.long_chat()
        self.box.model.set_text(FORTY)
        _draw(self.surface, history)
        before = self.box.scroll
        self.assertTrue(self.wheel(up=True))
        self.assertEqual(self.box.scroll, before - sf._WHEEL_LINES)
        self.assertEqual(self.surface.view.scroll, 0.0)              # the chat stayed
        self.box.scroll_by(-1000)
        self.assertTrue(self.wheel(up=True))                         # at its top: still the input's
        self.assertEqual(self.surface.view.scroll, 0.0)

    def test_over_a_short_input_the_chat_scrolls(self):
        history = self.long_chat()
        self.box.model.set_text("short")
        _draw(self.surface, history)
        self.assertTrue(self.wheel(up=True))
        self.assertGreater(self.surface.view.scroll, 0.0)

    def test_ctrl_wheel_over_the_input_zooms(self):
        self.fill()
        self.assertTrue(self.wheel(up=True, ctrl=True))
        self.assertGreater(self.surface.zoom, 1.0)

    def test_no_input_on_screen_no_wheel_for_it(self):
        self.fill()
        rect = self.box.rect
        notice = sf.Notice("Harness", "INFO", ("Runs elsewhere",))
        self.surface.draw(_canvas(self.surface), RECT, [], notice=notice)
        self.assertIsNone(self.box.rect)
        scroll = self.box.scroll
        self.surface.on_wheel(True, False, rect.x + 5, rect.y + 5)
        self.assertEqual(self.box.scroll, scroll)                    # the hidden input doesn't scroll


class TestTopEdge(_Surface):

    def drag_edge(self, dy, steps=4):
        s, box = self.surface, self.box
        edge = _hit(s, "input_resize")
        x, y = edge.x + edge.w / 2, edge.y + edge.h / 2
        self.assertEqual(s.hit(x, y), "input_resize")
        s.on_press(x, y)
        for i in range(1, steps + 1):
            s.on_move(x, y + dy * i / steps)
            _draw(s)
        s.on_release(x, y + dy)
        _draw(s)

    def test_hit_strip_and_cursor_on_the_top_edge(self):
        s = self.surface
        edge = _hit(s, "input_resize")
        self.assertAlmostEqual(edge.y + edge.h / 2, self.box.rect.top)
        self.assertLessEqual(edge.h, 12)
        self.assertEqual(s.cursor_at(edge.x + 20, edge.y + edge.h / 2), sf._RESIZE_CURSOR)
        self.assertEqual(sf._RESIZE_CURSOR, "MOVE_Y")
        self.assertEqual(s.cursor_at(edge.x + 20, self.box.rect.top - 20), "TEXT")     # the text below it
        self.assertTrue(sf.tip_for("input_resize"))

    def test_drag_up_makes_it_taller_and_tells_the_host(self):
        self.fill()
        lh = st.Style().line_height
        h0 = self.box.rect.h
        self.drag_edge(lh * 4)
        self.assertEqual(self.box.max_lines, ib._MAX_VISIBLE_LINES + 4)
        self.assertEqual(len(self.box._drawn), ib._MAX_VISIBLE_LINES + 4)
        self.assertAlmostEqual(self.box.rect.h, h0 + 4 * lh)
        self.assertEqual(self.remembered[-1], ib._MAX_VISIBLE_LINES + 4)
        self.assertEqual(len(self.remembered), len(set(self.remembered)))     # once per new value
        self.assertTrue(_caret_shown(self.box))
        self.assertIsNone(self.surface.pressed)
        self.assertFalse(self.box.resizing)

    def test_limits_minimum_and_half_the_chat(self):
        self.fill()
        self.drag_edge(5000)
        cap = self.box.line_cap
        self.assertEqual(self.box.max_lines, cap)
        self.assertLessEqual(self.box.rect.h, RECT.h * 0.5 + 1)       # about half the chat at most
        self.assertGreater(self.box.rect.h, RECT.h * 0.4)
        self.drag_edge(-5000)
        self.assertEqual(self.box.max_lines, ib._MIN_VISIBLE_LINES)
        self.assertEqual(len(self.box._drawn), ib._MIN_VISIBLE_LINES)
        self.assertTrue(_caret_shown(self.box))

    def test_grows_with_its_text_up_to_the_chosen_size(self):
        self.fill()
        self.drag_edge(st.Style().line_height * 6)              # from the 6 lines it shows
        self.assertEqual(self.box.max_lines, 12)
        self.fill("one\ntwo\nthree")
        self.assertEqual(len(self.box._drawn), 3)                    # as tall as its text
        self.fill("\n".join(str(i) for i in range(9)))
        self.assertEqual(len(self.box._drawn), 9)
        self.fill()
        self.assertEqual(len(self.box._drawn), 12)                   # then it scrolls

    def test_a_click_on_the_edge_changes_nothing(self):
        self.fill("short")
        edge = _hit(self.surface, "input_resize")
        x, y = edge.x + edge.w / 2, edge.y + edge.h / 2
        h0 = self.box.rect.h
        self.surface.on_press(x, y)
        _draw(self.surface)
        self.assertAlmostEqual(self.box.rect.h, h0)                  # no jump on press
        self.surface.on_release(x, y)
        _draw(self.surface)
        self.assertEqual(self.remembered, [])
        self.assertEqual(self.box.max_lines, ib._MAX_VISIBLE_LINES)

    def test_a_drag_on_the_edge_keeps_the_chats_selection_and_the_prompt(self):
        self.fill("hello world")
        self.box.model.select_all()
        self.drag_edge(st.Style().line_height * 2)
        self.assertEqual(self.box.model.selected_text(), "hello world")   # the edge doesn't place the caret
        self.assertTrue(self.box.focused)

    def test_ctrl_up_and_down_step_it(self):
        self.fill()
        self.assertTrue(_key(self.box, "UP_ARROW", ctrl=True))
        self.assertEqual(self.box.max_lines, ib._MAX_VISIBLE_LINES + 1)
        self.assertEqual(self.box.model.cursor, len(FORTY))          # the caret didn't move
        _key(self.box, "DOWN_ARROW", ctrl=True)
        _key(self.box, "DOWN_ARROW", ctrl=True)
        self.assertEqual(self.box.max_lines, ib._MAX_VISIBLE_LINES - 1)
        self.assertEqual(self.remembered, [7, 6, 5])
        for _ in range(20):
            _key(self.box, "DOWN_ARROW", ctrl=True)
        self.assertEqual(self.box.max_lines, ib._MIN_VISIBLE_LINES)
        for _ in range(200):
            _key(self.box, "UP_ARROW", ctrl=True)
        self.assertEqual(self.box.max_lines, self.box.line_cap)      # never past the cap

    def test_ctrl_down_shrinks_from_what_shows(self):
        self.box.max_lines = 20
        self.fill("one\ntwo\nthree\nfour")
        _key(self.box, "DOWN_ARROW", ctrl=True)
        self.assertEqual(self.box.max_lines, 3)                      # at once, not 16 presses later

    def test_plain_arrows_still_move(self):
        self.fill()
        _key(self.box, "UP_ARROW")
        self.assertEqual(_line_no(self.box, self.box.model.cursor), 39)
        self.assertEqual(self.box.max_lines, ib._MAX_VISIBLE_LINES)


class TestRemembered(unittest.TestCase):

    def test_the_box_shows_what_the_host_keeps(self):
        kept = {"lines": 9}
        s = sf.ChatSurface(clock=_Clock())
        s.box.on_lines = lambda n: kept.update(lines=n)
        s.box.model.set_text(FORTY)
        for _ in range(3):                    # the host sets it before every draw, like chat_editor
            s.box.max_lines = kept["lines"]
            _draw(s)
            self.assertEqual(len(s.box._drawn), 9)
        s.box.focused = True
        _key(s.box, "UP_ARROW", ctrl=True)
        self.assertEqual(kept["lines"], 10)
        s.box.max_lines = kept["lines"]
        _draw(s)
        self.assertEqual(len(s.box._drawn), 10)

    def test_a_kept_size_too_big_for_this_chat_is_capped_not_changed(self):
        kept = {"lines": 60}
        s = sf.ChatSurface(clock=_Clock())
        s.box.on_lines = lambda n: kept.update(lines=n)
        s.box.max_lines = kept["lines"]
        s.box.model.set_text(FORTY)
        _draw(s)
        self.assertEqual(len(s.box._drawn), s.box.line_cap)
        self.assertLess(s.box.line_cap, 40)
        self.assertEqual(kept["lines"], 60)                          # a taller window gets it back

    def test_compact_and_full_surfaces_cap_by_their_own_height(self):
        full, compact = sf.ChatSurface(clock=_Clock()), sf.ChatSurface(compact=True, clock=_Clock())
        for s in (full, compact):
            s.box.max_lines = 60
            s.box.model.set_text(FORTY)
        full.draw(_canvas(full), lay.Rect(0, 0, 500, 900), [])
        compact.draw(_canvas(compact), lay.Rect(0, 0, 250, 400), [])
        self.assertGreater(full.box.line_cap, compact.box.line_cap)


class TestPromptSelectionStillWorks(_Surface):

    def test_drag_selects_and_double_click_selects_a_word(self):
        self.fill("hello wonderful world")
        s, box = self.surface, self.box
        top, bottom, x0, _start, _piece = box._drawn[0]
        cw = 0.55 * st.Style().font_size
        y = (top + bottom) / 2
        s.on_press(x0 + 0.1, y)
        s.on_move(x0 + cw * 15, y)
        s.on_release(x0 + cw * 15, y)
        self.assertEqual(box.model.selected_text(), "hello wonderful")
        s.clock.t += 5
        s.on_press(x0 + cw * 18, y)
        s.on_release(x0 + cw * 18, y)
        s.on_press(x0 + cw * 18, y)
        s.on_release(x0 + cw * 18, y)
        self.assertEqual(box.model.selected_text(), "world")

    def test_click_on_a_scrolled_line_places_the_caret_there(self):
        self.fill()
        box = self.box
        box.scroll_by(-100)
        _draw(self.surface)
        top, bottom, x0, _start, _piece = box._drawn[2]
        self.surface.on_press(x0 + 1, (top + bottom) / 2)
        self.surface.on_release(x0 + 1, (top + bottom) / 2)
        _draw(self.surface)
        self.assertEqual(_line_no(box, box.model.cursor), 3)
        self.assertEqual(_shown(box)[0], 1)                          # no jump


class TestModalCursor(unittest.TestCase):

    def setUp(self):
        self.bpy = _fake_bpy()
        self.ic = _load(self.bpy)
        self.sf = importlib.import_module(_PKG + ".ui_gpu.surface")
        self.region, self.area = _Region(), object()
        self.surface = self.sf.ChatSurface(clock=_Clock())
        self.surface.box.model.set_text(FORTY)
        self._draw()
        self.surface.bounds = RECT
        self.ic.set_host(lambda _c, _e: (self.surface, self.area, self.region), on_action=lambda *_a: None)
        self.context = _Context(self.bpy.context.window_manager)
        self.op = self.ic.BFACW_OT_gpu_input()

    def tearDown(self):
        self.ic.set_host(None)

    def _draw(self):
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), self.surface.interaction())
        self.surface.draw(canvas, RECT, [])

    def _send(self, kind, value, xy, **kw):
        x, y = xy
        return self.op._handle(self.context, _event(kind, value, x + _Region.x, y + _Region.y, **kw),
                               self.ic._HOST["router"])

    def test_resize_cursor_on_the_edge_and_through_the_drag(self):
        edge = _hit(self.surface, "input_resize")
        on_edge = (edge.x + 40, edge.y + edge.h / 2)
        self._send("MOUSEMOVE", "NOTHING", on_edge)
        self.assertEqual(self.context.cursors[-1:], ["MOVE_Y"])
        self.assertEqual(self._send("LEFTMOUSE", "PRESS", on_edge), {"RUNNING_MODAL"})
        lh = st.Style().line_height
        for i in range(1, 4):
            self._send("MOUSEMOVE", "NOTHING", (on_edge[0], on_edge[1] + lh * i))
            self._draw()
        self.assertEqual(self.context.cursors[-1:], ["MOVE_Y"])      # kept while dragging
        self._send("LEFTMOUSE", "RELEASE", (on_edge[0], on_edge[1] + lh * 3))
        self.assertEqual(self.surface.box.max_lines, ib._MAX_VISIBLE_LINES + 3)
        self._draw()
        text = self.surface.box._drawn[1]
        self._send("MOUSEMOVE", "NOTHING", (text[2] + 10, (text[0] + text[1]) / 2))
        self.assertEqual(self.context.cursors[-1:], ["TEXT"])

    def test_wheel_over_the_long_input_is_used(self):
        rect = self.surface.box.rect
        before = self.surface.box.scroll
        self.assertEqual(self._send("WHEELUPMOUSE", "PRESS", (rect.x + 50, rect.top - 10)), {"RUNNING_MODAL"})
        self.assertLess(self.surface.box.scroll, before)


if __name__ == "__main__":
    unittest.main()
