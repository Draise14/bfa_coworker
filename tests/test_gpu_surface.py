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

    def test_attach_buttons_on_the_input_row(self):
        # #213: the image picker, render and screen sit on the input's button row.
        surface = sf.ChatSurface()
        _draw(surface)
        for widget in ("attach_menu", "attach_render", "attach_screen"):
            self.assertEqual(_click(surface, widget), [(widget, "")])
        narrow = sf.ChatSurface(compact=True)
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), narrow.interaction())
        narrow.draw(canvas, lay.Rect(0, 0, 120, 400), [])
        ids = [w for _r, w in canvas.hit_areas]
        self.assertIn("attach_menu", ids)
        self.assertNotIn("attach_screen", ids)          # a thin box keeps just the picker

    def test_icon_only_buttons_are_square(self):
        # #238, at three widths (editor, sidebar, thin sidebar); thumbnails are not buttons.
        icon_only = ("attach_menu", "attach_render", "attach_screen", "attach_pin", "attach_remove", "tool:",
                     "prefs", "open_editor", "mode_menu", "jump", "revert_turn", "copy")
        for compact, width in ((False, 900), (True, 300), (True, 150)):
            surface = sf.ChatSurface(compact=compact)
            surface.tools = (("new_thread", "ADD"), ("attach_menu", "IMAGE_DATA"))
            surface.box.attachment = "ref.png"
            surface.view.revertable = True
            surface.view.scroll = 500.0
            top = sf.TopBar(mode_label="Local", mode_icon="CONSOLE", status="Ready", status_icon="CHECKMARK")
            hist = _history() * 6 + [{"role": "user", "content": "x", "turn_start": True},
                                     {"role": "assistant", "content": "Done.\n```python\nx=1\n```"}]
            canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), surface.interaction())
            surface.draw(canvas, lay.Rect(0, 0, width, 900), hist, top=top if compact else None)
            ids = [w for _r, w in canvas.hit_areas]
            bad = [(w, r.w, r.h) for r, w in canvas.hit_areas
                   if (w.startswith(icon_only) or (w in ("send", "stop") and surface.box._narrow))
                   and abs(r.w - r.h) > 0.5]
            self.assertEqual(bad, [], (compact, width))
            self.assertTrue(any(w.startswith("copy") for w in ids))

    def test_tooltip_after_the_mouse_rests(self):
        # #237: a resting mouse over a button shows its tooltip after TIP_DELAY, not before.
        now = [100.0]
        surface = sf.ChatSurface()
        surface.clock = lambda: now[0]
        _draw(surface)
        surface.on_move(*_centre(surface, "attach_menu"))
        canvas, _ = _draw(surface)
        tip = sf.TIPS["attach_menu"]
        self.assertGreater(surface.tip_wait, 0.0)                 # the host draws again then
        self.assertNotIn(tip, [c.text for c in canvas.commands if c.kind == "text"])
        now[0] += sf.TIP_DELAY + 0.01
        canvas, _ = _draw(surface)
        self.assertIn(tip, " ".join(c.text for c in canvas.commands if c.kind == "text"))
        self.assertEqual(surface.tip_wait, 0.0)
        surface.on_leave()
        canvas, _ = _draw(surface)
        self.assertNotIn(tip, " ".join(c.text for c in canvas.commands if c.kind == "text"))

    def test_every_button_has_a_tooltip(self):
        surface = sf.ChatSurface(compact=True)
        surface.tools = (("new_thread", "ADD"), ("mention", "OUTLINER_OB_MESH"), ("revert_turn", "LOOP_BACK"),
                         ("open_editor", "WINDOW"), ("", ""), ("attach_menu", "IMAGE_DATA"),
                         ("attach_render", "RENDER_STILL"), ("attach_screen", "FULLSCREEN_ENTER"))
        surface.box.attachment = "ref.png"
        surface.view.revertable = True
        top = sf.TopBar(mode_label="Local", mode_icon="CONSOLE", status="Ready", status_icon="CHECKMARK",
                        running=True, chat_modes=(("AGENT", "Coworker", "GHOST_ENABLED"), ("ASK", "Ask", "QUESTION"),
                                                  ("PLAN", "Plan", "LINENUMBERS_ON")), chat_mode="AGENT")
        hist = _history() + [{"role": "user", "content": "x", "turn_start": True},
                             {"role": "assistant", "content": "Done.\n```python\nx=1\n```\nShall I add a roof?"}]
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), surface.interaction())
        surface.draw(canvas, lay.Rect(0, 0, 500, 900), hist, top=top)
        missing = sorted({w for _r, w in canvas.hit_areas if w != "input" and not sf.tip_for(w)})
        self.assertEqual(missing, [])

    def test_chat_modes_are_one_joined_row(self):
        # Like Blender's aligned buttons (BW | RGB | RGBA): rounded only at the row's two ends.
        surface = sf.ChatSurface(compact=True)
        top = sf.TopBar(mode_label="Local", mode_icon="CONSOLE", status="Ready", status_icon="CHECKMARK",
                        chat_modes=(("AGENT", "Coworker", ""), ("ASK", "Ask", ""), ("PLAN", "Plan", "")),
                        chat_mode="AGENT")
        canvas, _ = _draw(surface, top=top)
        buttons = sorted((r for r, w in canvas.hit_areas if w.startswith("mode:")), key=lambda r: r.x)
        corners = [next(c.corners for c in canvas.commands if c.kind == "rect" and c.rect == r) for r in buttons]
        self.assertEqual(corners, ["L", "N", "R"])
        self.assertEqual(sf.row_corners(0, 1), "")

    def test_tool_row_along_the_bottom(self):
        # #214: the sidebar chat's icon buttons in a row along its bottom edge, under the input,
        # left to right; the rest of the row is still the resize grip.
        surface = sf.ChatSurface(compact=True)
        surface.on_resize = lambda h: None
        surface.tools = (("new_thread", "ADD"), ("", ""), ("attach_menu", "IMAGE_DATA"))
        canvas, _ = _draw(surface)
        rects = {w: r for r, w in canvas.hit_areas}
        self.assertEqual(rects["tool:new_thread"].y, rects["tool:attach_menu"].y)        # one row
        self.assertLess(rects["tool:new_thread"].x, rects["tool:attach_menu"].x)        # left to right
        self.assertLessEqual(rects["tool:new_thread"].top, surface.box.rect.y)          # under the input
        grip = rects["resize"]
        self.assertEqual(surface.hit(grip.right - 5, grip.y + grip.h / 2), "resize")
        self.assertEqual(surface._chat_rect.x, 0.0)                                     # no column any more
        self.assertEqual(_click(surface, "tool:attach_menu"), [("attach_menu", "")])

    def test_notice_shows_the_attached_image(self):
        # External Harness (#199): a dropped image shows under the notice, with its own note.
        surface = sf.ChatSurface()
        surface.box.attachment = "ref.png"
        surface.attachment_note = "Your client can look at it"
        notice = sf.Notice("External Harness", "WORLD", ("x",))
        canvas, _ = _draw(surface, notice=notice)
        texts = [c.text for c in canvas.commands if c.kind == "text"]
        self.assertTrue(any(t.startswith("ref.png") for t in texts))
        self.assertTrue(any(t.startswith("Your client") for t in texts))
        self.assertIn("attach_remove", [w for _r, w in canvas.hit_areas])
        self.assertEqual(_click(surface, "attach_remove", notice=notice), [("attachment_remove", "")])

    def test_notice_list_scrolls(self):
        # #243: the harness card's live list scrolls with the wheel; the title says which rows show.
        surface = sf.ChatSurface()
        rows = tuple(("CHECKMARK", "tool_{:d}".format(i), "ok") for i in range(40))
        notice = sf.Notice("External Harness", "WORLD", ("x",), rows=rows, rows_title="What your client did")
        canvas, _ = _draw(surface, notice=notice)
        texts = [c.text for c in canvas.commands if c.kind == "text"]
        self.assertIn("tool_0", texts)
        self.assertTrue(any(t.startswith("What your client did  (1-") for t in texts))
        room = surface._notice_room
        for _ in range(5):
            self.assertTrue(surface.on_wheel(False, False, room.x + 5, room.y + 5))
        canvas, _ = _draw(surface, notice=notice)
        texts = [c.text for c in canvas.commands if c.kind == "text"]
        self.assertNotIn("tool_0", texts)
        self.assertIn("tool_5", texts)
        for _ in range(99):
            surface.on_wheel(False, False, room.x + 5, room.y + 5)
        canvas, _ = _draw(surface, notice=notice)
        self.assertIn("tool_39", [c.text for c in canvas.commands if c.kind == "text"])   # stops at the end

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


