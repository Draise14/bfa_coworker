# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for selecting text with the mouse (#451, #455): ui_gpu/selection.py, and
what layout.py, chat_view.py, surface.py, input_box.py and input_capture.py do
with it, with a fixed-width stand-in font (and a stand-in ``bpy`` for the input
modal).

1. The prompt: a drag selects (the box's own selection), a click only places the
   cursor, a double-click selects a word.
2. Hit testing: the nearest character boundary on a line, the nearest line in a
   gap, nothing outside the clip; wrapped lines keep their offsets in the text.
3. Messages: a press on a message starts a selection (and is used, the prompt
   loses the keyboard); a drag extends it across wrapped lines, blocks and
   turns; the copied text is the text as shown (no markdown or attachment
   markers, blocks a blank line apart, list items a line apart), also for a turn
   scrolled out of view; a double-click selects a word; the wheel scrolls while
   dragging and the end follows.
4. Drawing: the highlight sits behind the selected text in the theme's
   text-selection colour; heights don't change.
5. Lifetime: a click elsewhere or Esc clears it; it is dropped when its turn
   changes; a reply that still streams can't be selected, the Thinking box can.
6. Keys: Ctrl+C copies it through the host's copy (redaction) while no box has
   the keyboard and the mouse is over the chat; otherwise the key passes through.
   The right-click menu knows there is a selection.

Run with::

    python -m unittest discover -s tests -p test_text_selection.py -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

from test_autosave_gap import _fake_bpy, _load

_PKG = "bfacw_autosave_gap_pkg"
_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"

LONG = ("This paragraph is long enough to wrap over several lines in a chat that is five hundred "
        "pixels wide, with **bold** words and `inline code` in it, so a selection can start on one "
        "line and end on another one further down without losing a single space between words.")


def _modules():
    _load(_fake_bpy())          # makes the package (and input_capture, with a stand-in bpy)
    return [importlib.import_module(_PKG + ".ui_gpu." + m)
            for m in ("surface", "layout", "style", "chat_view", "selection", "input_box")]


sf, lay, st, cv, sel, ib = _modules()
RECT = lay.Rect(0, 0, 500, 600)


def _history():
    return [
        {"role": "user", "content": "Make a campfire [attached: ref.png]"},
        {"role": "assistant", "content": "## Campfire\n\n" + LONG + "\n\n- first item\n- second item\n\n"
                                         "```python\nimport bpy\nprint('fire')\n```"},
        {"role": "user", "content": "Thanks a lot!"},
        {"role": "assistant", "content": "You're welcome."},
    ]


def _draw(surface, history=None, live=None):
    canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), surface.interaction())
    actions = surface.draw(canvas, RECT, _history() if history is None else history, live=live)
    return canvas, actions


def _lines(surface, turn, unit):
    return sorted((ln for ln in surface._lines if ln.key == (turn, unit)), key=lambda ln: -ln.top)


def _at(surface, line, offset):
    """(x, y) of the caret before *offset* on *line*, mid-height."""
    return sel.x_at(line, offset, surface._measure), (line.top + line.bottom) / 2


def _drag(surface, start, end, history=None, live=None):
    surface.on_press(*start)
    surface.on_move(*end)
    _draw(surface, history, live)
    surface.on_release(*end)
    return _draw(surface, history, live)


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _surface():
    surface = sf.ChatSurface(clock=_Clock())
    _draw(surface)
    return surface


