# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the chat's right-click menu (#453): the routing in the GPU chat's
input modal (ui_gpu/input_capture.py) with a stand-in ``bpy``, and what the
chat view keeps for the menu (ui_gpu/chat_view.py, ui_gpu/surface.py) with a
fixed-width stand-in font.

1. Routing: a right press over a chat opens ``BFACW_MT_chat_context`` once,
   in that chat's area and region, and is used; its release and click are
   used too (no Node Editor menu in Blender or Bforartists).  Outside a chat,
   or outside what the chat drew (the rest of a sidebar), right-click goes
   through as before.  Left clicks are unchanged.
2. What the menu knows: the widget and the message under the mouse -- your
   message or the reply, with the text their Copy buttons copy, and the code
   of a code block; nothing in the gap between turns or off the chat.
3. The prompt's Cut / Copy / Paste / Select All act on the box it was
   opened on, like Ctrl X / C / V / A.
4. Copy Whole Chat: your messages and the replies, without tool steps.

Run with::

    python -m unittest tests.test_chat_context_menu -v
"""

__all__ = ()

import importlib
import types
import unittest

from test_autosave_gap import _fake_bpy, _load

_PKG = "bfacw_autosave_gap_pkg"


def _history():
    return [
        {"role": "user", "content": "Make a campfire"},
        {"role": "tool", "name": "execute_blender_code", "content": "ok"},
        {"role": "assistant", "content": "Done. Here it is:\n\n```python\nimport bpy\nprint(1)\n```\n\nEnjoy."},
        {"role": "user", "content": "Thanks [attached: ref.png]"},
        {"role": "assistant", "content": "You're welcome."},
    ]


class _Region:
    x, y = 100, 50

    def __init__(self):
        self.tagged = 0

    def tag_redraw(self):
        self.tagged += 1


class _Context:
    def __init__(self, wm):
        self.window = types.SimpleNamespace(cursor_set=lambda _c: None)
        self.window_manager = wm
        self.overrides = []

    def temp_override(self, **kw):
        self.overrides.append(kw)
        return _Null()


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _event(kind, value, x, y, **kw):
    base = dict(type=kind, value=value, mouse_x=x, mouse_y=y, shift=False, ctrl=False, alt=False,
                oskey=False, unicode="")
    base.update(kw)
    return types.SimpleNamespace(**base)


class _Base(unittest.TestCase):

    def setUp(self):
        self.bpy = _fake_bpy()
        self.ic = _load(self.bpy)
        self.sf = importlib.import_module(_PKG + ".ui_gpu.surface")
        self.lay = importlib.import_module(_PKG + ".ui_gpu.layout")
        self.st = importlib.import_module(_PKG + ".ui_gpu.style")
        self.menus = []
        self.ic.bpy.ops.wm = types.SimpleNamespace(call_menu=lambda name: self.menus.append(name))
        self.region, self.area = _Region(), object()
        self.surface = self.sf.ChatSurface()
        self.rect = self.lay.Rect(0, 0, 500, 600)
        self._draw()
        self.surface.bounds = self.rect
        self.over = True        # the router finds the chat under the mouse
        self.ic.set_host(lambda _c, _e: (self.surface, self.area, self.region) if self.over else None)
        self.context = _Context(self.bpy.context.window_manager)
        self.op = self.ic.BFACW_OT_gpu_input()

    def _draw(self):
        canvas = self.lay.Canvas(self.st.Style(), self.lay.fixed_measure(0.55), self.surface.interaction())
        self.surface.draw(canvas, self.rect, _history())

    def _centre(self, widget):
        rect = next(r for r, w in self.surface._hits if w == widget)
        return rect.x + rect.w / 2, rect.y + rect.h / 2

    def _send(self, kind, value, local_xy):
        x, y = local_xy
        return self.op._handle(self.context, _event(kind, value, x + _Region.x, y + _Region.y),
                               self.ic._HOST["router"])


class TestRouting(_Base):

    def test_right_press_over_the_chat_opens_the_menu_once_and_is_used(self):
        where = self._centre("input")
        self.assertEqual(self._send("RIGHTMOUSE", "PRESS", where), {"RUNNING_MODAL"})
        self.assertEqual(self.menus, ["BFACW_MT_chat_context"])
        override = self.context.overrides[-1]
        self.assertIs(override["area"], self.area)          # its entries act on this chat
        self.assertIs(override["region"], self.region)
        for value in ("RELEASE", "CLICK", "DOUBLE_CLICK"):
            self.assertEqual(self._send("RIGHTMOUSE", value, where), {"RUNNING_MODAL"}, value)
        self.assertEqual(len(self.menus), 1)

    def test_the_menu_knows_what_was_under_the_mouse(self):
        self._send("RIGHTMOUSE", "PRESS", self._centre("input"))
        target = self.ic.menu_target()
        self.assertEqual(target["widget"], "input")
        self.assertIs(target["surface"], self.surface)
        self.assertTrue(self.surface.box.focused)            # the prompt takes the keyboard
        self._send("RIGHTMOUSE", "PRESS", self._centre("copyuser:0"))
        target = self.ic.menu_target()
        self.assertEqual(target["widget"], "copyuser:0")
        self.assertEqual((target["turn"]["idx"], target["turn"]["part"], target["turn"]["text"]),
                         (0, "user", "Make a campfire"))

    def test_right_click_outside_a_chat_goes_through(self):
        self.over = False
        for value in ("PRESS", "RELEASE", "CLICK"):
            self.assertEqual(self._send("RIGHTMOUSE", value, (10, 10)), {"PASS_THROUGH"}, value)
        self.assertEqual(self.menus, [])

    def test_right_click_off_what_the_chat_drew_goes_through_and_blurs(self):
        # A sidebar: the router still returns the chat while its box has the keyboard.
        self.surface.bounds = self.lay.Rect(0, 0, 500, 300)
        self.ic.focus(self.surface.box, self.region)
        self.assertEqual(self._send("RIGHTMOUSE", "PRESS", (10, 450)), {"PASS_THROUGH"})
        self.assertEqual(self.menus, [])
        self.assertFalse(self.surface.box.focused)

    def test_left_clicks_unchanged(self):
        where = self._centre("input")
        self.assertEqual(self._send("LEFTMOUSE", "PRESS", where), {"RUNNING_MODAL"})
        self.assertTrue(self.surface.box.focused)
        self.over = False
        self.assertEqual(self._send("LEFTMOUSE", "PRESS", (10, 10)), {"PASS_THROUGH"})
        self.assertFalse(self.surface.box.focused)
        self.assertEqual(self.menus, [])

    def test_prompt_edit_acts_on_its_box(self):
        box = self.surface.box
        box.model.set_text("hello world")
        self._send("RIGHTMOUSE", "PRESS", self._centre("input"))
        self.assertIsNone(self.ic.menu_edit("copy", ""))       # no selection: nothing to copy
        self.assertIsNone(self.ic.menu_edit("select_all", ""))
        self.assertEqual(self.ic.menu_edit("cut", ""), "hello world")
        self.assertEqual(box.model.text, "")
        self.ic.menu_edit("paste", "pasted")
        self.assertEqual(box.model.text, "pasted")
        box.model.select_all()
        self.assertEqual(self.ic.menu_edit("copy", ""), "pasted")
        self.assertEqual(box.model.text, "pasted")
        self.assertTrue(box.focused)

    def test_forgotten_on_file_load(self):
        self._send("RIGHTMOUSE", "PRESS", self._centre("input"))
        self.ic._on_load_pre()
        self.assertIsNone(self.ic.menu_target()["surface"])
        self.assertIsNone(self.ic.menu_edit("paste", "x"))


class TestTurnAt(_Base):

    def test_your_message_and_the_reply(self):
        view = self.surface.view
        self.assertEqual(len(view.turn_rects), 2)            # the drawn frame only, not the measuring
        x, y = self._centre("copyuser:1")
        mine = self.surface.turn_at(x - 40, y)
        self.assertEqual((mine["idx"], mine["part"], mine["text"], mine["last"]), (1, "user", "Thanks", True))
        x, y = self._centre("copymsg:1")
        reply = self.surface.turn_at(x, y)
        self.assertEqual((reply["part"], reply["text"], reply["code"]), ("reply", "You're welcome.", ""))

    def test_code_block(self):
        x, y = self._centre("copy:0:0")
        turn = self.surface.turn_at(x, y - 20)
        self.assertEqual(turn["code"], "import bpy\nprint(1)")
        self.assertTrue(turn["text"].startswith("Done. Here it is:"))
        self.assertFalse(turn["last"])

    def test_nothing_between_turns_or_off_the_chat(self):
        lower, upper = sorted(self.surface.view.turn_rects, key=lambda t: t[0].y)[:2]
        self.assertLess(lower[0].top, upper[0].y)
        gap_y = (lower[0].top + upper[0].y) / 2
        self.assertIsNotNone(self.surface.turn_at(lower[0].x + 5, lower[0].top - 1))
        self.assertIsNone(self.surface.turn_at(lower[0].x + 5, gap_y))
        self.assertIsNone(self.surface.turn_at(*self._centre("input")))
        self.assertIsNone(self.surface.turn_at(-5, 300))

    def test_right_press_on_the_prompt_has_no_message(self):
        info = self.surface.on_right_press(*self._centre("input"))
        self.assertEqual(info, {"widget": "input", "turn": None})

    def test_no_new_hit_areas(self):
        ids = {w for _r, w in self.surface._hits}
        self.assertFalse({i for i in ids if i.startswith("turn")})

    def test_a_notice_has_no_messages(self):
        canvas = self.lay.Canvas(self.st.Style(), self.lay.fixed_measure(0.55), self.surface.interaction())
        self.surface.draw(canvas, self.rect, _history(), notice=self.sf.Notice("Harness", "INFO", ("line",)))
        self.assertEqual(self.surface.view.turn_rects, [])


class TestTranscript(unittest.TestCase):

    def test_messages_and_replies_without_tools(self):
        _load(_fake_bpy())
        cv = importlib.import_module(_PKG + ".ui_gpu.chat_view")
        text = cv.transcript(_history())
        self.assertTrue(text.startswith("You:\nMake a campfire\n\nCoworker:\nDone."))
        self.assertIn("You:\nThanks\n\nCoworker:\nYou're welcome.", text)
        self.assertNotIn("execute_blender_code", text)
        self.assertNotIn("[attached", text)


if __name__ == "__main__":
    unittest.main()
