# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the GPU chat's input box (ui_gpu/input_box.py, task "GPU 3") with a
fixed-width stand-in font.

1. Text model: insert / delete (also by word and over a selection), cursor
   moves across lines, select all, undo / redo.
2. Keys: Enter sends, Shift+Enter adds a line, an empty box sends nothing,
   Ctrl+C / X / V ask the host, Esc leaves the box, editor shortcuts are
   swallowed while focused and ignored while not.
3. ``@`` suggestions: only after a space or at the start, arrows pick, Tab /
   Enter insert, Esc closes, a click inserts.
4. Drawing: grows with lines up to a limit, placeholder when empty, caret and
   selection, Send / Stop, attachment chip, click places the cursor.

Run with::

    python -m unittest tests.test_gpu_input_box -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_input_box_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return [importlib.import_module(name + ".ui_gpu." + m) for m in ("input_box", "layout", "style")]


ib, lay, st = _load()
K = ib.Key


def _type(box, text):
    for ch in text:
        box.handle_key(K("SPACE" if ch == " " else ch.upper(), unicode=ch))


def _provider(query):
    names = ["Cube", "Camera", "Campfire Light", "Log.001"]
    return [ib.Suggestion(n, "@" + n.replace(" ", "_"), "object")
            for n in names if n.lower().startswith(query.lower())]


class TestTextModel(unittest.TestCase):

    def test_insert_delete_and_selection(self):
        m = ib.TextModel()
        m.insert("hello world")
        m.delete(word=True)
        self.assertEqual(m.text, "hello ")
        m.move("start")
        m.move("word_right", select=True)
        self.assertEqual(m.selected_text(), "hello")
        m.insert("bye")
        self.assertEqual((m.text, m.cursor), ("bye ", 3))
        m.delete(forward=True)
        self.assertEqual(m.text, "bye")
        m.select_all()
        m.delete()
        self.assertEqual(m.text, "")

    def test_lines_and_undo(self):
        m = ib.TextModel()
        m.insert("first line\nsecond")
        m.move("up")
        self.assertEqual(m.cursor, 6)                    # same column on the line above
        m.move("end")
        self.assertEqual(m.cursor, len("first line"))
        m.move("down")
        self.assertEqual(m.cursor, len(m.text))          # clamped to the shorter line
        m.undo()
        self.assertEqual(m.text, "")
        m.redo()
        self.assertEqual(m.text, "first line\nsecond")

    def test_left_right_collapse_a_selection(self):
        m = ib.TextModel("abcdef", cursor=1)
        m.move("end_all", select=True)
        m.move("left")
        self.assertEqual((m.cursor, m.selection), (1, None))


class TestKeys(unittest.TestCase):

    def test_enter_sends_shift_enter_new_line(self):
        box = ib.InputBox(focused=True)
        _type(box, "make")
        box.handle_key(K("RET", shift=True))
        _type(box, "fire")
        self.assertEqual(box.model.text, "make\nfire")
        self.assertTrue(box.handle_key(K("RET")))
        self.assertEqual(box.actions, [("send", "make\nfire")])
        self.assertEqual(box.model.text, "")
        box.model.undo()                                  # a sent message doesn't come back
        self.assertEqual(box.model.text, "")
        box.handle_key(K("NUMPAD_ENTER"))
        self.assertEqual(len(box.actions), 1)             # empty box: nothing sent

    def test_clipboard_and_undo_keys(self):
        box = ib.InputBox(focused=True)
        _type(box, "abc")
        box.handle_key(K("A", ctrl=True))
        box.handle_key(K("X", ctrl=True))
        self.assertEqual((box.actions[-1], box.model.text), (("copy", "abc"), ""))
        box.handle_key(K("V", ctrl=True))
        self.assertEqual(box.actions[-1], ("paste", ""))
        box.paste("one\r\ntwo")
        self.assertEqual(box.model.text, "one\ntwo")
        box.handle_key(K("Z", ctrl=True))
        self.assertEqual(box.model.text, "")
        box.handle_key(K("Z", ctrl=True, shift=True))
        self.assertEqual(box.model.text, "one\ntwo")

    def test_focus_and_shortcut_blocking(self):
        box = ib.InputBox()
        self.assertFalse(box.handle_key(K("G", unicode="g")))   # not focused: G is the editor's
        box.focused = True
        self.assertTrue(box.handle_key(K("G", unicode="g")))
        self.assertTrue(box.handle_key(K("F3")))                # swallowed while typing
        self.assertTrue(box.handle_key(K("G", value="RELEASE")))
        self.assertFalse(box.handle_key(K("LEFTMOUSE")))        # clicks stay with the host
        self.assertTrue(box.handle_key(K("ESC")))
        self.assertEqual((box.focused, box.actions[-1]), (False, ("blur", "")))