class TestPrompt(unittest.TestCase):

    def setUp(self):
        self.surface = _surface()
        self.box = self.surface.box
        self.box.model.set_text("hello wonderful world")
        _draw(self.surface)

    def _x(self, chars):
        top, bottom, x0, _start, piece = self.box._drawn[0]
        return x0 + self.box._measure(piece[:chars], "ui", self.box._size), (top + bottom) / 2

    def test_drag_selects(self):
        self.assertTrue(self.surface.on_press(*self._x(0)))
        self.assertEqual(self.surface.pressed, "input")
        self.surface.on_move(*self._x(len("hello wonderful")))
        self.assertEqual(self.box.model.selection, (0, len("hello wonderful")))
        self.assertTrue(self.surface.on_release(*self._x(len("hello wonderful"))))
        self.assertIsNone(self.surface.pressed)
        self.assertEqual(self.box.model.selected_text(), "hello wonderful")
        self.assertTrue(self.box.focused)
        self.assertEqual(_draw(self.surface)[1], [])                   # a drag is no click

    def test_click_only_places_the_cursor(self):
        x, y = self._x(5)
        self.surface.on_press(x, y)
        self.surface.on_move(x + 1, y)                                 # a little jitter: no drag
        self.surface.on_release(x + 1, y)
        self.assertIsNone(self.box.model.selection)
        self.assertEqual(self.box.model.cursor, 5)

    def test_double_click_selects_a_word(self):
        x, y = self._x(len("hello wonderful wo"))
        self.surface.on_press(x, y)
        self.surface.on_release(x, y)
        self.surface.clock.t += 0.2                                    # soon after: a double-click
        self.surface.on_press(x, y)
        self.surface.on_move(x + 1, y)
        self.surface.on_release(x + 1, y)
        self.assertEqual(self.box.model.selected_text(), "world")

    def test_double_click_from_the_host(self):
        x, y = self._x(2)
        self.surface.on_press(x, y, double=True)
        self.assertEqual(self.box.model.selected_text(), "hello")

    def test_presses_far_apart_in_time_are_two_clicks(self):
        x, y = self._x(3)
        self.surface.on_press(x, y)
        self.surface.on_release(x, y)
        self.surface.clock.t += 2.0
        self.surface.on_press(x, y)
        self.assertIsNone(self.box.model.selection)

    def test_word_bounds(self):
        self.assertEqual(sel.word_bounds("hello wonderful world", 8), (6, 15))
        self.assertEqual(sel.word_bounds("hello wonderful world", 6), (6, 15))     # at its start
        self.assertEqual(sel.word_bounds("hello wonderful world", 15), (6, 15))    # at its end
        self.assertEqual(sel.word_bounds("a   b", 2), (1, 4))                       # the spaces
        self.assertEqual(sel.word_bounds("", 0), (0, 0))


class TestHitTest(unittest.TestCase):

    def setUp(self):
        self.measure = lay.fixed_measure(0.5)
        self.canvas = lay.Canvas(st.Style(), self.measure)
        self.canvas.push_clip(lay.Rect(0, 0, 400, 400))
        self.text = "one two three four five six seven eight nine ten"
        self.canvas.wrapped(10, 300, 100, self.text, unit=(0, (2, 0)))
        self.lines = self.canvas.text_lines

    def test_wrapped_lines_keep_their_offsets(self):
        self.assertGreater(len(self.lines), 2)
        for line in self.lines:
            self.assertEqual(self.text[line.start:line.end], line.pieces[0][1])
        self.assertEqual([(o, t) for o, t in lay.wrap_offsets(self.text, 100, self.measure)],
                         [(ln.start, ln.pieces[0][1]) for ln in self.lines])
        self.assertEqual([t for _o, t in lay.wrap_offsets("a\n\nb", 100, self.measure)],
                         lay.wrap_text("a\n\nb", 100, self.measure))

    def test_nearest_boundary_on_the_line(self):
        line = self.lines[1]
        for k in range(len(line.pieces[0][1]) + 1):
            x, y = sel.x_at(line, line.start + k, self.measure), (line.top + line.bottom) / 2
            self.assertEqual(sel.point_to_pos(self.lines, x + 1, y, self.measure), (0, (2, 0), line.start + k))
        y = (line.top + line.bottom) / 2
        self.assertEqual(sel.point_to_pos(self.lines, -50, y, self.measure)[2], line.start)     # left of it
        self.assertEqual(sel.point_to_pos(self.lines, 900, y, self.measure)[2], line.end)       # right of it

    def test_nearest_line_above_and_below(self):
        first, last = self.lines[0], self.lines[-1]
        self.assertEqual(sel.point_to_pos(self.lines, 10, first.top + 50, self.measure)[2], first.start)
        self.assertEqual(sel.point_to_pos(self.lines, 900, last.bottom - 50, self.measure)[2], len(self.text))

    def test_nothing_outside_the_clip(self):
        canvas = lay.Canvas(st.Style(), self.measure)
        canvas.push_clip(lay.Rect(0, 0, 400, 100))
        canvas.wrapped(10, 300, 100, "hidden text", unit=(0, (2, 0)))
        self.assertIsNone(sel.point_to_pos(canvas.text_lines, 20, 290, self.measure))
        self.assertFalse(sel.over_text(canvas.text_lines, 20, 290))

    def test_without_a_unit_no_lines(self):
        canvas = lay.Canvas(st.Style(), self.measure)
        canvas.wrapped(10, 300, 100, self.text)
        self.assertEqual(canvas.text_lines, [])


