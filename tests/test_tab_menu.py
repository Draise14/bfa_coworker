# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
A chat tab's right-click menu (#573): Rename, Close, Close Other Tabs and the rest,
for the tab under the mouse.

1. The button menu draws the tab's entries only for a ``bfacw.tab_show`` button (it reads
   ``context.button_operator``), for that button's thread; every other button's menu is
   left as it was.  The entries come before Coworker's others (prepended).
2. The entries: Rename, Close, Close Other Tabs, Start This Chat Fresh, Stop (only while
   its turn runs), Copy Whole Chat, New Chat (and for This <editor> where there is one),
   each naming the tab; what a running turn refuses is greyed out, not hidden.  The tabs
   drop-down has Rename and Close Other Tabs for the shown tab.
3. Rename: the tab shows the name at once, whether or not the chat is in History yet;
   the save keeps it (marked ``renamed``) and the automatic title (the first message)
   never replaces it; it comes back after a restart; empty = the automatic title again.
   History's own Rename updates an open tab too.
4. Close Other Tabs closes every other tab the close button would, and says how many
   stayed open because their turn runs.

Runs the code sliced from ui_chat.py / ui_context_menus.py with stand-ins (no Blender)::

    python -m unittest tests.test_tab_menu -v
"""

__all__ = ()

import importlib.util
import shutil
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_tabmenu_pkg"


def _module(name):
    spec = importlib.util.spec_from_file_location("{:s}.{:s}".format(_PKG, name), _ADDON / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod        # dataclasses look their module up
    spec.loader.exec_module(mod)
    return mod


_pkg = types.ModuleType(_PKG)
_pkg.__path__ = []
sys.modules[_PKG] = _pkg
threads = _module("threads")
chat_history = _module("chat_history")
log = _module("log")      # the real redaction: no stand-in


def _wand_spec():
    spec = importlib.util.spec_from_file_location(_PKG + ".wand.spec", _ADDON / "wand" / "spec.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _slice(src, name):
    """The top-level ``def`` / ``class`` *name* of *src*, up to the next top-level block."""
    for marker in ("\ndef {:s}(".format(name), "\nclass {:s}(".format(name)):
        start = src.find(marker)
        if start >= 0:
            break
    else:
        raise LookupError(name)
    end = src.find("\n\n\n", start + 1)
    return src[start + 1:end if end >= 0 else len(src)]


class _State:
    def __init__(self):
        self.conversation_history, self.turn_active = [], False


class _Store:
    def to_payload(self):
        return {}


class _Layout:
    """Records the operators drawn: (idname, text, icon, props, enabled)."""

    def __init__(self, log, enabled=True):
        self.log, self.enabled, self.operator_context, self.scale_y = log, enabled, "EXEC_REGION_WIN", 1.0
        self.activate_init = False

    def row(self, align=False):
        return _Layout(self.log)

    def separator(self):
        self.log.append(("separator",))

    def label(self, text="", icon="NONE"):
        self.log.append(("label", text))

    def prop(self, owner, name, text=""):
        self.log.append(("prop", name))

    def operator(self, idname, text="", icon="NONE"):
        props = types.SimpleNamespace(thread_id="", scope="ALL", what="MESSAGE")
        self.log.append(("op", idname, text, icon, props, self))
        return props


def _ops(log):
    return [e for e in log if e[0] == "op"]


class _UiBase(unittest.TestCase):
    """ui_chat's tab code over a real registry and a real chat index in a temp folder."""

    NAMES = ("_current_chat_of", "_tab_label", "_user_title", "_set_user_title", "_tab_full_title",
             "_save_thread_chat", "tab_busy", "draw_tab_entries", "_close_tab", "BFACW_OT_tab_close",
             "BFACW_OT_tab_close_others", "BFACW_OT_tab_rename", "BFACW_OT_chat_rename", "BFACW_MT_chat_tabs", "BFACW_OT_chat_stop")

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.base), True)
        self.new_ui()

    def new_ui(self):
        """A fresh add-on session (as after a restart): new registry, nothing cached."""
        src = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
        self.reg = reg = threads.ThreadRegistry(make_state=_State, make_store=_Store, default_objects=True)
        self.states, self.fresh_default, self.reports, self.helper_titles = {}, [], [], {}
        self.chat = {"id": "", "blend": ""}
        states = self.states
        index = chat_history.ChatIndex(self.base)
        index.ensure()
        self.index = index

        def thread_state(thread):
            return thread.state if thread.state is not None else states.setdefault(thread.id, _State())

        def current_chat_id():
            if not self.chat["id"]:
                self.chat["id"] = "default-chat"
            return self.chat["id"]

        self.ac = types.SimpleNamespace(THREADS=reg, thread_state=thread_state)
        self.stopped, self.status, self.helper_closed = [], [], []
        helper_chats = types.SimpleNamespace(tab_label=lambda t, named=True: self.helper_titles.get(t.id, ""),
                                             tab_closed=lambda t: self.helper_closed.append(t.id), stop_helpers=lambda t: self.stopped.append(t.id))
        approvals = types.SimpleNamespace(cancel_all=lambda _tag: None)
        for name, mod in (("coworker_api", types.SimpleNamespace(cancel_questions=lambda _tag: None)),
                          ("risky_steps", types.SimpleNamespace(APPROVALS=approvals)),
                          ("speech", types.SimpleNamespace(stop_chat_jobs=lambda _tag: None))):
            setattr(_pkg, name, mod)
            sys.modules[_PKG + "." + name] = mod
        session_memory = types.SimpleNamespace(store_lock=threading.Lock())
        for name, mod in (("threads", threads), ("chat_history", chat_history), ("helper_chats", helper_chats),
                          ("session_memory", session_memory)):
            setattr(_pkg, name, mod)
            sys.modules[_PKG + "." + name] = mod
        setattr(_pkg, "log", log)
        self.shown = reg.default
        bpy = types.SimpleNamespace(data=types.SimpleNamespace(filepath=""),
                                    types=types.SimpleNamespace(Menu=object, Context=object))
        prop = lambda **kw: kw.get("default", "")       # noqa: E731
        self.ns = {
            "__name__": _PKG + ".ui_chat", "__package__": _PKG, "bpy": bpy, "Operator": object,
            "StringProperty": prop, "EnumProperty": prop, "BoolProperty": prop,
            "agent_controller": self.ac, "_CHAT": self.chat, "_current_chat_id": current_chat_id,
            "_chat_index": lambda: index, "_USER_TITLES": {}, "_history_save_lock": threading.Lock(),
            "_log": log, "json": __import__("json"),
            "_blend_key": lambda: "", "_chat_engine_label": lambda: "Local", "_operating_mode": lambda: "LOCAL_LLM",
            "_save_chat_history": lambda: None, "_ui_thread": lambda _ctx=None: self.shown,
            "_redraw_all_chats": lambda: None, "_save_tabs": lambda: None,
            "_fresh_default_chat": lambda: self.fresh_default.append(True), "_auto_toggle": lambda *_a: None,
            "_AREA_PINS": {}, "_socket": lambda: types.SimpleNamespace(focus=""), "_focus_thread": lambda _t: None,
            "_acp": None, "safe_icon": lambda name: name, "_thread_status": lambda _t: ("Idle", "DOT"), "_thread_status_shown": lambda _t: ("Idle", "DOT"),
            "_magic": types.SimpleNamespace(shown_agent_text=lambda text, loose=False: text, redacted_agent_text=lambda text, loose=False: text),
            "_tab_icon": lambda _t: ("WORLD", False), "_running_now": lambda: False,
            "draw_new_chat_entries": lambda layout, _ctx: layout.log.append(("new_chat_entries",)),
            "new_chat_for_label": lambda _ctx: self.editor_label, "_tab_kind": lambda _ctx: "VIEW_3D",
            "_editor_icon": lambda kind: "VIEW3D", "_is_external_agent": lambda _ctx=None: False,
            "_thread_engine": lambda t: types.SimpleNamespace(cancel=lambda: self.cancelled.append(t.id)),
            "_redraw_areas": lambda _ctx: None,
        }
        self.cancelled = []
        self.editor_label = ""
        code = "\n\n\n".join(_slice(src, name) for name in self.NAMES)
        exec(compile(code, "ui_chat.py", "exec"), self.ns)  # noqa: S102

    def tab(self, first="", chat_id="", editor=""):
        thread = self.reg.create(editor=editor, title=threads.short_name(editor) or "Chat")
        thread.chat_id = chat_id or "chat-" + thread.id
        if first:
            self.ac.thread_state(thread).conversation_history.append({"role": "user", "content": first})
        return thread

    def say(self, thread, text):
        self.ac.thread_state(thread).conversation_history.append({"role": "user", "content": text})

    def label(self, thread):
        return self.ns["_tab_label"](thread)

    def op(self, cls, **props):
        op = self.ns[cls]()
        for key, value in props.items():
            setattr(op, key, value)
        op.report = lambda _level, text: self.reports.append(text)
        return op


