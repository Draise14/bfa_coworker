# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Edit by Transcript's Blender side (#498, ``transcript_edit_ops.py``) on a stand-in ``bpy``:
register puts the operators and the Text Editor panel in, the header button, the line overlay, the
frame / load handlers and the timer; unregister takes every one of them out again.  The text's link
(``state_of`` / ``pending``) and the report line.

Run with::

    python -m unittest tests.test_transcript_edit_ops -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest

import test_wand_remove_silences as W


class _Hooks(list):
    def append(self, fn):
        super().append(fn)

    def remove(self, fn):
        super().remove(fn)


class _Space:
    handlers: list = []

    @classmethod
    def draw_handler_add(cls, fn, args, region, kind):
        cls.handlers.append((fn, region, kind))
        return len(cls.handlers)

    @classmethod
    def draw_handler_remove(cls, handle, region):
        cls.handlers.pop()


def _fake_bpy():
    bpy = types.ModuleType("bpy")
    registered = []
    timers = []
    header = _Hooks()
    _Space.handlers = []
    bpy.types = types.SimpleNamespace(Operator=type("Operator", (), {}), Panel=type("Panel", (), {}),
                                      TEXT_HT_header=header, SpaceTextEditor=_Space)
    bpy.props = types.SimpleNamespace(BoolProperty=lambda **kw: ("bool", kw), IntProperty=lambda **kw: ("int", kw),
                                      StringProperty=lambda **kw: ("str", kw))
    bpy.utils = types.SimpleNamespace(register_class=registered.append, unregister_class=registered.remove)
    bpy.app = types.SimpleNamespace(
        handlers=types.SimpleNamespace(frame_change_post=[], load_post=[], persistent=lambda fn: fn),
        timers=types.SimpleNamespace(register=lambda fn, first_interval=0.0, persistent=False: timers.append(fn),
                                     unregister=timers.remove, is_registered=lambda fn: fn in timers))
    bpy.path = types.SimpleNamespace(abspath=lambda p, library=None: p)
    bpy.data = types.SimpleNamespace(filepath="", texts=[], scenes={})
    bpy.registered, bpy.timers_list, bpy.header = registered, timers, header
    return bpy


def _load(bpy):
    names = ("bpy", "bpy.props", "bpy.types")
    saved = {name: sys.modules.get(name) for name in names}
    sys.modules.update({"bpy": bpy, "bpy.props": bpy.props, "bpy.types": bpy.types})
    try:
        W._package()
        sys.modules.pop(W._PKG + ".transcript_edit_ops", None)
        return importlib.import_module(W._PKG + ".transcript_edit_ops")
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class _Text(dict):
    def __init__(self, name, body, prop=None):
        super().__init__()
        self.name, self.body = name, body
        if prop is not None:
            self["bfacw_transcript"] = prop

    def as_string(self):
        return self.body


class TestWiring(unittest.TestCase):

    def test_register_and_unregister_leave_nothing_behind(self):
        bpy = _fake_bpy()
        ops = _load(bpy)
        ops.register()
        names = [c.__name__ for c in bpy.registered]
        for name in ("BFACW_OT_open_transcript", "BFACW_OT_transcript_apply", "BFACW_OT_transcript_remove_fillers",
                     "BFACW_OT_transcript_seek", "BFACW_OT_transcript_jump_to_playhead", "BFACW_PT_transcript"):
            self.assertIn(name, names)
        self.assertEqual(len(bpy.header), 1)
        self.assertEqual(len(_Space.handlers), 1)
        self.assertEqual(len(bpy.app.handlers.frame_change_post), 1)
        self.assertEqual(len(bpy.app.handlers.load_post), 1)
        self.assertEqual(len(bpy.timers_list), 1)
        ops.register()                          # a second register doesn't double the hooks
        self.assertEqual(len(_Space.handlers), 1)
        self.assertEqual(len(bpy.timers_list), 1)
        ops.unregister()
        ops.unregister()
        self.assertEqual(bpy.registered, [])
        self.assertEqual(list(bpy.header), [])
        self.assertEqual(_Space.handlers, [])
        self.assertEqual(bpy.app.handlers.frame_change_post, [])
        self.assertEqual(bpy.app.handlers.load_post, [])
        self.assertEqual(bpy.timers_list, [])

    def test_panel_is_in_the_text_editor(self):
        ops = _load(_fake_bpy())
        self.assertEqual(ops.BFACW_PT_transcript.bl_space_type, "TEXT_EDITOR")
        self.assertEqual(ops.BFACW_PT_transcript.bl_category, "Coworker")
        self.assertIn("UNDO", ops.BFACW_OT_transcript_apply.bl_options)
        self.assertIn("UNDO", ops.BFACW_OT_transcript_remove_fillers.bl_options)


class TestLink(unittest.TestCase):

    def setUp(self):
        self.ops = _load(_fake_bpy())

    def test_only_transcript_texts_count(self):
        self.assertIsNone(self.ops.state_of(_Text("notes", "hello")))
        self.assertIsNone(self.ops.state_of(None))
        text = _Text("talk transcript", "[0:01.0] Hi.\n", {"source": "c:/media/talk.mp4", "applied": ""})
        self.assertEqual(self.ops.state_of(text)["source"], "c:/media/talk.mp4")

    def test_pending_edits(self):
        body = "[0:01.0] Hi there.\n"
        text = _Text("t", body, {"source": "x", "applied": self.ops._hash(body)})
        self.assertFalse(self.ops.pending(text))
        text.body = "[0:01.0] Hi.\n"
        self.assertTrue(self.ops.pending(text))

    def test_report_line(self):
        msg = self.ops._message({"cut_words": 7, "retexted": 1, "moved": False, "flags": []},
                                {"seconds_removed": 4.0}, None, 1, 0)
        self.assertEqual(msg, "Cut 7 words (4.0 s); 1 word changed; 1 caption updated")
        flagged = self.ops._message({"cut_words": 0, "retexted": 0, "moved": False,
                                     "flags": [{"line": 3, "message": "no audio behind this text: nothing is added "
                                                "to the video"}]}, None, None, 0, 0)
        self.assertTrue(flagged.startswith("1 line flagged (line 3)"))
        self.assertEqual(self.ops._message({"cut_words": 0, "retexted": 0, "moved": False, "flags": []},
                                           None, None, 0, 0), "Nothing to apply: the text matches the timeline")


if __name__ == "__main__":
    unittest.main()