class TestMessages(unittest.TestCase):

    def setUp(self):
        self.surface = _surface()

    def test_units_are_the_text_as_shown(self):
        units = cv.unit_texts(cv.group_turns(_history())[0])
        texts = {unit: text for unit, text, _k in units}
        self.assertEqual(texts[(cv.USER, 0)], "Make a campfire")          # no attachment marker
        self.assertEqual(texts[(cv.REPLY, 0)], "Campfire")                # no "##"
        self.assertIn("with bold words and inline code in it", texts[(cv.REPLY, 1)])
        self.assertEqual(texts[(cv.REPLY, 4)], "import bpy\nprint('fire')")
        self.assertEqual(cv.unit_texts(cv.group_turns(_history())[0], active=True),
                         [((cv.USER, 0), "Make a campfire", "user")])     # no reply while it runs

    def test_lines_match_the_units(self):
        units = {unit: text for unit, text, _k in cv.unit_texts(cv.group_turns(_history())[0])}
        para = _lines(self.surface, 0, (cv.REPLY, 1))
        self.assertGreaterEqual(len(para), 3)                               # it wraps
        for line in para:
            shown = "".join(p[1] for p in line.pieces)
            self.assertEqual(units[(cv.REPLY, 1)][line.start:line.end], shown)

    def test_press_on_a_message_is_used_and_takes_the_keyboard_from_the_prompt(self):
        self.surface.box.focused = True
        line = _lines(self.surface, 0, (cv.REPLY, 1))[0]
        self.assertTrue(self.surface.on_press(*_at(self.surface, line, line.start + 3)))
        self.assertEqual(self.surface.pressed, "select")
        self.assertFalse(self.surface.box.focused)
        self.surface.on_release(*_at(self.surface, line, line.start + 3))
        self.assertFalse(self.surface.has_selection())                  # a click selects nothing
        self.assertIsNone(self.surface.view.selection)

    def test_drag_across_wrapped_lines(self):
        para = _lines(self.surface, 0, (cv.REPLY, 1))
        units = {unit: text for unit, text, _k in cv.unit_texts(cv.group_turns(_history())[0])}
        a, b = para[0].start + 5, para[2].start + 7
        _drag(self.surface, _at(self.surface, para[0], a), _at(self.surface, para[2], b))
        self.assertEqual(self.surface.view.selection.ordered(), ((0, (cv.REPLY, 1), a), (0, (cv.REPLY, 1), b)))
        text = self.surface.selected_text()
        self.assertEqual(text, units[(cv.REPLY, 1)][a:b])
        self.assertNotIn("  ", text)
        self.assertNotIn("*", text)
        self.assertEqual(len(text.split()), len(units[(cv.REPLY, 1)][a:b].split()))   # no lost spaces

    def test_drag_upwards_is_the_same_selection(self):
        para = _lines(self.surface, 0, (cv.REPLY, 1))
        down = (_at(self.surface, para[0], para[0].start + 2), _at(self.surface, para[1], para[1].start + 4))
        _drag(self.surface, *down)
        first = self.surface.selected_text()
        self.surface.clock.t += 5
        _drag(self.surface, down[1], down[0])
        self.assertEqual(self.surface.selected_text(), first)

    def test_across_blocks_and_turns(self):
        heading = _lines(self.surface, 0, (cv.REPLY, 0))[0]
        item = _lines(self.surface, 0, (cv.REPLY, 3))[0]
        _drag(self.surface, _at(self.surface, heading, 0), _at(self.surface, item, item.end))
        text = self.surface.selected_text()
        self.assertTrue(text.startswith("Campfire\n\nThis paragraph is long"))
        self.assertTrue(text.endswith("without losing a single space between words.\n\nfirst item\nsecond item"))
        self.surface.clock.t += 5
        code = _lines(self.surface, 0, (cv.REPLY, 4))[1]
        mine = _lines(self.surface, 1, (cv.USER, 0))[0]
        _drag(self.surface, _at(self.surface, code, code.start + 5), _at(self.surface, mine, mine.start + 6))
        self.assertEqual(self.surface.selected_text(), "('fire')\n\nThanks")

    def test_copy_spans_a_turn_scrolled_out_of_view(self):
        long = _history() + [m for i in range(12) for m in ({"role": "user", "content": "q {:d}".format(i)},
                                                           {"role": "assistant", "content": "answer {:d}".format(i)})]
        surface = sf.ChatSurface(clock=_Clock())
        _draw(surface, long)
        surface.view.scroll = surface.view.content_height          # the top: turn 0 on screen
        _draw(surface, long)
        mine = _lines(surface, 0, (cv.USER, 0))[0]
        surface.on_press(*_at(surface, mine, 5))
        self.assertEqual(surface.view.selection.anchor, (0, (cv.USER, 0), 5))
        surface.view.extend((3, (cv.USER, 0), 3))                     # as a drag down to "q 1" would
        surface.on_release(*_at(surface, mine, 5))
        surface.view.scroll = 0.0                                      # turn 0 now off screen
        _draw(surface, long)
        self.assertFalse(_lines(surface, 0, (cv.USER, 0)))
        self.assertEqual(surface.selected_text().split("\n\n")[:2], ["a campfire", "Campfire"])
        self.assertTrue(surface.selected_text().endswith("answer 0\n\nq 1"))

    def test_double_click_selects_a_word(self):
        line = _lines(self.surface, 0, (cv.REPLY, 1))[0]
        where = _at(self.surface, line, line.start + 6)                 # in "paragraph"
        self.surface.on_press(*where)
        self.surface.on_release(*where)
        self.surface.clock.t += 0.1
        self.surface.on_press(*where)
        self.surface.on_release(*where)
        self.assertEqual(self.surface.selected_text(), "paragraph")

    def test_wheel_while_dragging_moves_the_end(self):
        long = [m for i in range(30) for m in ({"role": "user", "content": "question number {:d}".format(i)},
                                              {"role": "assistant", "content": "answer {:d}".format(i)})]
        surface = sf.ChatSurface(clock=_Clock())
        _draw(surface, long)
        last = _lines(surface, 29, (cv.USER, 0))[0]
        start = _at(surface, last, 3)
        surface.on_press(*start)
        surface.on_move(start[0] + 40, start[1])
        _draw(surface, long)
        before = surface.view.selection.focus
        self.assertTrue(surface.on_wheel(True, False, *surface.mouse))   # scroll up under the mouse
        _draw(surface, long)
        self.assertLess(surface.view.selection.focus, before)          # the end is now older text
        surface.on_release(*surface.mouse)
        self.assertTrue(surface.has_selection())

    def test_press_on_a_button_or_empty_chat_clears_it(self):
        para = _lines(self.surface, 0, (cv.REPLY, 1))
        _drag(self.surface, _at(self.surface, para[0], 1), _at(self.surface, para[1], 3))
        self.assertTrue(self.surface.has_selection())
        rect = next(r for r, w in self.surface._hits if w == "scroll_track") if any(
            w == "scroll_track" for _r, w in self.surface._hits) else None
        if rect is not None:            # the scrollbar keeps it: you scroll to see more
            self.surface.on_press(rect.x + 1, rect.y + 1)
            self.surface.on_release(rect.x + 1, rect.y + 1)
            self.assertTrue(self.surface.has_selection())
        self.assertFalse(self.surface.on_press(250, 595))               # empty chat: not used
        self.assertFalse(self.surface.has_selection())

    def test_click_in_the_prompt_clears_it(self):
        para = _lines(self.surface, 0, (cv.REPLY, 1))
        _drag(self.surface, _at(self.surface, para[0], 1), _at(self.surface, para[1], 3))
        rect = next(r for r, w in self.surface._hits if w == "input")
        self.surface.on_press(rect.x + 20, rect.top - 10)
        self.assertFalse(self.surface.has_selection())
        self.assertTrue(self.surface.box.focused)