class TestApproval(unittest.TestCase):

    def test_card_above_the_input_with_three_answers(self):
        surface = sf.ChatSurface()
        card = sf.Approval(title="Claude Code wants to run bash", detail="ls -la", waiting=2,
                           note="The agent's own tool, outside Blender")
        canvas, _ = _draw(surface, approval=card)
        texts = " ".join(c.text for c in canvas.commands if c.kind == "text")
        self.assertIn("(+1 more)", texts)
        self.assertIn("ls -la", texts)
        widgets = [w for _r, w in surface._hits]
        for choice in ("once", "always", "deny"):
            self.assertIn("approve:" + choice, widgets)
        button = next(r for r, w in surface._hits if w == "approve:once")
        box = next(r for r, w in surface._hits if w == "input")
        self.assertGreater(button.y, box.top)                      # sits above the input
        self.assertEqual(_click(surface, "approve:deny", approval=card), [("approve", "deny")])

    def test_narrow_sidebar_stacks_the_buttons(self):
        surface = sf.ChatSurface(compact=True)
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), surface.interaction())
        surface.draw(canvas, lay.Rect(0, 0, 160, 600), [],
                     approval=sf.Approval(title="OpenCode wants to run bash"))
        rects = {w: r for r, w in surface._hits if w.startswith("approve:")}
        self.assertGreater(rects["approve:once"].y, rects["approve:deny"].y)


