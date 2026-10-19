# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the GPU chat's markdown parser and chat view (ui_gpu/markdown.py,
ui_gpu/chat_view.py, task "GPU 2") with a fixed-width stand-in font.

1. Markdown: headings, paragraphs, nested bullet / numbered items, quotes,
   fenced code (also unclosed while streaming), hr, tables as aligned rows,
   inline bold / italic / code / links.
2. Chat view: newest turn at the bottom, scroll clamped to the content,
   Workshop collapsed by default and listing failed tools when open, Copy /
   link / jump clicks returned as actions, live text for the running turn,
   cached heights for finished turns, an empty-chat hint.

Run with::

    python -m unittest tests.test_gpu_chat_view -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_chat_view_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    mods = [importlib.import_module(name + ".ui_gpu." + m) for m in ("markdown", "chat_view", "layout", "style")]
    return mods


md, cv, lay, st = _load()


class TestMarkdown(unittest.TestCase):

    def test_blocks(self):
        blocks = md.parse("# Title\n\nSome **bold** text\nthat wraps.\n\n- one\n  - nested\n3. third\n"
                          "> quoted\n\n---\n```python\nx = 1\n```")
        kinds = [(b.kind, b.level) for b in blocks]
        self.assertEqual(kinds, [("heading", 1), ("para", 0), ("item", 0), ("item", 1), ("item", 0),
                                 ("quote", 0), ("hr", 0), ("code", 0)])
        self.assertEqual(blocks[1].text, "Some bold text that wraps.")
        self.assertEqual(blocks[4].marker, "3.")
        self.assertEqual((blocks[7].lang, blocks[7].lines), ("python", ["x = 1"]))

    def test_unclosed_fence_while_streaming(self):
        blocks = md.parse("Here:\n```\nline 1\nline 2")
        self.assertEqual(blocks[-1].kind, "code")
        self.assertEqual(blocks[-1].lines, ["line 1", "line 2"])

    def test_table(self):
        blocks = md.parse("| Name | Count |\n|---|---|\n| logs | 5 |\n| light | 1 |")
        self.assertEqual(blocks[0].kind, "table")
        self.assertEqual(blocks[0].lines[0], "Name   Count")
        self.assertEqual(blocks[0].lines[2], "logs   5")

    def test_inline(self):
        spans = md.parse_inline("Use `bpy.ops` for **bold**, *italic* and [docs](https://x.org).")
        self.assertEqual([(s.text, s.style) for s in spans if s.style != "plain"],
                         [("bpy.ops", "code"), ("bold", "bold"), ("italic", "italic"), ("docs", "link")])
        self.assertEqual([s.url for s in spans if s.style == "link"], ["https://x.org"])
        # Snake_case and arithmetic are not italic.
        self.assertTrue(all(s.style == "plain" for s in md.parse_inline("my_var_name and 2 * 3 * 4")))


def _history():
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Make a campfire"},
        {"role": "tool", "name": "execute_blender_code", "content": "Error: NameError x"},
        {"role": "tool", "name": "execute_blender_code", "content": "{\"status\": \"ok\"}"},
        {"role": "assistant", "content": "Done. Code:\n```python\nlight.energy = 800\n```\nSee [docs](https://d.org)."},
        {"role": "user", "content": "Now add smoke"},
        {"role": "assistant", "content": "Added a smoke domain."},
    ]


class _Live:
    is_thinking = True
    turn_phase = "thinking"
    thinking_dots = 1
    streaming_text = "Working on **it**"