class TestDrawing(unittest.TestCase):

    def test_highlight_behind_the_text_and_heights_unchanged(self):
        surface = _surface()
        height = surface.view.content_height
        para = _lines(surface, 0, (cv.REPLY, 1))
        canvas, _ = _drag(surface, _at(surface, para[0], para[0].start + 5), _at(surface, para[1], para[1].start + 3))
        style = canvas.style
        marks = [n for n, c in enumerate(canvas.commands) if c.kind == "rect" and c.color == style.selection]
        self.assertEqual(len(marks), 2)                                 # one per selected line
        texts = [n for n, c in enumerate(canvas.commands) if c.kind == "text"]
        for n in marks:
            nxt = next(i for i in texts if i > n)
            line_y = canvas.commands[n].rect
            self.assertLessEqual(line_y.y, canvas.commands[nxt].rect.y)      # the line's own text comes next
        self.assertEqual(surface.view.content_height, height)

    def test_selection_colour_is_the_text_fields(self):
        style = st.Style()
        self.assertEqual(style.selection, style.text_field.item)

    def test_your_message_gets_a_highlight_its_box_doesnt_hide(self):
        surface = _surface()
        mine = _lines(surface, 1, (cv.USER, 0))[0]
        canvas, _ = _drag(surface, _at(surface, mine, 0), _at(surface, mine, 6))
        marks = [c for c in canvas.commands if c.kind == "rect" and c.rect.h == mine.top - mine.bottom
                 and abs(c.rect.y - mine.bottom) < 0.01]
        self.assertEqual(len(marks), 1)
        self.assertNotEqual(marks[0].color, canvas.style.button.inner_sel)

    def test_inline_code_highlight_over_its_own_background(self):
        surface = _surface()
        line = next(ln for ln in _lines(surface, 0, (cv.REPLY, 1)) if any(p[6] >= 0 for p in ln.pieces))
        canvas, _ = _drag(surface, _at(surface, line, line.start), _at(surface, line, line.end))
        style = canvas.style
        code = next(p for p in line.pieces if p[6] >= 0)
        marks = [c for c in canvas.commands if c.kind == "rect" and c.color == style.selection
                 and abs(c.rect.x - (code[2] - sel.CHIP_PAD)) < 0.01]
        self.assertTrue(marks)                                          # its background's edge covered too


