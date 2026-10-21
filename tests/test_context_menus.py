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



def _translate_text():
    src = (_ADDON / "ui_context_menus.py").read_text(encoding="utf-8")
    start = src.index("def translate_text(")
    end = src.index("\n\n\n", start)
    ns: dict = {}
    exec(compile(src[start:end], "ui_context_menus.py", "exec"), ns)      # noqa: S102
    return ns["translate_text"]


class TestTranslateText(unittest.TestCase):

    def test_a_property_gets_its_exact_path_and_text(self):
        text = _translate_text()("bpy.data.curves['Text'].body", "Hello world", "Spanish",
                                 "the text object “Text”")
        self.assertIn("into Spanish", text)
        self.assertIn("bpy.data.curves['Text'].body = \"...\"", text)
        self.assertIn("“Hello world”", text)
        self.assertIn("keep line breaks", text)

    def test_a_text_datablock_is_written_back_whole(self):
        text = _translate_text()("bpy.data.texts['Notes'].as_string()", "Bonjour", "English", "the selected text")
        self.assertIn("bpy.data.texts['Notes'].from_string(...)", text)
        self.assertNotIn(".as_string() = ", text)

if __name__ == "__main__":
    unittest.main()
