# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Coworker in the right-click menus (#290 Explain This): the question
each entry puts in the chat.

Run with::

    python -m unittest tests.test_context_menus -v
"""

__all__ = ()

import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _explain_text():
    src = (_ADDON / "ui_context_menus.py").read_text(encoding="utf-8")
    start = src.index("def explain_text(")
    end = src.index("\n\n\n", start)
    ns: dict = {}
    exec(compile("from typing import Any\n" + src[start:end], "ui_context_menus.py", "exec"), ns)   # noqa: S102
    return ns["explain_text"]


class TestExplainText(unittest.TestCase):

    def test_a_property_says_what_where_and_its_value(self):
        text = _explain_text()({"kind": "property", "label": "Resolution X", "editor": "Properties",
                                "path": "scenes['Scene'].render.resolution_x", "type": "int", "value": "1920",
                                "description": "Number of horizontal pixels in the rendered image"})
        self.assertIn("“Resolution X” in Properties", text)
        self.assertIn("scenes['Scene'].render.resolution_x, int, now 1920", text)
        self.assertIn("Number of horizontal pixels", text)
        self.assertTrue(text.endswith("Keep it short and practical."))

    def test_an_operator_and_an_editor(self):
        f = _explain_text()
        op = f({"kind": "operator", "label": "Add Cube", "idname": "mesh.primitive_cube_add", "editor": "3D Viewport",
                "description": "Construct a cube mesh"})
        self.assertIn("the operator mesh.primitive_cube_add", op)
        editor = f({"kind": "editor", "editor": "Shader Editor", "state": " (node tree: Material)"})
        self.assertIn("Explain this editor: the Shader Editor (node tree: Material).", editor)



def _spot_text():
    src = (_ADDON / "ui_context_menus.py").read_text(encoding="utf-8")
    start = src.index("def spot_text(")
    end = src.index("\n\n\n", start)
    ns: dict = {}
    exec(compile(src[start:end], "ui_context_menus.py", "exec"), ns)      # noqa: S102
    return ns["spot_text"]


class TestSpotText(unittest.TestCase):

    def test_a_button_python_cannot_name_is_shown_in_a_picture(self):
        text = _spot_text()("3D Viewport", "HEADER")
        self.assertIn("in the 3D Viewport's header", text)
        self.assertIn("inside the red ring", text)
        self.assertIn("the button I right-clicked:", _spot_text()("", ""))


def _translate_text():
    src = (_ADDON / "ui_context_menus.py").read_text(encoding="utf-8")
    start = src.index("def translate_text(")
    end = src.index("\n\n\n", start)
    ns: dict = {}
    exec(compile(src[start:end], "ui_context_menus.py", "exec"), ns)      # noqa: S102
    return ns["translate_text"]


def _helper(name):
    src = (_ADDON / "ui_context_menus.py").read_text(encoding="utf-8")
    start = src.index("def {:s}(".format(name))
    end = src.index("\n\n\n", start)
    ns: dict = {}
    exec(compile(src[start:end], "ui_context_menus.py", "exec"), ns)      # noqa: S102
    return ns[name]


class TestTranslateText(unittest.TestCase):

    def test_asks_for_only_the_translation(self):
        text = _translate_text()("bpy.data.curves['Text'].body", "Hello world", "Spanish",
                                 "the text object “Text”")
        self.assertIn("into Spanish", text)
        self.assertIn("Hello world", text)
        self.assertIn("Reply with only the translated text", text)
        self.assertIn("Keep line breaks", text)
        self.assertNotIn("execute_blender_code", text)      # Coworker writes it in, not the agent

    def test_clean_reply(self):
        clean = _helper("clean_reply")
        self.assertEqual(clean("  “Hola mundo”  "), "Hola mundo")
        self.assertEqual(clean('"Hola"'), "Hola")
        self.assertEqual(clean("```\nline one\nline two\n```"), "line one\nline two")
        self.assertEqual(clean("Hola, “amigo”"), "Hola, “amigo”")      # inner quotes stay

    def test_splice_a_selection(self):
        splice = _helper("splice")
        self.assertEqual(splice("one two\nthree four", (0, 4, 1, 5), "X"), "one X four")
        self.assertEqual(splice("abc", (0, 0, 0, 3), "xyz"), "xyz")

    def test_from_a_language_or_detected(self):
        f = _translate_text()
        text = f("bpy.data.curves['Text'].body", "Bonjour", "English", "the text object “Text”", "French")
        self.assertIn("the text object “Text” from French into English", text)
        self.assertNotIn(" from ", f("bpy.data.curves['Text'].body", "Bonjour", "English", "the text"))


# -- #670: a wand's mode is for its request only ---------------------------------------

def _ask_under_test(chat_send, ready=True, state=None):
    """``_ask`` from the source, with a fake bpy whose chat_send is *chat_send(props, kw)*."""
    import types
    src = (_ADDON / "ui_context_menus.py").read_text(encoding="utf-8")
    start = src.index("def _ask(")
    end = src.index("\n\n\n", start)
    props = types.SimpleNamespace(chat_mode="AGENT")
    ops = types.SimpleNamespace(chat_send=lambda **kw: chat_send(props, kw))
    state = {"normal": "", "syncing": ""} if state is None else state
    ns = {"bpy": types.SimpleNamespace(ops=types.SimpleNamespace(bfacw=ops)),
          "chat_ready": lambda: ready, "_editor_kind": lambda _c: "",
          "_agent_mode_state": lambda: state, "Any": object}
    exec(compile(src[start:end], "ui_context_menus.py", "exec"), ns)   # noqa: S102
    context = types.SimpleNamespace(window_manager=types.SimpleNamespace(bfacw_chat_props=props))
    return ns["_ask"], context, props


class TestAskKeepsTheChatsMode(unittest.TestCase):

    def test_the_request_runs_in_ask_and_the_chat_gets_its_mode_back(self):
        seen = []
        ask, ctx, props = _ask_under_test(lambda p, kw: seen.append(p.chat_mode))
        for before in ("AGENT", "PLAN"):
            props.chat_mode = before
            self.assertTrue(ask(ctx, "hi", "ASK"))
            self.assertEqual(props.chat_mode, before)
        self.assertEqual(seen, ["ASK"] * 2)

    def test_a_chat_already_on_ask_stays_on_ask(self):
        seen = []
        ask, ctx, props = _ask_under_test(lambda p, kw: seen.append(p.chat_mode))
        props.chat_mode = "ASK"
        self.assertTrue(ask(ctx, "hi", "ASK"))
        self.assertEqual((props.chat_mode, seen), ("ASK", ["ASK"]))

    def test_a_thread_send_restores_too(self):
        ask, ctx, props = _ask_under_test(lambda p, kw: None)
        props.chat_mode = "PLAN"
        self.assertTrue(ask(ctx, "hi", "ASK", thread_id="t1"))
        self.assertEqual(props.chat_mode, "PLAN")

    def test_a_failing_send_restores_it(self):
        def boom(p, kw):
            raise RuntimeError("Operator failed")
        ask, ctx, props = _ask_under_test(boom)
        props.chat_mode = "AGENT"
        with self.assertRaises(RuntimeError):
            ask(ctx, "hi", "ASK")
        self.assertEqual(props.chat_mode, "AGENT")

    def test_a_chat_that_is_not_ready_is_left_alone(self):
        ask, ctx, props = _ask_under_test(lambda p, kw: self.fail("sent"), ready=False)
        props.chat_mode = "PLAN"
        self.assertFalse(ask(ctx, "hi", "ASK"))
        self.assertEqual(props.chat_mode, "PLAN")

    def test_the_restore_does_not_sync_the_agents_mode(self):
        """External Agent: the mode box's update would flip the agent mid-turn (#670)."""
        state = {"normal": "", "syncing": ""}
        log = []
        ask, ctx, _props = _ask_under_test(
            lambda p, kw: log.append(("send", ctx.window_manager.bfacw_chat_props.chat_mode, state["syncing"])),
            state=state)

        class Props:
            def __init__(self):
                self._m = "PLAN"

            @property
            def chat_mode(self):
                return self._m

            @chat_mode.setter
            def chat_mode(self, v):      # what _on_chat_mode does
                log.append(("set", v, state["syncing"]))
                self._m = v
        ctx.window_manager.bfacw_chat_props = Props()
        self.assertTrue(ask(ctx, "hi", "ASK"))
        self.assertEqual(log, [("set", "ASK", ""), ("send", "ASK", ""), ("set", "PLAN", "1")])
        self.assertEqual(state["syncing"], "")      # and it is cleared again