class TestRename(_UiBase):

    def test_a_chat_not_in_history_yet_keeps_its_name_through_save_and_a_restart(self):
        tab = self.tab("make a campfire with stones around it")
        self.assertEqual(self.label(tab), "make a campfire w…")
        self.assertIsNone(self.index.get(tab.chat_id), "not saved yet")
        self.ns["_set_user_title"](tab, "  Campfire   scene ")
        self.assertEqual(self.label(tab), "Campfire scene", "the tab shows it at once")
        self.ns["_save_thread_chat"](tab)
        entry = self.index.get(tab.chat_id)
        self.assertEqual((entry["title"], entry["renamed"]), ("Campfire scene", True), "History has it")
        self.say(tab, "now add some logs")
        self.ns["_save_thread_chat"](tab)
        self.assertEqual(self.index.get(tab.chat_id)["title"], "Campfire scene", "the automatic title doesn't win")
        chat_id = tab.chat_id
        self.new_ui()                                   # a restart: nothing cached, the tab reopens
        again = self.tab(chat_id=chat_id)
        self.assertEqual(self.label(again), "Campfire scene")

    def test_a_chat_in_history_is_renamed_there_at_once_and_empty_gives_the_automatic_title_back(self):
        tab = self.tab("light the scene")
        self.ns["_save_thread_chat"](tab)
        self.assertEqual(self.index.get(tab.chat_id)["title"], "light the scene")
        self.ns["_set_user_title"](tab, "Lighting")
        self.assertEqual(self.index.get(tab.chat_id)["title"], "Lighting", "before any save")
        self.assertEqual(self.label(tab), "Lighting")
        self.ns["_set_user_title"](tab, "   ")
        entry = self.index.get(tab.chat_id)
        self.assertEqual((entry["title"], entry["renamed"]), ("light the scene", False))
        self.assertEqual(self.label(tab), "light the scene")
        self.say(tab, "warmer please")
        self.ns["_save_thread_chat"](tab)
        self.assertFalse(self.index.get(tab.chat_id)["renamed"])

    def test_all_editors_is_renamed_through_its_chat(self):
        default = self.reg.default
        self.assertEqual(self.label(default), "All editors")
        self.ns["_set_user_title"](default, "Main chat")
        self.assertEqual(self.chat["id"], "default-chat", "a chat id is made for it")
        self.assertEqual(self.label(default), "Main chat")
        self.chat["id"] = "another-chat"                # All editors goes on in another chat
        self.assertEqual(self.label(default), "All editors")

    def test_a_long_name_is_shortened_on_the_tab_only(self):
        tab = self.tab("hi")
        self.ns["_set_user_title"](tab, "A very long name for this chat tab")
        self.assertEqual(self.label(tab), "A very long name…")
        self.assertEqual(self.ns["_tab_full_title"](tab), "A very long name for this chat tab")

    def test_a_name_wins_over_a_helper_title(self):
        tab = self.tab("hi")
        self.helper_titles[tab.id] = "Helper: textures"
        self.assertEqual(self.label(tab), "Helper: textures")
        self.ns["_set_user_title"](tab, "Textures")
        self.assertEqual(self.label(tab), "Textures")

    def test_the_dialog_starts_from_the_full_title_and_unchanged_keeps_the_automatic_one(self):
        tab = self.tab("make a campfire with stones around it")
        shown = []
        ctx = types.SimpleNamespace(window_manager=types.SimpleNamespace(
            invoke_props_dialog=lambda op, **kw: shown.append(kw) or {"RUNNING_MODAL"}))
        op = self.op("BFACW_OT_tab_rename", thread_id=tab.id, title="", auto="")
        self.assertEqual(op.invoke(ctx, None), {"RUNNING_MODAL"})
        self.assertEqual(op.title, "make a campfire with stones around it")
        self.assertEqual(shown[0]["title"], "Rename Chat")
        self.assertEqual(op.execute(ctx), {"CANCELLED"}, "left as it was")
        self.assertEqual(self.ns["_user_title"](tab), "")
        op.title = "Campfire"
        self.assertEqual(op.execute(ctx), {"FINISHED"})
        self.assertEqual(self.label(tab), "Campfire")
        again = self.op("BFACW_OT_tab_rename", thread_id=tab.id, title="", auto="")
        again.invoke(ctx, None)
        self.assertEqual((again.title, again.auto), ("Campfire", ""), "a name of yours: any change counts")
        again.title = ""
        self.assertEqual(again.execute(ctx), {"FINISHED"})
        self.assertEqual(self.label(tab), "make a campfire w…", "empty: the automatic title again")

    def test_a_closed_tab_is_not_renamed(self):
        op = self.op("BFACW_OT_tab_rename", thread_id="gone", title="x", auto="")
        self.assertEqual(op.execute(None), {"CANCELLED"})

    def test_historys_rename_updates_the_open_tab(self):
        tab = self.tab("light the scene")
        self.ns["_save_thread_chat"](tab)
        self.assertEqual(self.label(tab), "light the scene")
        op = self.op("BFACW_OT_chat_rename", chat_id=tab.chat_id, title="Lighting", auto="")
        self.assertEqual(op.execute(None), {"FINISHED"})
        self.assertEqual(self.label(tab), "Lighting")
        op = self.op("BFACW_OT_chat_rename", chat_id=tab.chat_id, title="", auto="")
        self.assertEqual(op.execute(None), {"FINISHED"}, "empty: the automatic title")
        self.assertEqual(self.label(tab), "light the scene")


