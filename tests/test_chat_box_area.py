# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Typing in a GPU chat acts on that chat's own area (its tab), not on the area the
keyboard modal was started in.

With two chats in a window, or after switching tabs, the modal still carries the
first chat's context.  The draft is stored on the thread of the area in context,
so without the area override the typed text went to the other chat's tab (the box
was then reset to its own empty draft: "I can't write") and Enter sent there too.

Run with::

    python -m unittest tests.test_chat_box_area -v
"""

__all__ = ()

import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


class _Region:
    def __init__(self, name):
        self.name = name


class _Context:
    """bpy.context whose temp_override really swaps window / area / region."""

    def __init__(self, window, area):
        self.window, self.area, self.region = window, area, None
        self.window_manager = None

    def temp_override(self, **kw):
        ctx = self

        class _Override:
            def __enter__(self):
                self.saved = {k: getattr(ctx, k, None) for k in kw}
                for k, v in kw.items():
                    setattr(ctx, k, v)
                return self

            def __exit__(self, *_exc):
                for k, v in self.saved.items():
                    setattr(ctx, k, v)
                return False

        return _Override()


class _Props:
    """chat_input reads and writes the draft of the thread of the area in context,
    like ui_chat._draft_get / _draft_set."""

    def __init__(self, bpy, drafts):
        self._bpy, self._drafts = bpy, drafts

    @property
    def chat_input(self):
        return self._drafts.get(self._bpy.context.area.name, "")

    @chat_input.setter
    def chat_input(self, value):
        self._drafts[self._bpy.context.area.name] = value


def _source(src, start_marker, end_marker):
    start = src.index(start_marker)
    return src[start:src.index(end_marker, start)]


def _load(bpy, surfaces, props, sends):
    src = (_ADDON / "chat_editor.py").read_text(encoding="utf-8")
    code = (_source(src, "def _window_override(", "def _do(")
            + _source(src, "def _region_for_box(", "# -- Operators"))

    def _do(action, value, surface, region=None):
        if action == "send":
            props.chat_input = value
            sends.append((bpy.context.area.name, value))

    ns = {"bpy": bpy, "Any": object, "InputBox": object, "_SURFACES": surfaces, "_SYNCED": {},
          "_props": lambda: props, "_do": _do,
          "_surface_for_box": lambda box: next((s for s, _r in surfaces.values() if s.box is box), None)}
    exec(compile("from __future__ import annotations\n" + code, "chat_editor.py", "exec"), ns)  # noqa: S102
    return ns


class TestTypingInTheSecondChat(unittest.TestCase):

    def setUp(self):
        self.region_all, self.region_tab = _Region("all"), _Region("tab")
        self.area_all = types.SimpleNamespace(name="all", regions=[self.region_all])
        self.area_tab = types.SimpleNamespace(name="tab", regions=[self.region_tab])
        window = types.SimpleNamespace(screen=types.SimpleNamespace(areas=[self.area_all, self.area_tab]))
        bpy = types.SimpleNamespace()
        # The keyboard modal was started in the All editors chat.
        bpy.context = _Context(window, self.area_all)
        bpy.context.window_manager = types.SimpleNamespace(windows=[window])
        self.bpy = bpy
        self.drafts, self.sends = {}, []
        self.box_all = types.SimpleNamespace(model=types.SimpleNamespace(text=""))
        self.box_tab = types.SimpleNamespace(model=types.SimpleNamespace(text=""))
        surfaces = {1: (types.SimpleNamespace(box=self.box_all), self.region_all),
                    2: (types.SimpleNamespace(box=self.box_tab), self.region_tab)}
        self.props = _Props(bpy, self.drafts)
        self.ns = _load(bpy, surfaces, self.props, self.sends)

    def test_a_key_goes_to_the_draft_of_the_chat_typed_in(self):
        self.box_tab.model.text = "hello"
        self.ns["_after_key"](self.box_tab)
        self.assertEqual(self.drafts, {"tab": "hello"})
        self.assertIs(self.bpy.context.area, self.area_all, "the modal's own context is left as it was")

    def test_the_first_chat_still_gets_its_own_keys(self):
        self.box_all.model.text = "hi"
        self.ns["_after_key"](self.box_all)
        self.assertEqual(self.drafts, {"all": "hi"})

    def test_enter_sends_from_the_chat_typed_in(self):
        self.ns["_on_box_action"](self.box_tab, "send", "make a cube")
        self.assertEqual(self.sends, [("tab", "make a cube")])
        self.assertEqual(self.drafts, {"tab": "make a cube"})

    def test_a_box_whose_region_is_gone_keeps_the_modal_context(self):
        stray = types.SimpleNamespace(model=types.SimpleNamespace(text="x"))
        self.ns["_after_key"](stray)
        self.assertEqual(self.drafts, {"all": "x"})


if __name__ == "__main__":
    unittest.main()
