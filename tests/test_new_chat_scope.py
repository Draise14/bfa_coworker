# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
New Chat (#466): the + starts a chat for All editors everywhere; one editor's context is
an opt-in; Start This Chat Fresh is in the menus.

1. ``bfacw.tab_new``: scope ALL (the default, the +) from a sidebar makes a tab for All
   editors and the area shows it; scope EDITOR makes the editor's.  Its tooltip follows.
2. The menu entries (the tab menu, the Chat menu, the right-click menu): New Chat, then
   New Chat for This <editor> -- not in the Coworker editor -- then Start This Chat Fresh,
   greyed out while that chat's turn runs.
3. ``bfacw.chat_clear`` (Start This Chat Fresh) on another tab keeps the tab, saves the
   old chat and gives it a new chat id; on All editors it starts it fresh; it refuses
   while that chat's turn runs, and acts on the tab it was drawn for.
4. A second All-editors tab is told from the default one by its title.

Runs the code sliced from ui_chat.py with stand-ins (no Blender needed)::

    python -m unittest tests.test_new_chat_scope -v
"""

__all__ = ()

import importlib.util
import sys
import threading
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_newchat_pkg"


def _module(name):
    spec = importlib.util.spec_from_file_location("bfacw_ncs_" + name, _ADDON / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod        # dataclasses look their module up
    spec.loader.exec_module(mod)
    return mod


threads = _module("threads")


def _slice(src, start_marker, end_marker):
    start = src.index(start_marker)
    return src[start:src.index(end_marker, start)]


class _Space:
    def __init__(self, ptr, tree_type=""):
        self.ptr, self.tree_type = ptr, tree_type

    def as_pointer(self):
        return self.ptr


class _Area:
    def __init__(self, kind, ptr, coworker=False, tree_type=""):
        self.type, self.coworker = kind, coworker
        self.spaces = types.SimpleNamespace(active=_Space(ptr, tree_type))


class _State:
    def __init__(self):
        self.conversation_history, self.streaming_text, self.reasoning_text = [], "", ""
        self.turn_active = False

    def reset_usage(self):
        pass


class _Store:
    def __init__(self):
        self.resets = 0

    def reset(self):
        self.resets += 1


class _Layout:
    """Records the operators drawn, with the row they are in."""

    def __init__(self, log, enabled=True):
        self.log, self.enabled = log, enabled

    def row(self, align=False):
        return _Layout(self.log)

    def separator(self):
        self.log.append(("separator",))

    def operator(self, idname, text="", icon="NONE"):
        props = types.SimpleNamespace(scope="ALL", thread_id="")
        self.log.append(("op", idname, text, icon, props, self))
        return props


class _Base(unittest.TestCase):

    def setUp(self):
        src = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
        self.reg = threads.ThreadRegistry(make_state=_State, make_store=_Store, default_objects=True)
        self.pins, self.saved, self.fresh_default, self.ids = {}, [], [], iter(range(100))
        states = {}

        def thread_state(thread):
            return thread.state if thread.state is not None else states.setdefault(thread.id, _State())

        reg = self.reg
        self.ac = types.SimpleNamespace(
            THREADS=reg, thread_state=thread_state, _reset_session_domains=lambda _t=None: None,
            clear_session_scene_lock=lambda _t=None: None, _clear_coworker_text_blocks=lambda _t=None: None)
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = []
        pkg.threads = threads
        pkg.session_memory = types.SimpleNamespace(store_lock=threading.Lock())
        pkg.chat_editor = types.SimpleNamespace(is_coworker_space=lambda space: False)
        for name, mod in ((_PKG, pkg), (_PKG + ".threads", threads), (_PKG + ".session_memory", pkg.session_memory)):
            sys.modules[name] = mod
            self.addCleanup(sys.modules.pop, name, None)
        pins = self.pins

        def ui_thread(context=None):        # as ui_chat._ui_thread: the area's pin, else All editors
            area = getattr(context, "area", None)
            return threads.pick(reg, "", pinned=pins.get(area.spaces.active.ptr, "")) if area else reg.default

        props = types.SimpleNamespace(StringProperty=lambda **kw: "", EnumProperty=lambda **kw: kw.get("default"))
        bpy = types.SimpleNamespace(types=types.SimpleNamespace(Context=object, Operator=object), props=props)
        self.ns = {
            "__name__": _PKG + ".ui_chat", "__package__": _PKG, "bpy": bpy, "Operator": object,
            "StringProperty": props.StringProperty, "EnumProperty": props.EnumProperty,
            "agent_controller": self.ac, "_ui_thread": ui_thread, "_acp": None,
            "_is_coworker_area": lambda area: area.coworker,
            "SIDEBAR_SPACES": (("VIEW_3D", "", "", "VIEW3D"), ("NODE_EDITOR", "", "", "NODETREE")),
            "SIDEBAR_TYPES": frozenset({"VIEW_3D", "NODE_EDITOR"}),
            "_EDITOR_NAMES": {"VIEW_3D": "3D Viewport", "NODE_EDITOR": "Node Editor"},
            "_AREA_PINS": pins, "_pin_key": lambda area: area.spaces.active.ptr,
            "_focus_thread": lambda _t: None, "_redraw_all_chats": lambda: None,
            "_chat_index": lambda: types.SimpleNamespace(new_id=lambda: "chat{:d}".format(next(self.ids))),
            "_load_thread_chat": lambda _t: None,
            "_save_thread_chat": lambda t: self.saved.append(t.chat_id),
            "_save_tabs": lambda: None, "_fresh_default_chat": lambda: self.fresh_default.append(True),
            "safe_icon": lambda name: name,
        }
        code = "\n\n\n".join((
            _slice(src, "def _editor_thread(", "\n\n\n# -- Threads 2c"),
            _slice(src, "def _show_in_area(", "\n\n\ndef _redraw_all_chats("),
            _slice(src, "class BFACW_OT_chat_clear(", "\n\n\ndef _fresh_default_chat("),
            _slice(src, "def _new_chat_in_thread(", "\n\n\n_RECENT"),
            _slice(src, "def _editor_icon(", "\n\n\ndef _tab_label("),
            _slice(src, "# New Chat (#466)", "\n\n\ndef draw_chat_tabs("),
            _slice(src, "class BFACW_OT_tab_new(", "\n\n\nclass "),
        ))
        exec(compile(code, "ui_chat.py", "exec"), self.ns)  # noqa: S102
        self.view3d = _Area("VIEW_3D", 1)
        self.coworker = _Area("NODE_EDITOR", 2, coworker=True)
        self.shader = _Area("NODE_EDITOR", 3, tree_type="ShaderNodeTree")
        self.reports = []

    def ctx(self, area):
        return types.SimpleNamespace(area=area)

    def op(self, cls, **props):
        op = self.ns[cls]()
        for key, value in props.items():
            setattr(op, key, value)
        op.report = lambda _level, text: self.reports.append(text)
        return op


class TestNewChatScope(_Base):

    def test_all_from_a_sidebar_is_for_all_editors_and_shown_there(self):
        op = self.op("BFACW_OT_tab_new", scope="ALL")
        self.assertEqual(op.execute(self.ctx(self.view3d)), {"FINISHED"})
        new = self.reg.live()[-1]
        self.assertFalse(new.is_default)
        self.assertEqual(new.editor, "", "a chat for All editors, though made in a 3D Viewport")
        self.assertEqual(self.pins[1], new.id, "the area shows the new tab")

    def test_editor_scope_makes_the_editors(self):
        self.op("BFACW_OT_tab_new", scope="EDITOR").execute(self.ctx(self.view3d))
        new = self.reg.live()[-1]
        self.assertEqual(new.editor, "VIEW_3D")
        self.assertEqual(self.pins[1], new.id)

    def test_editor_scope_in_the_coworker_editor_is_all_editors(self):
        self.op("BFACW_OT_tab_new", scope="EDITOR").execute(self.ctx(self.coworker))
        self.assertEqual(self.reg.live()[-1].editor, "")

    def test_the_tooltip_follows_the_scope(self):
        cls = self.ns["BFACW_OT_tab_new"]
        tip = lambda area, scope: cls.description(self.ctx(area), types.SimpleNamespace(scope=scope))
        self.assertEqual(tip(self.view3d, "ALL"), "New chat in a new tab, for All editors")
        self.assertEqual(tip(self.view3d, "EDITOR"), "New chat in a new tab, using this 3D Viewport's context")
        self.assertEqual(tip(self.shader, "EDITOR"), "New chat in a new tab, using this Shader Editor's context")
        self.assertEqual(tip(self.coworker, "EDITOR"), "New chat in a new tab, for All editors")
        self.assertIn("All editors", cls.__doc__)


class TestMenuEntries(_Base):

    def draw(self, area):
        log = []
        self.ns["draw_new_chat_entries"](_Layout(log), self.ctx(area))
        return [e for e in log if e[0] == "op"]

    def test_in_an_editor_new_chat_then_for_this_editor_then_start_fresh(self):
        ops = self.draw(self.view3d)
        self.assertEqual([(e[1], e[2]) for e in ops],
                         [("bfacw.tab_new", "New Chat"), ("bfacw.tab_new", "New Chat for This 3D Viewport"),
                          ("bfacw.chat_clear", "Start This Chat Fresh")])
        self.assertEqual([e[4].scope for e in ops[:2]], ["ALL", "EDITOR"])
        self.assertEqual(ops[1][3], "VIEW3D", "the editor's icon")

    def test_a_node_editor_is_named_by_its_tree(self):
        self.assertEqual(self.draw(self.shader)[1][2], "New Chat for This Shader Editor")

    def test_no_opt_in_in_the_coworker_editor(self):
        self.assertEqual([e[2] for e in self.draw(self.coworker)], ["New Chat", "Start This Chat Fresh"])

    def test_start_fresh_names_the_shown_tab_and_greys_out_while_it_runs(self):
        tab = self.reg.create(editor="", title="Chat")
        self.pins[1] = tab.id
        fresh = self.draw(self.view3d)[-1]
        self.assertEqual(fresh[4].thread_id, tab.id)
        self.assertTrue(fresh[5].enabled)
        tab.state.turn_active = True
        self.assertFalse(self.draw(self.view3d)[-1][5].enabled)


class TestStartFresh(_Base):

    def test_another_tab_keeps_its_tab_with_a_new_chat(self):
        tab = self.reg.create(editor="VIEW_3D", title="3D")
        tab.chat_id = "old"
        tab.state.conversation_history = [{"role": "user", "content": "hi"}]
        self.pins[1] = tab.id
        op = self.op("BFACW_OT_chat_clear", thread_id="")
        self.assertEqual(op.execute(self.ctx(self.view3d)), {"FINISHED"})
        self.assertIn(tab, self.reg.live(), "the tab stays")
        self.assertEqual(self.saved, ["old"], "the old chat goes to History")
        self.assertNotEqual(tab.chat_id, "old")
        self.assertEqual(tab.state.conversation_history, [])
        self.assertEqual(tab.store.resets, 1)
        self.assertEqual(self.fresh_default, [], "All editors is left alone")

    def test_all_editors_starts_fresh(self):
        self.op("BFACW_OT_chat_clear", thread_id="").execute(self.ctx(self.view3d))
        self.assertEqual(self.fresh_default, [True])

    def test_acts_on_the_tab_it_was_drawn_for(self):
        tab = self.reg.create(editor="", title="Chat")
        tab.chat_id = "old"
        self.op("BFACW_OT_chat_clear", thread_id=tab.id).execute(self.ctx(None))   # no area in context
        self.assertEqual(self.saved, ["old"])
        self.assertEqual(self.fresh_default, [])

    def test_refused_while_its_turn_runs(self):
        tab = self.reg.create(editor="VIEW_3D", title="3D")
        tab.chat_id = "old"
        tab.state.turn_active = True
        op = self.op("BFACW_OT_chat_clear", thread_id=tab.id)
        self.assertEqual(op.execute(self.ctx(None)), {"CANCELLED"})
        self.assertEqual((tab.chat_id, self.saved), ("old", []))
        self.assertEqual(self.reports, ["Its turn is running: stop it first"])
        self.reg.default.state.turn_active = True
        self.assertEqual(self.op("BFACW_OT_chat_clear", thread_id="").execute(self.ctx(None)), {"CANCELLED"})
        self.assertEqual(self.fresh_default, [], "nor All editors while its turn runs")

    def test_a_closed_tab_does_nothing(self):
        self.assertEqual(self.op("BFACW_OT_chat_clear", thread_id="gone").execute(self.ctx(None)), {"CANCELLED"})

    def test_label(self):
        cls = self.ns["BFACW_OT_chat_clear"]
        self.assertEqual(cls.bl_label, "Start This Chat Fresh")
        self.assertEqual(cls.bl_description, "This chat goes to History and its tab starts empty")


class TestTitles(unittest.TestCase):

    def test_a_second_all_editors_tab_is_told_from_the_default(self):
        reg = threads.ThreadRegistry()
        second = reg.create(editor="", title=threads.short_name("") or "Chat")   # as _editor_thread("")
        self.assertEqual(threads.tab_title(reg.default), "All editors")
        self.assertEqual(threads.tab_title(second), "Chat")
        self.assertEqual(threads.tab_title(second, "light the campfire"), "light the campfire")


if __name__ == "__main__":
    unittest.main()