class TestCloseOthers(_UiBase):

    def test_closes_the_others_but_a_running_turn(self):
        keep, a, busy = self.tab("one"), self.tab("two"), self.tab("three")
        self.ac.thread_state(busy).turn_active = True
        op = self.op("BFACW_OT_tab_close_others", thread_id=keep.id)
        self.assertEqual(op.execute(None), {"FINISHED"})
        self.assertEqual(self.reg.live(), [self.reg.default, keep, busy])
        self.assertEqual(self.fresh_default, [True], "All editors starts fresh, as its X does")
        self.assertEqual(self.reports, ["Closed 2 tabs; 1 tab stayed open: its turn is running"])
        self.assertIsNotNone(self.index.get(a.chat_id), "the closed chat is in History")

    def test_a_helper_chat_closes_like_any_other_tab(self):
        keep, helper, busy = self.tab("one"), self.tab("do a part"), self.tab("three")
        self.helper_titles[helper.id] = "Helper: part"
        self.ac.thread_state(busy).turn_active = True
        op = self.op("BFACW_OT_tab_close_others", thread_id=keep.id)
        self.assertEqual(op.execute(None), {"FINISHED"})
        self.assertEqual(self.reg.live(), [self.reg.default, keep, busy])
        self.assertIn(helper.id, self.helper_closed, "it is told, so a waiting helper counts as stopped")
        self.assertEqual(self.reports, ["Closed 2 tabs; 1 tab stayed open: its turn is running"])

    def test_nothing_to_close(self):
        keep = self.tab("one")
        self.ac.thread_state(self.reg.default).turn_active = True
        op = self.op("BFACW_OT_tab_close_others", thread_id=keep.id)
        self.assertEqual(op.execute(None), {"CANCELLED"})
        self.assertEqual(self.reports, ["No tab closed; 1 tab stayed open: its turn is running"])

    def test_close_still_refuses_a_running_turn(self):
        tab = self.tab("one")
        self.ac.thread_state(tab).turn_active = True
        self.assertEqual(self.op("BFACW_OT_tab_close", thread_id=tab.id).execute(None), {"CANCELLED"})
        self.assertEqual(self.reports, ["Its turn is running: stop it first"])
        self.ac.thread_state(tab).turn_active = False
        self.assertEqual(self.op("BFACW_OT_tab_close", thread_id=tab.id).execute(None), {"FINISHED"})
        self.assertNotIn(tab, self.reg.live())

    def test_the_pure_helpers(self):
        reg = threads.ThreadRegistry()
        keep, a, b = reg.create(), reg.create(), reg.create()
        close, stay = threads.others_to_close(reg, keep, lambda t: t is b)
        self.assertEqual((close, stay), ([reg.default, a], [b]))
        self.assertEqual(threads.close_others_report(1, 0), "Closed 1 tab")
        self.assertEqual(threads.close_others_report(0, 2), "No tab closed; 2 tabs stayed open: their turn is running")


