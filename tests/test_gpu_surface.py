# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the GPU chat surface (ui_gpu/surface.py, task "GPU 4"): the chat
as it sits in a region, with a fixed-width stand-in font.

1. Layout: input pinned at the bottom, chat above, the compact top bar only
   when asked; a notice card replaces chat and input.
2. Mouse: a click is a press and release on the same widget; the input box
   takes focus from a click and loses it on a click elsewhere; wheel
   scrolls, Ctrl+wheel zooms (clamped).
3. Actions: send / top-bar buttons / chat mode / notice buttons come back
   from draw; toggle and jump are done by the surface itself.
4. Attachments: user-message markers become thumbnails (or a name chip when
   the image isn't loaded); thumbnail sizes S / M / L.

Run with::

    python -m unittest tests.test_gpu_surface -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_surface_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return [importlib.import_module(name + ".ui_gpu." + m)
            for m in ("surface", "layout", "style", "input_box", "chat_view")]


sf, lay, st, ib, cv = _load()
RECT = lay.Rect(0, 0, 500, 600)


def _history():
    return [
        {"role": "user", "content": "Make a campfire [attached: ref.png, gone.png]"},
        {"role": "tool", "name": "execute_blender_code", "content": "ok"},
        {"role": "assistant", "content": "Done."},
    ]


def _draw(surface, history=None, **kw):
    canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), surface.interaction())
    actions = surface.draw(canvas, RECT, _history() if history is None else history, **kw)
    return canvas, actions


def _centre(surface, widget):
    rect = next(r for r, w in surface._hits if w == widget)
    return rect.x + rect.w / 2, rect.y + rect.h / 2


def _click(surface, widget, **kw):
    x, y = _centre(surface, widget)
    surface.on_press(x, y)
    surface.on_release(x, y)
    return _draw(surface, **kw)[1]


class TestLayout(unittest.TestCase):

    def test_input_pinned_under_the_chat(self):
        surface = sf.ChatSurface()
        canvas, _ = _draw(surface)
        box = surface.box.rect
        self.assertLess(box.y, 20)                                    # at the bottom
        self.assertGreaterEqual(surface._chat_rect.y, box.top)        # chat above it
        self.assertNotIn("start", [w for _r, w in canvas.hit_areas])  # no top bar in the editor

    def test_compact_top_bar(self):
        surface = sf.ChatSurface(compact=True)
        top = sf.TopBar(status="Idle", running=False, chat_modes=(("AGENT", "Coworker"), ("ASK", "Ask")),
                        chat_mode="AGENT")
        canvas, _ = _draw(surface, top=top)
        ids = [w for _r, w in canvas.hit_areas]
        for widget in ("start", "open_editor", "prefs", "mode:AGENT", "mode:ASK"):
            self.assertIn(widget, ids)
        self.assertLess(surface._chat_rect.top, RECT.top - 20)        # chat starts under the bar
        self.assertEqual(_click(surface, "mode:ASK", top=top), [("chat_mode", "ASK")])
        self.assertEqual(_click(surface, "open_editor", top=top), [("open_editor", "")])
        top.running = True
        _draw(surface, top=top)
        self.assertEqual(_click(surface, "stop_agent", top=top), [("stop_agent", "")])

    def test_notice_replaces_chat_and_input(self):
        surface = sf.ChatSurface()
        notice = sf.Notice("External Harness", "WORLD", ("The chat runs in your client.",),
                           (("bridge", "Start Bridge", "PLAY"), ("copy_config", "Copy Config", "COPYDOWN")))
        canvas, _ = _draw(surface, notice=notice)
        texts = [c.text for c in canvas.commands if c.kind == "text"]
        self.assertIn("External Harness", texts)
        self.assertNotIn("input", [w for _r, w in canvas.hit_areas])  # nothing to type into
        self.assertEqual(_click(surface, "notice:copy_config", notice=notice), [("notice", "copy_config")])

    def test_narrow_notice_stacks_buttons(self):
        surface = sf.ChatSurface(compact=True)
        notice = sf.Notice("External Harness", "WORLD", ("x",),
                           (("bridge", "Start Bridge", "PLAY"), ("copy_config", "Copy Config", "COPYDOWN"),
                            ("configure", "Configure", "PREFERENCES")))
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), surface.interaction())
        surface.draw(canvas, lay.Rect(0, 0, 180, 600), [], notice=notice)
        rects = [r for r, w in canvas.hit_areas if w.startswith("notice:")]
        self.assertEqual(len({round(r.y) for r in rects}), 3)          # one per row