class TestRevertButton(unittest.TestCase):

    def test_only_on_the_last_reply_when_revertable(self):
        surface = sf.ChatSurface()
        _draw(surface)
        self.assertNotIn("revert_turn", [w for _r, w in surface._hits])
        surface.view.revertable = True
        _draw(surface)
        self.assertIn("revert_turn", [w for _r, w in surface._hits])
        self.assertEqual(_click(surface, "revert_turn"), [("revert_turn", "")])


class TestShortSidebar(unittest.TestCase):
    """#182: a compact chat shorter than its panel keeps its top bar and input apart."""

    def _draw(self, h):
        surface = sf.ChatSurface(compact=True)
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), surface.interaction())
        top = sf.TopBar(status="Connected", chat_modes=(("AGENT", "Coworker"), ("ASK", "Ask")), chat_mode="AGENT")
        surface.draw(canvas, lay.Rect(0, 0, 300, h), _history() * 4, top=top)
        return surface

    def test_messages_stay_between_the_bar_and_the_input(self):
        for h in (600, 260, 160):
            surface = self._draw(h)
            box = next(r for r, w in surface._hits if w == "input")
            start = next(r for r, w in surface._hits if w in ("start", "stop_agent"))
            chat = surface._chat_rect
            self.assertGreaterEqual(chat.y, box.top)                 # above the input
            self.assertLessEqual(chat.top, start.y)                  # below the top bar

    def test_mode_row_goes_first_when_short(self):
        self.assertTrue(any(w.startswith("mode:") for _r, w in self._draw(600)._hits))
        self.assertFalse(any(w.startswith("mode:") for _r, w in self._draw(160)._hits))


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


class TestPolish(unittest.TestCase):

    def test_altgr_types_the_at_sign(self):
        box = ib.InputBox(focused=True)
        self.assertTrue(box.handle_key(ib.Key("TWO", unicode="@", alt=True)))   # Blender: AltGr is Alt on Windows
        self.assertTrue(box.handle_key(ib.Key("Q", unicode="@", ctrl=True, alt=True)))   # ...or Ctrl+Alt elsewhere
        self.assertEqual(box.model.text, "@@")

    def test_attachment_tray_above_the_input(self):
        surface = sf.ChatSurface()
        surface.box.attachment = "a_really_long_image_name_" * 6 + ".png"
        canvas, _ = _draw(surface)
        names = [c for c in canvas.commands if c.kind == "text" and "really" in c.text]
        self.assertTrue(names and names[0].text.endswith("…"))          # cut to fit
        self.assertGreater(names[0].rect.y, surface.box.rect.top)            # above the input box
        self.assertIn(surface.box.attachment, [c.text for c in canvas.commands if c.kind == "image"])
        self.assertEqual(_click(surface, "attach_pin"), [("attachment_pin", "")])
        self.assertEqual(_click(surface, "attach_remove"), [("attachment_remove", "")])
        surface.attachment_pinned = True
        canvas, _ = _draw(surface)
        self.assertIn("Kept for every message", [c.text for c in canvas.commands if c.kind == "text"])

    def test_box_starts_multi_line_with_buttons_below(self):
        box = ib.InputBox(focused=True)
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55))
        box.draw(canvas, lay.Rect(0, 0, 400, 300))
        send = next(r for r, w in canvas.hit_areas if w == "send")
        self.assertGreaterEqual(box.rect.h, 2 * canvas.style.line_height + send.h)   # 2 lines + the button row
        caret = [c for c in canvas.commands if c.kind == "rect" and c.color == canvas.style.cursor]
        self.assertTrue(caret)                                    # caret shows even with nothing typed
        self.assertNotIn("native_edit", [w for _r, w in canvas.hit_areas])   # Edit moved to the Chat menu

    def test_resize_grip_and_drop_target(self):
        heights = []
        surface = sf.ChatSurface(compact=True)
        surface.on_resize = heights.append
        surface.bounds = RECT
        canvas, _ = _draw(surface, top=sf.TopBar())
        x, y = _centre(surface, "resize")
        surface.on_press(x, y)
        surface.on_move(x, y - 40)
        self.assertTrue(heights and heights[-1] > RECT.h)          # dragged down: taller
        surface.on_release(x, y - 40)
        surface.drop_hover = True
        canvas, _ = _draw(surface, top=sf.TopBar())
        self.assertIn("Drop to attach the image", [c.text for c in canvas.commands if c.kind == "text"])