class TestEntries(_UiBase):

    def draw(self, thread):
        log = []
        layout = _Layout(log)
        self.ns["draw_tab_entries"](layout, None, thread)
        return layout, log

    def test_every_entry_names_the_tab(self):
        tab, other = self.tab("one"), self.tab("two")
        layout, log = self.draw(other)
        ops = _ops(log)
        self.assertEqual([(e[1], e[2]) for e in ops], [
            ("bfacw.tab_rename", "Rename..."), ("bfacw.tab_close", "Close"),
            ("bfacw.tab_close_others", "Close Other Tabs"), ("bfacw.chat_clear", "Start This Chat Fresh"),
            ("bfacw.chat_context_copy", "Copy Whole Chat"), ("bfacw.tab_new", "New Chat")])
        self.assertTrue(all(e[4].thread_id == other.id for e in ops[:5]), "the tab right-clicked, not the shown one")
        self.assertEqual(ops[4][4].what, "CHAT")
        self.assertTrue(all(e[5].enabled for e in ops))
        self.assertEqual(layout.operator_context, "INVOKE_DEFAULT", "Rename opens its dialog")
        del tab

    def test_a_running_turn_greys_out_close_and_fresh_and_offers_stop(self):
        tab = self.tab("one")
        self.ac.thread_state(tab).turn_active = True
        ops = {e[1]: e for e in _ops(self.draw(tab)[1])}
        self.assertFalse(ops["bfacw.tab_close"][5].enabled)
        self.assertFalse(ops["bfacw.chat_clear"][5].enabled)
        self.assertTrue(ops["bfacw.tab_close_others"][5].enabled, "All editors can still close")
        self.assertEqual(ops["bfacw.chat_stop"][4].thread_id, tab.id)

    def test_close_others_greyed_out_when_no_other_tab_can_close(self):
        ops = {e[1]: e for e in _ops(self.draw(self.reg.default)[1])}
        self.assertFalse(ops["bfacw.tab_close_others"][5].enabled, "All editors alone")
        self.assertNotIn("bfacw.chat_stop", ops)

    def test_new_chat_for_this_editor_where_there_is_one(self):
        self.editor_label = "New Chat for This 3D Viewport"
        ops = _ops(self.draw(self.reg.default)[1])
        self.assertEqual((ops[-1][2], ops[-1][4].scope), ("New Chat for This 3D Viewport", "EDITOR"))

    def test_the_tabs_drop_down_has_rename_and_close_others_for_the_shown_tab(self):
        tab = self.tab("one")
        self.shown = tab
        menu = self.ns["BFACW_MT_chat_tabs"]()
        menu.layout = _Layout([])
        menu.draw(None)
        ops = {e[1]: e for e in _ops(menu.layout.log) if e[1] != "bfacw.tab_show"}
        self.assertEqual(ops["bfacw.tab_rename"][4].thread_id, tab.id)
        self.assertEqual(ops["bfacw.tab_close_others"][4].thread_id, tab.id)