class TestMouse(unittest.TestCase):

    def test_click_needs_press_and_release_on_the_same_widget(self):
        surface = sf.ChatSurface()
        surface.box.model.insert("hello")
        _draw(surface)
        x, y = _centre(surface, "send")
        surface.on_press(x, y)
        surface.on_release(5, 590)                                     # released elsewhere
        self.assertEqual(_draw(surface)[1], [])
        self.assertEqual(_click(surface, "send"), [("send", "hello")])

    def test_focus_follows_clicks(self):
        surface = sf.ChatSurface()
        _draw(surface)
        x, y = _centre(surface, "input")
        self.assertTrue(surface.on_press(x, y))
        self.assertTrue(surface.box.focused)
        self.assertFalse(surface.on_press(250, 300))                   # empty chat area: not used
        self.assertFalse(surface.box.focused)

    def test_wheel_scrolls_and_ctrl_wheel_zooms(self):
        long = [m for i in range(40) for m in ({"role": "user", "content": "q {:d}".format(i)},
                                              {"role": "assistant", "content": "answer {:d}".format(i)})]
        surface = sf.ChatSurface()
        _draw(surface, long)
        cx, cy = 250, surface._chat_rect.y + 50
        self.assertTrue(surface.on_wheel(True, False, cx, cy))
        self.assertGreater(surface.view.scroll, 0)
        self.assertFalse(surface.on_wheel(True, False, 250, 5))        # over the input: not the chat
        for _ in range(40):
            surface.on_wheel(True, True, cx, cy)
        self.assertEqual(surface.zoom, 2.5)                            # clamped
        self.assertTrue(surface.on_move(cx, cy) in (True, False))

    def test_toggle_and_jump_are_handled_inside(self):
        surface = sf.ChatSurface()
        _draw(surface)
        self.assertEqual(_click(surface, "ws:t0"), [])
        self.assertIn("t0", surface.view.expanded)
        _click(surface, "ws:t0")
        self.assertNotIn("t0", surface.view.expanded)


class TestAttachments(unittest.TestCase):

    def test_thumbnails_and_missing_image_chip(self):
        surface = sf.ChatSurface()
        surface.view.image_size = lambda name: (200, 100) if name == "ref.png" else None
        canvas, _ = _draw(surface)
        images = [c for c in canvas.commands if c.kind == "image"]
        self.assertEqual([c.text for c in images], ["ref.png"])
        texts = [c.text for c in canvas.commands if c.kind == "text"]
        self.assertIn("gone.png", texts)                               # a chip with its name
        self.assertNotIn("[attached", " ".join(texts))                 # the marker is never shown
        self.assertEqual(_click(surface, "img:ref.png"), [("open_image", "ref.png")])

    def test_thumbnail_sizes(self):
        heights = {}
        for size in ("S", "M", "L"):
            surface = sf.ChatSurface()
            surface.view.thumb_size = size
            surface.view.image_size = lambda _name: (100, 100)
            canvas, _ = _draw(surface)
            heights[size] = next(c.rect.h for c in canvas.commands if c.kind == "image")
        self.assertLess(heights["S"], heights["M"])
        self.assertLess(heights["M"], heights["L"])

    def test_split_attachments(self):
        self.assertEqual(cv.split_attachments("hi [attached: a.png, b.jpg]"), ("hi", ["a.png", "b.jpg"]))
        self.assertEqual(cv.split_attachments("plain"), ("plain", []))


class TestMentions(unittest.TestCase):

    def test_category_filter_row(self):
        def provider(_query):
            return [ib.Suggestion("Camera", "@Camera", "object", "OUTLINER_OB_CAMERA", "Camera"),
                    ib.Suggestion("Cloth", "@Cloth", "material", "MATERIAL", "Material")]
        box = ib.InputBox(focused=True, provider=provider,
                          categories=(("object", "Objects", "OUTLINER_OB_MESH"), ("material", "Materials", "MATERIAL")))
        for ch in "@c":
            box.handle_key(ib.Key(ch.upper() if ch.isalpha() else "TWO", unicode=ch))
        self.assertEqual((box.match_count, [s.label for s in box.suggestions]), (2, ["Camera", "Cloth"]))
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), lay.InteractionState(clicked="mcat:material"))
        box.draw(canvas, lay.Rect(0, 0, 500, 300))
        self.assertEqual([s.label for s in box.suggestions], ["Cloth"])     # filtered by the row
        icons = [c.text for c in canvas.commands if c.kind == "icon"]
        self.assertIn("VIEWZOOM", icons)
        self.assertIn("MATERIAL", icons)


if __name__ == "__main__":
    unittest.main()