class TestLifetime(unittest.TestCase):

    def test_dropped_when_its_turn_changes(self):
        surface = _surface()
        mine = _lines(surface, 1, (cv.USER, 0))[0]
        _drag(surface, _at(surface, mine, 0), _at(surface, mine, 6))
        self.assertTrue(surface.has_selection())
        history = _history() + [{"role": "tool", "name": "x", "content": "ok"}]
        _draw(surface, history)
        self.assertFalse(surface.has_selection())

    def test_kept_while_an_older_turn_stays_the_same(self):
        surface = _surface()
        mine = _lines(surface, 0, (cv.USER, 0))[0]
        _drag(surface, _at(surface, mine, 0), _at(surface, mine, 4))
        history = _history() + [{"role": "user", "content": "another question"}]
        _draw(surface, history)
        self.assertEqual(surface.selected_text(), "Make")

    def test_streaming_reply_isnt_selectable_the_thinking_box_is(self):
        live = types.SimpleNamespace(is_thinking=True, streaming_text="Half a reply that streams in",
                                     reasoning_text="Thinking about the fire and the stones", thinking_dots=0,
                                     thinking_start_time=0.0, phase="", status_text="")
        history = _history()[:3]
        surface = sf.ChatSurface(clock=_Clock())
        _draw(surface, history, live)
        keys = {ln.key for ln in surface._lines if ln.key[0] == 1}
        self.assertNotIn(cv.REPLY, {k[1][0] for k in keys})
        think = _lines(surface, 1, (cv.THINK, 0))
        self.assertTrue(think)
        _drag(surface, _at(surface, think[0], 0), _at(surface, think[0], len("Thinking about")), history, live)
        self.assertEqual(surface.selected_text(), "Thinking about")
        live.reasoning_text = "Thinking anew"                          # the text it covered changed
        _draw(surface, history, live)
        self.assertFalse(surface.has_selection())

    def test_a_notice_has_no_selection(self):
        surface = _surface()
        mine = _lines(surface, 1, (cv.USER, 0))[0]
        _drag(surface, _at(surface, mine, 0), _at(surface, mine, 6))
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), surface.interaction())
        surface.draw(canvas, RECT, _history(), notice=sf.Notice("Harness", "INFO", ("line",)))
        self.assertFalse(surface.has_selection())