def _context_menus(ui_chat, agent_controller):
    """ui_context_menus.py, loaded with a stand-in bpy."""
    hooked = []

    class _Menu:
        @staticmethod
        def prepend(fn):
            hooked.append(("prepend", fn.__name__))

        @staticmethod
        def append(fn):
            hooked.append(("append", fn.__name__))

        @staticmethod
        def remove(fn):
            hooked.append(("remove", fn.__name__))

    bpy = types.ModuleType("bpy")
    bpy.props = types.SimpleNamespace(BoolProperty=lambda **kw: None, StringProperty=lambda **kw: None)
    bpy.types = types.SimpleNamespace(Operator=object, UI_MT_button_context_menu=_Menu)
    bpy.utils = types.SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    saved = {k: sys.modules.get(k) for k in ("bpy", "bpy.props", "bpy.types")}
    sys.modules.update({"bpy": bpy, "bpy.props": bpy.props, "bpy.types": bpy.types})
    try:
        _pkg.wand = types.ModuleType(_PKG + ".wand")        # the tooltip helper of the wands (#607)
        _pkg.wand.__path__ = []
        sys.modules[_PKG + ".wand"] = _pkg.wand
        sys.modules[_PKG + ".wand.spec"] = _pkg.wand.spec = _wand_spec()
        _pkg.ui_chat, _pkg.agent_controller = ui_chat, agent_controller
        sys.modules[_PKG + ".ui_chat"], sys.modules[_PKG + ".agent_controller"] = ui_chat, agent_controller
        mod = _module("ui_context_menus")
    finally:
        for name in (_PKG + ".wand", _PKG + ".wand.spec"):      # the stand-in goes again (#607)
            sys.modules.pop(name, None)
        if hasattr(_pkg, "wand"):
            del _pkg.wand
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value
    return mod, hooked


