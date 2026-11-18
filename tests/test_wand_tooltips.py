# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Every wand operator's tooltip ends with its own line saying whether the agent has to be
started (#607).  bpy-free: the add-on's sources are read with ``ast``.

1. The helpers in ``wand/spec.py`` (the shared constant, the line for a part that needs a
   model, the line for a wand that is all model work).
2. Each operator in ``WAND_OPERATORS``, and each ``coworker.*`` operator, has a class whose
   ``bl_description`` (or ``description()`` classmethod) goes through ``agent_line`` /
   ``needs_agent_line``.
3. Every operator drawn with a wand icon (``wand/icons.operator``, ``_wand_op``) or listed in
   the Strip > Coworker section is in ``WAND_OPERATORS``, so a new wand cannot skip the line.

Run with::

    python -m unittest tests.test_wand_tooltips -v
"""

__all__ = ()

import ast
import importlib.util
import sys
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_HELPERS = ("agent_line", "needs_agent_line")


def _load_spec():
    spec = importlib.util.spec_from_file_location("bfacw_wand_spec_607", _ADDON / "wand" / "spec.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod        # dataclasses look the module up
    spec.loader.exec_module(mod)
    return mod


SPEC = _load_spec()


def _operator_classes() -> dict:
    """``bl_idname`` (a literal, or ``SPEC.bl_idname`` of the framework) -> (file, class node)."""
    out = {}
    for path in sorted(_ADDON.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if (isinstance(stmt, ast.Assign) and any(getattr(t, "id", "") == "bl_idname" for t in stmt.targets)):
                    if isinstance(stmt.value, ast.Constant):
                        out[stmt.value.value] = (path, node)
                    elif path.name == "sequencer.py" and path.parent.name == "wand":
                        out["coworker.sequencer_remove_silences"] = (path, node)
    return out


def _uses_helper(node: ast.ClassDef) -> bool:
    """The class gives its tooltip through the helper: ``bl_description = agent_line(...)`` and, when
    it has one, a ``description`` classmethod that calls it too."""
    found = []
    for stmt in node.body:
        if isinstance(stmt, ast.Assign) and any(getattr(t, "id", "") == "bl_description" for t in stmt.targets):
            found.append(_calls_helper(stmt.value))
        elif isinstance(stmt, ast.FunctionDef) and stmt.name == "description":
            found.append(_calls_helper(stmt))
    return bool(found) and all(found)


def _calls_helper(tree: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) and getattr(n.func, "id", "") in _HELPERS for n in ast.walk(tree))


def _wand_drawn() -> set:
    """Operator ids drawn with a wand icon or listed in the Strip > Coworker section."""
    ids = set()
    for path in sorted(_ADDON.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                owner = getattr(getattr(node.func, "value", None), "id", "")
                if name == "_wand_op" or (name == "operator" and owner in ("icons", "wand_icons")):
                    for arg in node.args[:2]:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "." in arg.value:
                            ids.add(arg.value)
            if (isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "OTHERS" for t in node.targets)
                    and path.name == "sequencer.py"):
                ids.update(e.elts[0].value for e in node.value.elts)
    return ids


class Helpers(unittest.TestCase):
    def test_plain_line_is_the_last_line(self) -> None:
        text = SPEC.agent_line("Cut the quiet parts.\n")
        self.assertEqual(text, "Cut the quiet parts.\nWorks without the agent started.")
        self.assertEqual(text.splitlines()[-1], SPEC.NO_AGENT_LINE)

    def test_part_that_needs_a_model_is_named_on_the_line(self) -> None:
        text = SPEC.agent_line("Follow it.", "finding the subject, which needs a vision model")
        self.assertEqual(text.splitlines()[-1], "Works without the agent started, except finding the subject, "
                                                "which needs a vision model.")

    def test_all_model_work_says_so(self) -> None:
        text = SPEC.needs_agent_line("Translate this.", "it does the translating")
        self.assertEqual(text.splitlines()[-1], "Needs the agent started: it does the translating.")


class EveryWand(unittest.TestCase):
    classes = _operator_classes()

    def test_registry_has_no_duplicates_and_all_exist(self) -> None:
        self.assertEqual(len(set(SPEC.WAND_OPERATORS)), len(SPEC.WAND_OPERATORS))
        for idname in SPEC.WAND_OPERATORS:
            self.assertIn(idname, self.classes, idname)

    def test_every_wand_operator_has_the_line(self) -> None:
        wands = set(SPEC.WAND_OPERATORS) | {i for i in self.classes if i.startswith("coworker.")}
        self.assertIn("coworker.sequencer_remove_silences", wands)
        for idname in sorted(wands):
            _path, node = self.classes[idname]
            self.assertTrue(_uses_helper(node), "{:s} (class {:s}) has no 'agent started' tooltip line: "
                            "bl_description = agent_line(...)".format(idname, node.name))

    def test_every_operator_drawn_with_a_wand_icon_is_a_wand(self) -> None:
        drawn = {i for i in _wand_drawn() if i in self.classes}
        self.assertTrue({"bfacw.translate", "bfacw.auto_captions", "bfacw.explain"} <= drawn, drawn)
        missing = sorted(drawn - set(SPEC.WAND_OPERATORS) - {i for i in drawn if i.startswith("coworker.")})
        self.assertEqual(missing, [], "drawn with a wand icon but not in wand/spec.py WAND_OPERATORS")

    def test_the_wand_without_the_helper_is_caught(self) -> None:
        node = ast.parse('class A:\n    bl_idname = "x.y"\n    bl_description = "plain"\n').body[0]
        self.assertFalse(_uses_helper(node))


if __name__ == "__main__":
    unittest.main()