# The three callers, run for real with fake Blender modules: each sends in Ask and the
# chat ends on the user's mode.

def _load_menus(chat_mode, sent):
    """Import ui_context_menus under a fake package; chat_send records the mode in *sent*."""
    import importlib.util
    import sys
    import types
    from contextlib import contextmanager

    props = types.SimpleNamespace(chat_mode=chat_mode)
    pkg = "_fakecw"
    mods = {}

    def mod(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        mods[name] = m
        return m

    class _Timers:
        def register(self, fn, first_interval=0.0):
            sent.setdefault("timers", []).append(fn)

        def is_registered(self, _fn):
            return True

    @contextmanager
    def override(**_kw):
        yield

    wm = types.SimpleNamespace(bfacw_chat_props=props, windows=[])
    prefs = types.SimpleNamespace(
        operating_mode="COWORKER", translate_language="es", translate_from="auto",
        bl_rna=types.SimpleNamespace(properties={
            "translate_language": types.SimpleNamespace(enum_items={"es": types.SimpleNamespace(name="Spanish")})}))
    ctx = types.SimpleNamespace(
        window_manager=wm, area=None, window=None, temp_override=override,
        preferences=types.SimpleNamespace(addons={pkg: types.SimpleNamespace(preferences=prefs)}))

    def chat_send(**kw):
        sent.setdefault("modes", []).append(props.chat_mode)
    ops = types.SimpleNamespace(bfacw=types.SimpleNamespace(chat_send=chat_send))
    bpy = mod("bpy", ops=ops, context=ctx, data=types.SimpleNamespace(),
              app=types.SimpleNamespace(timers=_Timers()), types=None)
    mod("bpy.props", BoolProperty=lambda **k: None, StringProperty=lambda **k: None)
    mod("bpy.types", Operator=object, Context=object)
    bpy.types = sys.modules.get("bpy.types") or mods["bpy.types"]
    mod(pkg, __path__=[])
    mod(pkg + ".wand", __path__=[])
    mod(pkg + ".wand.spec", needs_agent_line=lambda doc, _t: doc)
    mod(pkg + ".ui_chat", SIDEBAR_TYPES=(), _EDITOR_NAMES={}, _AGENT_MODE={"normal": "", "syncing": ""},
        _editor_thread=None, _acp=None)
    mod(pkg + ".agent_controller", THREADS=None, _agent_state=types.SimpleNamespace(mcp_server_running=True))
    mod(pkg + ".threads", pick=lambda *a, **k: types.SimpleNamespace(id="t1"))
    mod(pkg + ".chat_attachments", capture_screen_rect=lambda *a, **k: "")
    for name, m in mods.items():
        sys.modules[name] = m
    spec = importlib.util.spec_from_file_location(pkg + ".ui_context_menus", _ADDON / "ui_context_menus.py")
    menus = importlib.util.module_from_spec(spec)
    sys.modules[pkg + ".ui_context_menus"] = menus
    mods[pkg + ".ui_context_menus"] = menus
    spec.loader.exec_module(menus)
    return menus, ctx, props, mods


class TestEveryWandGoesThroughAsk(unittest.TestCase):
    """Explain This, the editor spot and Translate Captions: Ask for the request, the user's mode after."""

    def setUp(self):
        import sys
        self._saved = {k: v for k, v in sys.modules.items() if k == "bpy" or k.startswith(("bpy.", "_fakecw"))}
        for k in list(self._saved):
            del sys.modules[k]

    def tearDown(self):
        import sys
        for k in [k for k in sys.modules if k == "bpy" or k.startswith(("bpy.", "_fakecw"))]:
            del sys.modules[k]
        sys.modules.update(self._saved)

    def _check(self, run, before):
        import types
        sent: dict = {}
        menus, ctx, props, _mods = _load_menus(before, sent)
        run(menus, ctx, types)
        self.assertEqual(sent.get("modes"), ["ASK"])
        self.assertEqual(props.chat_mode, before)

    def test_explain_this(self):
        for before in ("AGENT", "PLAN", "ASK"):
            with self.subTest(before=before):
                self.tearDown()
                self._check(lambda m, c, t: self.assertEqual(
                    m.BFACW_OT_explain.execute(t.SimpleNamespace(text="why?", report=lambda *a: None), c),
                    {'FINISHED'}), before)

    def test_explain_spot(self):
        for before in ("AGENT", "PLAN", "ASK"):
            with self.subTest(before=before):
                self.tearDown()

                def run(m, c, _t):
                    c.window_manager.windows = [c.window]
                    c.window = _Window()
                    c.window_manager.windows = [c.window]
                    m._explain_spot(0, 5, 5)
                self._check(run, before)

    def test_translate(self):
        for before in ("AGENT", "PLAN", "ASK"):
            with self.subTest(before=before):
                self.tearDown()

                def run(m, c, t):
                    m.agent_controller_history = lambda _e="": []
                    me = t.SimpleNamespace(path="p", current="Hello", what="text", shown=True, span="",
                                           report=lambda *a: None)
                    self.assertEqual(m.BFACW_OT_translate.execute(me, c), {'FINISHED'})
                self._check(run, before)


class _Window:
    width = height = 100
    screen = type("S", (), {"areas": []})()


if __name__ == "__main__":
    unittest.main()