class TestButtonMenu(unittest.TestCase):

    def setUp(self):
        self.reg = threads.ThreadRegistry()
        self.tab = self.reg.create()
        self.drawn = []
        ui_chat = types.SimpleNamespace(draw_tab_entries=lambda layout, ctx, thread: self.drawn.append(thread))
        self.menus, self.hooked = _context_menus(ui_chat, types.SimpleNamespace(THREADS=self.reg))

    @staticmethod
    def button(identifier, **props):
        return types.SimpleNamespace(bl_rna=types.SimpleNamespace(identifier=identifier), **props)

    def draw(self, **ctx):
        menu = types.SimpleNamespace(layout=_Layout([]))
        self.menus._draw_tab_menu(menu, types.SimpleNamespace(**ctx))
        return menu.layout.log

    def test_a_tab_button_gets_its_threads_entries(self):
        log = self.draw(button_operator=self.button("BFACW_OT_tab_show", thread_id=self.tab.id))
        self.assertEqual(self.drawn, [self.tab])
        self.assertEqual(log, [("separator",)], "set off from Blender's entries")

    def test_other_buttons_are_left_as_they_were(self):
        for ctx in ({"button_operator": self.button("BFACW_OT_tab_new", thread_id=self.tab.id)},
                    {"button_operator": self.button("MESH_OT_primitive_cube_add")},
                    {"button_operator": None, "button_prop": object()},
                    {}):
            self.assertEqual(self.draw(**ctx), [], ctx)
        self.assertEqual(self.drawn, [])

    def test_a_tab_closed_meanwhile_draws_nothing(self):
        self.assertEqual(self.draw(button_operator=self.button("BFACW_OT_tab_show", thread_id="gone")), [])
        self.assertEqual(self.menus.tab_under_mouse(types.SimpleNamespace(
            button_operator=self.button("BFACW_OT_tab_show", thread_id="gone"))), "gone")

    def test_the_tab_entries_come_first(self):
        self.menus.register()
        self.assertEqual(self.hooked, [("prepend", "_draw_tab_menu"), ("append", "_draw_button_menu")])
        self.menus.unregister()
        self.assertEqual(self.hooked[2:], [("remove", "_draw_tab_menu"), ("remove", "_draw_button_menu")])


class TestIndexMark(unittest.TestCase):

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.base), True)
        self.index = chat_history.ChatIndex(self.base)
        self.index.ensure()

    def test_a_name_given_before_the_first_save_is_kept_and_marked(self):
        history = [{"role": "user", "content": "make a campfire"}]
        self.assertFalse(self.index.rename("new", "Campfire"), "not in History yet")
        self.index.touch("new", history, user_title="Campfire")
        entry = chat_history.ChatIndex(self.base).get("new")          # read again, as after a restart
        self.assertEqual((entry["title"], entry["renamed"]), ("Campfire", True))
        self.index.touch("new", history + [{"role": "user", "content": "more"}])
        self.assertEqual(self.index.get("new")["title"], "Campfire")

    def test_an_empty_chat_is_still_not_listed(self):
        self.index.touch("empty", [], user_title="Name")
        self.assertIsNone(self.index.get("empty"))

    def test_clean_name(self):
        self.assertEqual(chat_history.clean_name("  a \n b  "), "a b")
        self.assertEqual(len(chat_history.clean_name("x" * 500)), 120)
        self.assertEqual(chat_history.clean_name(None), "")


