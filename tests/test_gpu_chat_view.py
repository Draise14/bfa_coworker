# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the GPU chat's markdown parser and chat view (ui_gpu/markdown.py,
ui_gpu/chat_view.py, task "GPU 2") with a fixed-width stand-in font.

1. Markdown: headings, paragraphs, nested bullet / numbered items, quotes,
   fenced code (also unclosed while streaming), hr, tables as aligned rows,
   inline bold / italic / code / links.
2. Icons: every glyph is triangles inside its square; icon buttons.
3. Chat view: newest turn at the bottom, scroll clamped to the content,
   Workshop collapsed by default and listing failed tools when open, Copy /
   link / jump clicks returned as actions, live text for the running turn,
   cached heights for finished turns, an empty-chat hint; icons on your
   messages, Coworker, finished turns, Workshop, tool rows, notes, code,
   Copy and headings; heading sizes.

Run with::

    python -m unittest tests.test_gpu_chat_view -v
"""

__all__ = ()

import importlib
import struct
import sys
import types
import unittest
import zlib
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_chat_view_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    mods = [importlib.import_module(name + ".ui_gpu." + m)
            for m in ("markdown", "chat_view", "layout", "style", "icons", "png")]
    return mods


md, cv, lay, st, ic, png = _load()


def _png(width, height, channels, rows):
    """Encode *rows* (bytes per row) as a PNG, each row with its own filter byte first."""
    ctype = {4: 6, 3: 2, 1: 0}[channels]
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"".join(rows)))
            + chunk(b"IEND", b""))


class TestIcons(unittest.TestCase):

    def test_bforartists_images_exist_for_every_image_icon(self):
        for name in ic.IMAGE_ICONS:
            small, big = ic.image_path(name, 14), ic.image_path(name, 40)
            self.assertTrue(small and small.endswith(name.lower() + "_16.png"), name)
            self.assertTrue(big and big.endswith(name.lower() + "_48.png"), name)
            w, h, rgba = png.read_rgba(Path(small).read_bytes())
            self.assertEqual((w, h, len(rgba)), (16, 16, 16 * 16 * 4))
        self.assertIsNone(ic.image_path("KEYTYPE_EXTREME_VEC", 16))     # drawn, in keyframe colours
        user = png.read_rgba(Path(ic.image_path("USER", 32)).read_bytes())[2]
        centre = user[(16 * 32 + 16) * 4:(16 * 32 + 16) * 4 + 3]
        self.assertGreater(max(centre) - min(centre), 40)               # full colour, not grey

    def test_png_filters(self):
        # 2x2 RGBA; row 0 "Sub" (filter 1), row 1 "Paeth" (filter 4) over row 0.
        row0 = bytes([1, 10, 20, 30, 255, 5, 5, 5, 0])
        row1 = bytes([4, 1, 1, 1, 1, 2, 2, 2, 2])
        w, h, rgba = png.read_rgba(_png(2, 2, 4, [row0, row1]))
        self.assertEqual((w, h), (2, 2))
        self.assertEqual(rgba[:8], bytes([10, 20, 30, 255, 15, 25, 35, 255]))
        self.assertEqual(rgba[8:12], bytes([11, 21, 31, 0]))            # Paeth picks "up" at x=0
        grey = png.read_rgba(_png(1, 1, 1, [bytes([0, 200])]))[2]
        self.assertEqual(grey, bytes([200, 200, 200, 255]))
        with self.assertRaises(ValueError):
            png.read_rgba(b"not a png")

    def test_heading_markers_use_keyframe_colours(self):
        c = lay.Canvas(st.Style(), lay.fixed_measure(0.55))
        cv.ChatView().draw(c, lay.Rect(0, 0, 600, 2000), [
            {"role": "user", "content": "hi"}, {"role": "assistant", "content": "# One\n## Two"}])
        marks = [cmd for cmd in c.commands if cmd.kind == "icon" and cmd.text.startswith("KEYTYPE")]
        self.assertEqual([m.color for m in marks],
                         [c.style.keyframe_border, c.style.keyframes[0], c.style.keyframe_border, c.style.keyframes[1]])

    def test_every_icon_is_triangles_inside_its_square(self):
        rect = lay.Rect(10, 20, 16, 16)
        for name in sorted(ic.NAMES) + ["NOT_AN_ICON"]:
            tris = ic.triangles(name, rect)
            self.assertTrue(tris and len(tris) % 3 == 0, name)
            for x, y in tris:
                self.assertTrue(rect.x - 2 <= x <= rect.right + 2 and rect.y - 2 <= y <= rect.top + 2, name)

    def test_icon_buttons(self):
        c = lay.Canvas(st.Style(), lay.fixed_measure(0.55))
        c.button(lay.Rect(0, 0, 80, 20), "copy", "Copy", icon="COPYDOWN")
        kinds = [(cmd.kind, cmd.text) for cmd in c.commands if cmd.kind in ("icon", "text")]
        self.assertEqual(kinds, [("icon", "COPYDOWN"), ("text", "Copy")])
        icon = next(cmd for cmd in c.commands if cmd.kind == "icon")
        text = next(cmd for cmd in c.commands if cmd.kind == "text")
        self.assertLess(icon.rect.right, text.rect.x + 0.01)          # icon before the label


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

    def icons(self, canvas):
        return [c.text for c in canvas.commands if c.kind == "icon"]

    def test_icons_read_before_words(self):
        c = self.canvas()
        view = cv.ChatView(agent_icon="GHOST_ENABLED")
        hist = _history() + [{"role": "assistant", "content": "# Big\n## Mid\n### Small"}]
        hist[-1]["turn_seconds"] = 75
        view.draw(c, lay.Rect(0, 0, 600, 2000), hist)
        names = self.icons(c)
        self.assertEqual(names.count("USER"), 2)                      # one per message you sent
        self.assertIn("GHOST_ENABLED", names)                         # the host's agent icon
        self.assertIn("CHECKMARK", names)                             # finished turn
        self.assertIn("SCRIPT", names)                                # code block header
        self.assertIn("COPYDOWN", names)                              # Copy button
        for marker in ("KEYTYPE_EXTREME_VEC", "KEYTYPE_MOVING_HOLD_VEC", "KEYTYPE_JITTER_VEC"):
            self.assertIn(marker, names)
        self.assertIn("Coworker  |  1m 15s", [t.text for t in self.texts(c)])
        sizes = {cmd.text: cmd.size for cmd in self.texts(c) if cmd.text in ("Big", "Mid", "Small")}
        body = c.style.font_size
        self.assertAlmostEqual(sizes["Big"], body * 1.6)
        self.assertGreater(sizes["Big"], sizes["Mid"])
        self.assertGreater(sizes["Mid"], sizes["Small"])

    def test_note_titles_and_icons(self):
        c = self.canvas()
        view = cv.ChatView()
        view.expanded.add("t0")
        hist = [{"role": "user", "content": "go"},
                {"role": "user", "content": "[System: keep going]", "system_note": "followup"},
                {"role": "assistant", "content": "ok"}]
        view.draw(c, lay.Rect(0, 0, 600, 2000), hist)
        self.assertIn("FORWARD", self.icons(c))
        self.assertTrue(any(t.text.startswith("Coworker → itself") for t in self.texts(c)))

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
        header = [cmd.text for cmd in self.texts(c) if cmd.text.startswith("Workshop")]
        self.assertTrue(any("2 steps" in h and "1 failed" in h for h in header))
        self.assertIn("TRIA_RIGHT", self.icons(c))
        self.assertIn("WARNING", self.icons(c))                     # a step failed: warning, not package
        self.assertFalse(any("NameError" in cmd.text for cmd in self.texts(c)))
        view.expanded.add("t0")
        c2 = self.canvas()
        view.draw(c2, lay.Rect(0, 0, 600, 2000), _history())
        failed = [cmd for cmd in self.texts(c2) if "NameError" in cmd.text]
        self.assertTrue(failed and failed[0].color == cv._ERROR)
        names = self.icons(c2)
        names = names[names.index("TRIA_DOWN"):]
        self.assertEqual([n for n in names if n in ("ERROR", "CHECKMARK", "TRIA_DOWN")][:3],
                         ["TRIA_DOWN", "ERROR", "CHECKMARK"])          # open arrow, then one icon per tool row

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
