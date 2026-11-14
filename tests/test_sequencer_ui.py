# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for #504: the Sequencer's Coworker UI (bpy-free, stand-in modules).

1. ``wand/scene.py``: the scene the Sequencer shows -- ``context.sequencer_scene``
   first (Bforartists gives the window's scene there while nothing is pinned), then
   the workspace's (``None`` = no scene, not the window's scene), the window's scene
   only where there is no ``sequencer_scene`` at all; the poll and its reasons.
2. The Video panel's polls in Sequencer, Sequencer & Preview and Preview, with
   Bforartists' toolshelf tabs on and off; the Strip-menu section in the same views;
   the toolshelf header's icon; the sidebar panel first in the Coworker tab.
3. The three tools' polls (Remove Silences, Auto Captions, Make Vertical) with no
   Sequencer scene, a scene without strips, and a scene with strips; Import / Export
   SRT the same.
4. The Translate Text Strip context entry reads the Sequencer's scene.

Run with::

    python -m unittest tests.test_sequencer_ui -v
"""

__all__ = ()

import importlib
import re
import sys
import types
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

import test_captions as C
import test_wand_remove_silences as W

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
SC = W._import("wand.scene")


class _Op:
    """An operator class stand-in: records poll_message_set."""

    def __init__(self):
        self.messages = []

    def poll_message_set(self, text):
        self.messages.append(text)


def _scene(name="Edit", strips=True):
    return SimpleNamespace(name=name, sequence_editor=SimpleNamespace(strips=[]) if strips else None)


def _ctx(sequencer_scene=None, workspace="none", scene=None, view="SEQUENCER", tabs=None):
    """A context: *workspace* "none" = no workspace, "old" = one without sequencer_scene (before
    5.0), else the scene (or None) the workspace pins."""
    if workspace == "none":
        ws = None
    elif workspace == "old":
        ws = SimpleNamespace()
    else:
        ws = SimpleNamespace(sequencer_scene=workspace)
    space = SimpleNamespace(view_type=view)
    if tabs is not None:
        space.show_toolshelf_tabs = tabs
    return SimpleNamespace(sequencer_scene=sequencer_scene, workspace=ws, scene=scene, space_data=space)


class TestSequencerScene(unittest.TestCase):

    def test_context_first(self):
        edit, window = _scene("Edit"), _scene("Window")
        self.assertIs(SC.sequencer_scene(_ctx(edit, workspace=None, scene=window)), edit)

    def test_bforartists_unpinned_is_the_window_scene(self):
        # Bforartists: context.sequencer_scene is the window's scene while nothing is pinned.
        window = _scene("Window")
        self.assertIs(SC.sequencer_scene(_ctx(window, workspace=None, scene=window)), window)

    def test_workspace_next(self):
        edit, window = _scene("Edit"), _scene("Window")
        self.assertIs(SC.sequencer_scene(_ctx(None, workspace=edit, scene=window)), edit)

    def test_no_scene_is_none_not_the_window_scene(self):
        # Blender: the Sequencer shows no scene; the window's scene is another one.
        self.assertIsNone(SC.sequencer_scene(_ctx(None, workspace=None, scene=_scene("Window"))))

    def test_window_scene_only_without_sequencer_scenes(self):
        window = _scene("Window")
        self.assertIs(SC.sequencer_scene(_ctx(None, workspace="old", scene=window)), window)   # before 5.0
        self.assertIs(SC.sequencer_scene(_ctx(None, workspace="none", scene=window)), window)  # timer, background

    def test_problem_and_poll(self):
        op = _Op()
        self.assertFalse(SC.poll(op, _ctx(None, workspace=None, scene=_scene("Window"))))
        self.assertEqual(op.messages, [SC.NO_SCENE])
        op = _Op()
        self.assertFalse(SC.poll(op, _ctx(_scene("Empty", strips=False), workspace=None)))
        self.assertEqual(op.messages, ["The Sequencer's scene “Empty” has no strips yet"])
        op = _Op()
        self.assertTrue(SC.poll(op, _ctx(_scene("Edit"), workspace=None)))
        self.assertEqual(op.messages, [])
        self.assertEqual(SC.sequencer_problem(_ctx(_scene("Edit"))), "")

    def test_no_bpy_needed(self):
        src = (_ADDON / "wand" / "scene.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"(?m)^import bpy|^from bpy")


class TestPanels(unittest.TestCase):

    def setUp(self):
        self.seq = W._load_sequencer(W._fake_bpy(True))

    def _polls(self, view, tabs):
        ctx = _ctx(view=view, tabs=tabs)
        return (bool(self.seq.COWORKER_PT_sequencer_toolshelf.poll(ctx)),
                bool(self.seq.COWORKER_PT_sequencer_sidebar.poll(ctx)))

    def test_bforartists_tabs_on(self):
        # (toolshelf, sidebar): the toolshelf in both views with a timeline.
        self.assertEqual(self._polls("SEQUENCER", True), (True, False))
        self.assertEqual(self._polls("SEQUENCER_PREVIEW", True), (True, False))
        # Preview alone: like Bforartists' Strip-tab panels (only its Image tab shows there).
        self.assertEqual(self._polls("PREVIEW", True), (False, False))

    def test_bforartists_tabs_off_and_blender(self):
        for tabs in (False, None):          # None: Blender's space has no show_toolshelf_tabs
            self.assertEqual(self._polls("SEQUENCER", tabs), (False, True))
            self.assertEqual(self._polls("SEQUENCER_PREVIEW", tabs), (False, True))
            self.assertEqual(self._polls("PREVIEW", tabs), (False, False))

    def test_the_tools_reachable_in_every_timeline_view(self):
        for view in ("SEQUENCER", "SEQUENCER_PREVIEW"):
            for tabs in (True, False, None):
                self.assertIn(True, self._polls(view, tabs), (view, tabs))

    def test_strip_menu_section_per_view(self):
        for view, shown in (("SEQUENCER", True), ("SEQUENCER_PREVIEW", True), ("PREVIEW", False)):
            layout = _Layout()
            self.seq._draw_strip_menu(SimpleNamespace(layout=layout), _ctx(view=view))
            self.assertEqual(bool(layout.calls), shown, view)

    def test_toolshelf_header_icon(self):
        layout = _Layout()
        self.seq.COWORKER_PT_sequencer_toolshelf.draw_header(SimpleNamespace(layout=layout), None)
        self.assertEqual(layout.calls, [("label", "", "FILE_MOVIE")])
        self.assertEqual(self.seq.COWORKER_PT_sequencer_toolshelf.bl_label, "Video")
        self.assertEqual(self.seq.COWORKER_PT_sequencer_sidebar.bl_label, "Video")

    def test_sidebar_panel_first_in_the_coworker_tab(self):
        # Queue, Plan, the chat ... are copied into the Sequencer's sidebar with their bl_order.
        # Blender keeps bl_order >= 0 (-1 becomes 0) and orders equal ones by registration.
        orders = []
        for name in ("ui_chat.py", "chat_editor.py"):
            orders += [int(v) for v in re.findall(r"(?m)^\s+bl_order = (-?\d+)",
                                                   (_ADDON / name).read_text(encoding="utf-8"))]
        self.assertTrue(orders)
        self.assertEqual(self.seq.COWORKER_PT_sequencer_sidebar.bl_order, 0)
        self.assertLessEqual(self.seq.COWORKER_PT_sequencer_sidebar.bl_order, min(orders))
        # ... so wand registers before the chat's panels.
        body = (_ADDON / "__init__.py").read_text(encoding="utf-8").split("\ndef _register(", 1)[1]
        body = body.split("\ndef ", 1)[0]
        self.assertLess(body.index("wand.register()"), body.index("ui_chat.register()"))
        self.assertLess(body.index("wand.register()"), body.index("chat_editor.register()"))


class _Layout:
    operator_context = ""

    def __init__(self):
        self.calls = []

    def separator(self, **_kw):
        self.calls.append(("separator",))

    def operator(self, idname, **kw):
        self.calls.append(("operator", idname))
        return SimpleNamespace()

    def menu(self, menu_id, **kw):
        self.calls.append(("menu", menu_id))

    def label(self, text="", icon="NONE", **_kw):
        self.calls.append(("label", text, icon))


# -- the operators' polls ------------------------------------------------------------------

def _states():
    """(name, context, expected message or "") for the three Sequencer states."""
    window = _scene("Window")                  # the window's scene has strips: it must not count
    return (("no scene", _ctx(None, workspace=None, scene=window), SC.NO_SCENE),
            ("no strips", _ctx(_scene("Empty", strips=False), workspace=None, scene=window),
             SC.NO_STRIPS.format("Empty")))


class TestRemoveSilencesPoll(unittest.TestCase):

    def test_poll(self):
        seq = W._load_sequencer(W._fake_bpy(False))
        cls = seq.COWORKER_OT_sequencer_remove_silences
        for name, ctx, message in _states():
            messages = []
            with unittest.mock.patch.object(cls, "poll_message_set", messages.append, create=True):
                self.assertFalse(cls.poll(ctx), name)
            self.assertEqual(messages, [message], name)
        self.assertTrue(cls.poll(_ctx(_scene("Edit"), workspace=None)))


class TestCaptionsPolls(unittest.TestCase):

    def setUp(self):
        self.ops = C._ops()

    def _poll(self, cls, ctx):
        messages = []
        with unittest.mock.patch.object(cls, "poll_message_set", messages.append, create=True):
            return cls.poll(ctx), messages

    def test_auto_captions(self):
        cls = self.ops.BFACW_OT_auto_captions
        for name, ctx, message in _states():
            self.assertEqual(self._poll(cls, ctx), (False, [message]), name)
        scene = C._Scene()
        C._clip(scene)                        # a selected sound strip
        self.assertEqual(self._poll(cls, _ctx(scene, workspace=None)), (True, []))
        scene.sequence_editor.strips[0].select = False
        ok, messages = self._poll(cls, _ctx(scene, workspace=None))
        self.assertFalse(ok)
        self.assertEqual(messages, ["Select a sound or movie strip first"])

    def test_export_srt(self):
        cls = self.ops.BFACW_OT_captions_export_srt
        for name, ctx, message in _states():
            self.assertEqual(self._poll(cls, ctx), (False, [message]), name)

    def test_import_srt(self):
        # Import makes the strips if the scene has none: it needs the Sequencer's scene only.
        cls = self.ops.BFACW_OT_captions_import_srt
        window = _scene("Window")
        self.assertEqual(self._poll(cls, _ctx(None, workspace=None, scene=window)), (False, [SC.NO_SCENE]))
        self.assertEqual(self._poll(cls, _ctx(_scene("Empty", strips=False), workspace=None, scene=window)),
                         (True, []))

    def test_seq_scene_keeps_the_window_fallback_for_tools(self):
        # The MCP tool and timers have no Sequencer: they still get the context's scene.
        window = _scene("Window")
        self.assertIs(self.ops.seq_scene(_ctx(None, workspace="none", scene=window)), window)
        edit = _scene("Edit")
        self.assertIs(self.ops.seq_scene(_ctx(None, workspace=edit, scene=window)), edit)


def _fake_reframe_bpy():
    bpy = types.ModuleType("bpy")
    props = types.ModuleType("bpy.props")
    for n in ("BoolProperty", "EnumProperty", "FloatProperty", "IntProperty"):
        setattr(props, n, lambda **_k: None)
    bpy.props = props
    bpy.types = SimpleNamespace(Operator=type("Operator", (), {}), Menu=type("Menu", (), {}))
    bpy.app = SimpleNamespace(handlers=SimpleNamespace(persistent=lambda fn: fn))
    return {"bpy": bpy, "bpy.props": props, "bpy.types": bpy.types}


def _reframe():
    name = W._PKG + ".reframe"
    if name in sys.modules:
        return sys.modules[name]
    fakes = _fake_reframe_bpy()
    saved = {k: sys.modules.get(k) for k in fakes}
    sys.modules.update(fakes)
    try:
        W._package()
        return importlib.import_module(name)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class TestMakeVerticalPoll(unittest.TestCase):

    def test_poll(self):
        rf = _reframe()
        cls = rf.BFACW_OT_make_vertical
        for name, ctx, message in _states():
            messages = []
            with unittest.mock.patch.object(cls, "poll_message_set", messages.append, create=True):
                self.assertFalse(cls.poll(ctx), name)
            self.assertEqual(messages, [message], name)
        self.assertTrue(cls.poll(_ctx(_scene("Edit"), workspace=None)))

    def test_sequencer_scene_keeps_the_window_fallback_for_tools(self):
        rf = _reframe()
        window = _scene("Window")
        self.assertIs(rf.sequencer_scene(_ctx(None, workspace="none", scene=window)), window)
        edit = _scene("Edit")
        self.assertIs(rf.sequencer_scene(_ctx(edit, workspace=None, scene=window)), edit)


class TestTextStripContextMenu(unittest.TestCase):

    def test_reads_the_sequencer_scene(self):
        src = (_ADDON / "ui_context_menus.py").read_text(encoding="utf-8")
        body = src.split("def _draw_text_strip_menu", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("sequencer_scene(context)", body)
        self.assertNotIn("context.scene", body)
        # Run it: the Sequencer shows "Edit" (a text strip active), the window "Window" (none).
        ns = {"Any": object, "_python_path": lambda pointer, identifier: "x." + identifier, "drawn": []}
        ns["_translate_op"] = lambda layout, path, current, what, label: ns["drawn"].append((path, current, label))
        code = "def _draw_text_strip_menu" + body.replace(
            "from .wand.scene import sequencer_scene", "sequencer_scene = SC.sequencer_scene")
        ns["SC"] = SC
        exec(compile(code, "ui_context_menus.py", "exec"), ns)  # noqa: S102  pylint: disable=exec-used
        text = SimpleNamespace(type="TEXT", text="Hello", name="title")
        edit = SimpleNamespace(name="Edit", sequence_editor=SimpleNamespace(active_strip=text))
        window = SimpleNamespace(name="Window", sequence_editor=SimpleNamespace(
            active_strip=SimpleNamespace(type="COLOR", name="card")))
        menu = SimpleNamespace(layout=_Layout())
        ns["_draw_text_strip_menu"](menu, _ctx(None, workspace=edit, scene=window))
        self.assertEqual(ns["drawn"], [("x.text", "Hello", "Translate Text Strip...")])
        ns["drawn"].clear()
        ns["_draw_text_strip_menu"](menu, _ctx(None, workspace=None, scene=window))   # no Sequencer scene
        self.assertEqual(ns["drawn"], [])


if __name__ == "__main__":
    unittest.main()