class TestChatView(unittest.TestCase):

    def canvas(self, **state):
        return lay.Canvas(st.Style(), lay.fixed_measure(0.55), lay.InteractionState(**state))

    def texts(self, canvas):
        return [c for c in canvas.commands if c.kind == "text"]

    def test_newest_turn_at_the_bottom(self):
        c = self.canvas()
        cv.ChatView().draw(c, lay.Rect(0, 0, 600, 2000), _history())
        def y_of(fragment):
            return next(cmd.rect.y for cmd in self.texts(c) if fragment in cmd.text)
        self.assertLess(y_of("Now add smoke"), y_of("Make a campfire"))   # newest lower on screen
        code = [cmd for cmd in self.texts(c) if cmd.font == "mono" and "light.energy" in cmd.text]
        self.assertEqual(len(code), 1)                                     # code block in monospace

    def test_workshop_collapsed_then_open(self):
        view = cv.ChatView()
        c = self.canvas()
        view.draw(c, lay.Rect(0, 0, 600, 2000), _history())
        header = [cmd.text for cmd in self.texts(c) if cmd.text.startswith(">")]
        self.assertTrue(any("2 steps" in h and "1 failed" in h for h in header))
        self.assertFalse(any("NameError" in cmd.text for cmd in self.texts(c)))
        view.expanded.add("t0")
        c2 = self.canvas()
        view.draw(c2, lay.Rect(0, 0, 600, 2000), _history())
        failed = [cmd for cmd in self.texts(c2) if "NameError" in cmd.text]
        self.assertTrue(failed and failed[0].color == cv._ERROR)

    def test_click_actions(self):
        view = cv.ChatView()
        self.assertEqual(view.draw(self.canvas(clicked="copy:0:0"), lay.Rect(0, 0, 600, 2000), _history()),
                         [("copy", "light.energy = 800")])
        self.assertEqual(view.draw(self.canvas(clicked="ws:t1"), lay.Rect(0, 0, 600, 2000), _history()), [])
        self.assertEqual(view.draw(self.canvas(clicked="ws:t0"), lay.Rect(0, 0, 600, 2000), _history()),
                         [("toggle", "t0")])
        self.assertEqual(view.draw(self.canvas(clicked="link:https://d.org"), lay.Rect(0, 0, 600, 2000),
                                   _history()), [("link", "https://d.org")])

    def test_scroll_clamp_and_jump_button(self):
        long = _history() + [m for i in range(30) for m in (
            {"role": "user", "content": "question {:d}".format(i)},
            {"role": "assistant", "content": "answer {:d} with some more words to wrap".format(i)})]
        view = cv.ChatView()
        rect = lay.Rect(0, 0, 400, 300)
        view.draw(self.canvas(), rect, long)
        self.assertGreater(view.content_height, rect.h)
        view.scroll_by(1e9, rect.h)
        self.assertAlmostEqual(view.scroll, view.content_height - rect.h)
        c = self.canvas(clicked="jump")
        self.assertIn(("jump", ""), view.draw(c, rect, long))
        view.scroll_by(-1e9, rect.h)
        self.assertEqual(view.scroll, 0.0)
        c2 = self.canvas()
        view.draw(c2, rect, long)
        self.assertNotIn("jump", [wid for _, wid in c2.hit_areas])  # following the newest: no button

    def test_live_turn(self):
        hist = _history() + [{"role": "user", "content": "And a log pile"}]
        c = self.canvas()
        cv.ChatView().draw(c, lay.Rect(0, 0, 600, 2000), hist, live=_Live())
        texts = [cmd.text for cmd in self.texts(c)]
        self.assertIn("it", texts)                                   # streamed markdown
        self.assertTrue(any(t.startswith("Coworker is thinking") for t in texts))

    def test_heights_cached_for_finished_turns(self):
        view = cv.ChatView()
        view.draw(self.canvas(), lay.Rect(0, 0, 600, 2000), _history())
        cached = len(view._heights)
        self.assertEqual(cached, 2)
        view.draw(self.canvas(), lay.Rect(0, 0, 600, 2000), _history())
        self.assertEqual(len(view._heights), cached)

    def test_compact_and_empty(self):
        c = self.canvas()
        view = cv.ChatView()
        view.draw(c, lay.Rect(0, 0, 280, 2000), _history(), compact=True)
        self.assertTrue(self.texts(c))
        empty = self.canvas()
        view.draw(empty, lay.Rect(0, 0, 280, 400), [{"role": "system", "content": "s"}])
        self.assertIn("Start a conversation", " ".join(cmd.text for cmd in self.texts(empty)))


if __name__ == "__main__":
    unittest.main()
