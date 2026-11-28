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

if __name__ == "__main__":
    unittest.main()


# -- #670: a wand's mode is for its request only ---------------------------------------

def _ask_under_test(chat_send, ready=True):
    """``_ask`` from the source, with a fake bpy whose chat_send is *chat_send(props)*."""
    import types
    src = (_ADDON / "ui_context_menus.py").read_text(encoding="utf-8")
    start = src.index("def _ask(")
    end = src.index("\n\n\n", start)
    props = types.SimpleNamespace(chat_mode="AGENT")
    ops = types.SimpleNamespace(chat_send=lambda **kw: chat_send(props, kw))
    ns = {"bpy": types.SimpleNamespace(ops=types.SimpleNamespace(bfacw=ops)),
          "chat_ready": lambda: ready, "_editor_kind": lambda _c: "",
          "Any": object}
    exec(compile(src[start:end], "ui_context_menus.py", "exec"), ns)   # noqa: S102
    context = types.SimpleNamespace(window_manager=types.SimpleNamespace(bfacw_chat_props=props))
    return ns["_ask"], context, props


class TestAskKeepsTheChatsMode(unittest.TestCase):

    def test_the_request_runs_in_ask_and_the_chat_gets_its_mode_back(self):
        seen = []
        ask, ctx, props = _ask_under_test(lambda p, kw: seen.append(p.chat_mode))
        for before in ("AGENT", "PLAN", "AUTO"):
            props.chat_mode = before
            self.assertTrue(ask(ctx, "hi", "ASK"))
            self.assertEqual(props.chat_mode, before)
        self.assertEqual(seen, ["ASK"] * 3)

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


class TestEveryWandGoesThroughAsk(unittest.TestCase):
    """The three callers (Explain This, the editor spot, Translate Captions) all send via
    ``_ask`` and none sets the chat's mode itself, so the restore covers each."""

    def _body(self, marker):
        src = (_ADDON / "ui_context_menus.py").read_text(encoding="utf-8")
        start = src.index(marker)
        return src[start:src.index("\nclass ", start) if "\nclass " in src[start:] else len(src)]

    def test_each_caller_uses_ask_and_never_sets_the_mode(self):
        src = (_ADDON / "ui_context_menus.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("chat_mode ="), 2)    # only inside _ask (set + restore)
        for cls in ("class BFACW_OT_explain(", "class BFACW_OT_explain_spot(", "class BFACW_OT_translate("):
            self.assertIn('_ask(', self._body(cls), cls)