# -- the input modal (stand-in bpy) ------------------------------------------------------

class _Region:
    x, y = 100, 50

    def __init__(self):
        self.tagged = 0

    def tag_redraw(self):
        self.tagged += 1


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Context:
    def __init__(self, wm):
        self.window = types.SimpleNamespace(cursor_set=lambda c: self.cursors.append(c))
        self.window_manager = wm
        self.cursors = []

    def temp_override(self, **_kw):
        return _Null()


def _event(kind, value, x, y, **kw):
    base = dict(type=kind, value=value, mouse_x=x, mouse_y=y, shift=False, ctrl=False, alt=False,
                oskey=False, unicode="")
    base.update(kw)
    return types.SimpleNamespace(**base)


class TestInputModal(unittest.TestCase):

    def setUp(self):
        self.bpy = _fake_bpy()
        self.ic = _load(self.bpy)
        self.sf = importlib.import_module(_PKG + ".ui_gpu.surface")
        self.ic.bpy.ops.wm = types.SimpleNamespace(call_menu=lambda name: None)
        self.region, self.area = _Region(), object()
        self.surface = self.sf.ChatSurface(clock=_Clock())
        self._draw()
        self.surface.bounds = RECT
        self.over = True
        self.copied = []
        self.ic.set_host(lambda _c, _e: (self.surface, self.area, self.region) if self.over else None,
                         on_action=lambda box, action, value: self.copied.append((box, action, value)))
        self.context = _Context(self.bpy.context.window_manager)
        self.op = self.ic.BFACW_OT_gpu_input()

    def tearDown(self):
        self.ic.set_host(None)

    def _draw(self):
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), self.surface.interaction())
        self.surface.draw(canvas, RECT, _history())

    def _send(self, kind, value, local_xy, **kw):
        x, y = local_xy
        return self.op._handle(self.context, _event(kind, value, x + _Region.x, y + _Region.y, **kw),
                               self.ic._HOST["router"])

    def _select(self):
        para = _lines(self.surface, 0, (cv.REPLY, 1))
        a, b = _at(self.surface, para[0], para[0].start), _at(self.surface, para[0], para[0].start + 4)
        self.assertEqual(self._send("LEFTMOUSE", "PRESS", a), {"RUNNING_MODAL"})     # no box select below
        self._send("MOUSEMOVE", "NOTHING", b)
        self._send("LEFTMOUSE", "RELEASE", b)
        self._draw()
        return b

    def test_ctrl_c_copies_through_the_host(self):
        where = self._select()
        self.assertEqual(self.surface.selected_text(), "This")
        self.assertEqual(self._send("C", "PRESS", where, ctrl=True), {"RUNNING_MODAL"})
        self.assertEqual([(a, v) for _b, a, v in self.copied], [("copy", "This")])
        self.assertIs(self.copied[0][0], self.surface.box)            # the host acts in this chat's area

    def test_ctrl_c_passes_through_without_a_selection_or_away_from_the_chat(self):
        self.assertEqual(self._send("C", "PRESS", (250, 300), ctrl=True), {"PASS_THROUGH"})
        where = self._select()
        self.assertEqual(self._send("C", "PRESS", where), {"PASS_THROUGH"})               # no Ctrl
        self.over = False                                              # the mouse over the 3D View
        self.assertEqual(self._send("C", "PRESS", (900, 900), ctrl=True), {"PASS_THROUGH"})
        self.assertEqual(self.copied, [])
        self.assertTrue(self.surface.has_selection())

    def test_with_the_prompt_focused_ctrl_c_is_the_prompts(self):
        self._select()
        self.surface.box.model.set_text("typed")
        rect = next(r for r, w in self.surface._hits if w == "input")
        self._send("LEFTMOUSE", "PRESS", (rect.x + 5, rect.top - 10))
        self._send("LEFTMOUSE", "RELEASE", (rect.x + 5, rect.top - 10))
        self.surface.box.model.select_all()
        self.assertFalse(self.surface.has_selection())                 # a click elsewhere cleared it
        self._send("C", "PRESS", (rect.x + 5, rect.top - 10), ctrl=True)
        self.assertEqual(self.copied, [])                              # the box copied its own text
        self.assertEqual(self.context.window_manager.clipboard, "typed")

    def test_esc_clears_it(self):
        where = self._select()
        self.assertEqual(self._send("ESC", "PRESS", where), {"RUNNING_MODAL"})
        self.assertFalse(self.surface.has_selection())
        self.assertEqual(self._send("ESC", "PRESS", where), {"PASS_THROUGH"})

    def test_a_click_in_another_editor_clears_it(self):
        self._select()
        self.over = False
        self.assertEqual(self._send("LEFTMOUSE", "PRESS", (900, 900)), {"PASS_THROUGH"})
        self.assertFalse(self.surface.has_selection())

    def test_double_click_event(self):
        para = _lines(self.surface, 0, (cv.REPLY, 1))
        where = _at(self.surface, para[0], para[0].start + 6)
        self.surface.clock.t = 0.0
        self.assertEqual(self._send("LEFTMOUSE", "DOUBLE_CLICK", where), {"RUNNING_MODAL"})
        self._send("LEFTMOUSE", "RELEASE", where)
        self.assertEqual(self.surface.selected_text(), "paragraph")

    def test_i_beam_over_message_text(self):
        para = _lines(self.surface, 0, (cv.REPLY, 1))
        self._send("MOUSEMOVE", "NOTHING", _at(self.surface, para[0], para[0].start + 3))
        self.assertEqual(self.context.cursors[-1:], ["TEXT"])
        self._send("MOUSEMOVE", "NOTHING", (250, 595))
        self.assertEqual(self.context.cursors[-1:], ["DEFAULT"])

    def test_the_menu_sees_the_selection(self):
        where = self._select()
        self._send("RIGHTMOUSE", "PRESS", where)
        target = self.ic.menu_target()
        self.assertTrue(target["surface"].has_selection())             # Copy Selection shows
        self.assertEqual(target["surface"].selected_text(), "This")

    def test_forgotten_on_file_load(self):
        self._select()
        self.ic._on_load_pre()
        self.assertIsNone(self.ic._SELECT["hit"])


if __name__ == "__main__":
    unittest.main()