class TestTabTitle(unittest.TestCase):

    def test_a_name_wins(self):
        reg = threads.ThreadRegistry()
        tab = reg.create(title="Chat")
        self.assertEqual(threads.tab_title(tab, "first message", user_title="Mine"), "Mine")
        self.assertEqual(threads.tab_title(reg.default, user_title="Main"), "Main")
        self.assertEqual(threads.tab_title(reg.default), "All editors")
        self.assertEqual(threads.tab_title(tab, "first message"), "first message")


if __name__ == "__main__":
    unittest.main()


class TestNoKeyInAName(_UiBase):
    """A chat's name goes to chats/index.json: never with a key in it (the first message may hold one)."""

    KEY = "sk-" + "abcdefghijklmnopqrstuvwx"       # made up

    def _index_text(self):
        return (self.base / "chats" / "index.json").read_text(encoding="utf-8")

    def test_the_dialog_is_filled_from_the_redacted_title(self):
        tab = self.tab("use my key {:s} for this".format(self.KEY))
        self.assertNotIn(self.KEY, self.ns["_tab_full_title"](tab))

    def test_renaming_never_writes_the_key_to_the_index(self):
        tab = self.tab("use my key {:s} for this".format(self.KEY))
        ctx = types.SimpleNamespace(window_manager=types.SimpleNamespace(invoke_props_dialog=lambda *a, **k: {"RUNNING_MODAL"}))
        op = self.op("BFACW_OT_tab_rename", thread_id=tab.id, title="", auto="")
        op.invoke(ctx, None)
        op.title = op.title + "x"
        op.execute(ctx)
        self.ns["_save_thread_chat"](tab)
        self.ns["_set_user_title"](tab, "my " + self.KEY)
        self.say(tab, "more")
        self.ns["_save_thread_chat"](tab)
        self.assertNotIn(self.KEY, self._index_text())
        self.assertNotIn(self.KEY, self.label(tab))

    def test_historys_rename_and_the_save_path_never_write_the_key(self):
        tab = self.tab("hello")
        self.ns["_save_thread_chat"](tab)
        self.op("BFACW_OT_chat_rename", chat_id=tab.chat_id, title="name " + self.KEY, auto="").execute(None)
        self.assertNotIn(self.KEY, self._index_text())
        self.ns["_USER_TITLES"][tab.chat_id] = "again " + self.KEY      # a name already in memory
        self.say(tab, "more")
        self.ns["_save_thread_chat"](tab)
        self.assertNotIn(self.KEY, self._index_text())


class TestHistoryRenameUnchanged(_UiBase):

    def test_confirming_the_same_title_does_not_mark_it_renamed(self):
        tab = self.tab("light the scene")
        self.ns["_save_thread_chat"](tab)
        ctx = types.SimpleNamespace(window_manager=types.SimpleNamespace(invoke_props_dialog=lambda *a, **k: {"RUNNING_MODAL"}))
        op = self.op("BFACW_OT_chat_rename", chat_id=tab.chat_id, title="", auto="")
        op.invoke(ctx, None)
        self.assertEqual(op.title, "light the scene")
        self.assertEqual(op.execute(ctx), {"CANCELLED"})
        self.assertFalse(self.index.get(tab.chat_id)["renamed"])
        op.title = "Lighting"
        self.assertEqual(op.execute(ctx), {"FINISHED"})
        again = self.op("BFACW_OT_chat_rename", chat_id=tab.chat_id, title="", auto="")
        again.invoke(ctx, None)
        self.assertEqual(again.auto, "", "a name of yours: any confirm counts")

    def test_the_dialog_says_what_empty_does(self):
        log_ = []
        op = self.op("BFACW_OT_chat_rename", chat_id="x", title="", auto="")
        op.layout = _Layout(log_)
        op.draw(None)
        self.assertTrue(any(e[0] == "label" and "Empty" in e[1] for e in log_))