class TestSuggestions(unittest.TestCase):

    def test_at_mentions(self):
        box = ib.InputBox(focused=True, provider=_provider)
        _type(box, "move @ca")
        self.assertEqual([s.label for s in box.suggestions], ["Camera", "Campfire Light"])
        box.handle_key(K("DOWN_ARROW"))
        box.handle_key(K("TAB"))
        self.assertEqual(box.model.text, "move @Campfire_Light ")
        self.assertEqual(box.suggestions, [])
        _type(box, "up")
        self.assertEqual(box.actions, [])                # Enter wasn't used by a list

    def test_not_inside_words_and_esc_closes(self):
        box = ib.InputBox(focused=True, provider=_provider)
        _type(box, "mail@c")
        self.assertEqual(box.suggestions, [])
        _type(box, " @c")
        self.assertTrue(box.suggestions)
        box.handle_key(K("ESC"))
        self.assertEqual((box.suggestions, box.focused), ([], True))   # first Esc closes the list
        box.handle_key(K("RET"))
        self.assertEqual(box.actions, [("send", "mail@c @c")])

    def test_click_a_suggestion(self):
        box = ib.InputBox(focused=True, provider=_provider)
        _type(box, "@cu")
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), lay.InteractionState(clicked="suggest:0"))
        box.draw(canvas, lay.Rect(0, 0, 500, 300))
        self.assertEqual(box.model.text, "@Cube ")


class TestDrawing(unittest.TestCase):

    def canvas(self, **state):
        return lay.Canvas(st.Style(), lay.fixed_measure(0.55), lay.InteractionState(**state))

    def texts(self, canvas):
        return [c.text for c in canvas.commands if c.kind == "text"]

    def test_grows_up_to_a_limit(self):
        box = ib.InputBox(focused=True)
        c = self.canvas()
        one = box.height(c, 400)
        box.model.insert("\n".join("line {:d}".format(i) for i in range(3)))
        three = box.height(c, 400)
        box.model.insert("\n" * 20)
        self.assertGreater(three, one)
        self.assertAlmostEqual(box.height(c, 400), one + (ib._MAX_VISIBLE_LINES - 1) * c.style.line_height)

    def test_placeholder_caret_and_buttons(self):
        box = ib.InputBox(focused=True)
        c = self.canvas()
        box.draw(c, lay.Rect(0, 0, 400, 200))
        self.assertTrue(any(t.startswith("Ask Coworker") for t in self.texts(c)))
        self.assertIn("Send", self.texts(c))
        _type(box, "hi")
        c = self.canvas()
        box.draw(c, lay.Rect(0, 0, 400, 200))
        caret = [cmd for cmd in c.commands if cmd.kind == "rect" and cmd.color == c.style.cursor]
        self.assertEqual(len(caret), 1)
        box.busy, box.attachment = True, "shot.png"
        c = self.canvas(clicked="stop")
        self.assertEqual(box.draw(c, lay.Rect(0, 0, 400, 200)), [("stop", "")])
        self.assertIn("Image: shot.png", self.texts(c))

    def test_send_button_and_click_to_place_cursor(self):
        box = ib.InputBox(focused=True)
        _type(box, "hello world")
        c = self.canvas()
        box.draw(c, lay.Rect(0, 0, 400, 200))
        top, bottom, x0, _start, _piece = box._drawn[0]
        char_w = 0.55 * c.style.font_size
        box.place_cursor(x0 + char_w * 5.1, (top + bottom) / 2)
        self.assertEqual(box.model.cursor, 5)
        box.place_cursor(x0 + char_w * 20, (top + bottom) / 2, select=True)
        self.assertEqual(box.model.selected_text(), " world")
        self.assertEqual(box.draw(self.canvas(clicked="send"), lay.Rect(0, 0, 400, 200)),
                         [("send", "hello world")])

    def test_wrapped_lines_keep_offsets(self):
        box = ib.InputBox()
        box.model.insert("alpha beta gamma delta epsilon zeta eta theta")
        lines = box._lines(self.canvas(), 200)
        self.assertGreater(len(lines), 1)
        for start, piece in lines:
            self.assertEqual(box.model.text[start:start + len(piece)], piece)


if __name__ == "__main__":
    unittest.main()