class TestStopTab(_UiBase):

    def _stop(self, thread_id=""):
        props = {"thread_id": thread_id}
        wm = types.SimpleNamespace(bfacw_chat_props=types.SimpleNamespace(chat_status="Ready"))
        op = self.op("BFACW_OT_chat_stop", **props)
        result = op.execute(types.SimpleNamespace(window_manager=wm, area=None, preferences=None))
        return result, wm.bfacw_chat_props.chat_status

    def test_a_valid_thread_id_stops_that_tab_and_the_shown_status_stays(self):
        shown, other = self.tab("one"), self.tab("two")
        self.shown = shown
        result, status = self._stop(thread_id=other.id)
        self.assertEqual((result, self.cancelled), ({"FINISHED"}, [other.id]))
        self.assertEqual(self.ac.thread_state(other).status_text, "Stopped")
        self.assertEqual(status, "Ready", "the shown chat's status line is not about the stopped tab")

    def test_the_shown_tab_updates_the_status_line(self):
        shown = self.tab("one")
        self.shown = shown
        self.assertEqual(self._stop(thread_id=shown.id), ({"FINISHED"}, "Stopped"))

    def test_no_thread_id_is_the_shown_chat_as_before(self):
        self.shown = self.tab("one")
        result, status = self._stop()
        self.assertEqual((result, status, self.cancelled), ({"FINISHED"}, "Stopped", [self.shown.id]))

    def test_a_wrong_or_closed_thread_id_is_cancelled(self):
        self.shown = self.tab("one")
        self.assertEqual(self._stop(thread_id="nope"), ({"CANCELLED"}, "Ready"))
        closed = self.tab("two")
        self.reg.remove(closed.id)
        self.assertEqual(self._stop(thread_id=closed.id), ({"CANCELLED"}, "Ready"))
        self.assertEqual(self.cancelled, [])


class TestContextCopy(unittest.TestCase):
    """Copy Whole Chat from a tab's menu (chat_editor.BFACW_OT_chat_context_copy)."""

    KEY = "sk-" + "abcdefghijklmnopqrstuvwx"

    def setUp(self):
        src = (_ADDON / "chat_editor.py").read_text(encoding="utf-8")
        self.reg = threads.ThreadRegistry(make_state=_State, make_store=_Store, default_objects=True)
        states = {}
        ac = types.SimpleNamespace(
            THREADS=self.reg, thread_state=lambda t: states.setdefault(t.id, _State()),
            _memory=lambda _t: types.SimpleNamespace(store=types.SimpleNamespace(retired_history=[])))
        self.states = states
        view = types.ModuleType(_PKG + ".ui_gpu.chat_view")
        view.transcript = lambda h: "\n".join(str(m.get("content", "")) for m in h)
        sys.modules[_PKG + ".ui_gpu"] = types.ModuleType(_PKG + ".ui_gpu")
        sys.modules[_PKG + ".ui_gpu.chat_view"] = view
        setattr(_pkg, "log", log)
        prop = lambda **kw: kw.get("default", "")       # noqa: E731
        bpy = types.SimpleNamespace(types=types.SimpleNamespace(Operator=object),
                                    props=types.SimpleNamespace(EnumProperty=prop, StringProperty=prop))
        self.ns = {"__name__": _PKG + ".chat_editor", "__package__": _PKG, "bpy": bpy, "agent_controller": ac,
                   "_COPY_WHAT": [("CHAT", "Whole Chat", "d"), ("MESSAGE", "Message", "d")], "Any": object,
                   "input_capture": types.SimpleNamespace(menu_target=lambda: {"turn": None, "surface": None})}
        code = "\n\n\n".join(_slice(src, n) for n in ("_history", "BFACW_OT_chat_context_copy"))
        exec(compile("from __future__ import annotations\n" + code, "chat_editor.py", "exec"), self.ns)  # noqa: S102

    def _copy(self, thread_id):
        wm = types.SimpleNamespace(clipboard="")
        op = self.ns["BFACW_OT_chat_context_copy"]()
        op.what, op.thread_id = "CHAT", thread_id
        return op.execute(types.SimpleNamespace(window_manager=wm, area=None)), wm.clipboard

    def test_the_right_tab_a_wrong_id_and_redaction(self):
        a, b = self.reg.create(editor="", title="a"), self.reg.create(editor="", title="b")
        self.states.setdefault(a.id, _State()).conversation_history.append(
            {"role": "user", "content": "tab a key {:s}".format(self.KEY)})
        self.states.setdefault(b.id, _State()).conversation_history.append({"role": "user", "content": "tab b"})
        result, text = self._copy(a.id)
        self.assertEqual(result, {"FINISHED"})
        self.assertIn("tab a", text)
        self.assertNotIn("tab b", text)
        self.assertNotIn(self.KEY, text)
        self.assertEqual(self._copy("gone"), ({"CANCELLED"}, ""))
