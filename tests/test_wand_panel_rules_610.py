# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The parts of the wand panel rules (``_misc/wand_panel_rules.md``, #610) that a script can check.
bpy-free: the add-on's sources are read with ``ast``.

1. Every button and sub-menu drawn by a wand panel, menu or entry table has an icon.
2. Every section header of the wand panels has an icon.
3. Every drop-down entry of the Reframe operators has an icon (and the layout table a fallback icon).
4. Order: what cuts or resets comes last (Reframe menu, the transcript panel's wands).
5. The checker itself catches a button without an icon.

The tooltip line (#607) is ``tests/test_wand_tooltips.py``.

Run with::

    python -m unittest tests.test_wand_panel_rules_610 -v
"""

__all__ = ()

import ast
import importlib
import sys
import types
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"

# (file, the classes / functions whose body draws buttons or menus)
_BUTTON_SCOPES = (
    ("transcript_edit_ops.py", ("BFACW_PT_transcript", "_draw_header", "_wand")),
    ("captions_ops.py", ("BFACW_MT_captions", "_draw_context_menu")),
    ("reframe.py", ("BFACW_MT_reframe",)),
    ("reframe_layouts.py", ("_layout_button", "_draw_buttons", "BFACW_MT_reframe_layout")),
    ("wand/reframe_entries.py", ("LayoutEntry", "ActionEntry")),
    ("wand/sequencer.py", ("CoworkerEntry", "_draw_strip_menu")),
    ("wand/icons.py", ("operator", "menu")),
    ("highlight_picks_ops.py", ("_draw_panel", "_draw_pick", "_draw_dialog")),       # #614
    ("chapters_ops.py", ("_draw_dialog", "_draw_progress")),                          # #542 Make Chapters
    ("reframe_zoom.py", ("BFACW_MT_reframe_zoom_to",)),                               # #542 Zoom To
)
# Panels whose labels that are plain text (not a formatted message) are section headers.
_HEADER_SCOPES = (
    ("transcript_edit_ops.py", ("_wands_header",)),
    ("reframe_layouts.py", ("_draw_buttons",)),
    ("highlight_picks_ops.py", ("_draw_dialog",)),                                  # #614
    ("chapters_ops.py", ("_draw_dialog", "_draw_progress")),                        # #542
    ("reframe_zoom.py", ("BFACW_OT_reframe_zoom_to",)),                             # #542 the dialog's boxes
)


def _tree(relative: str) -> ast.Module:
    return ast.parse((_ADDON / relative).read_text(encoding="utf-8"))


def _scoped(tree: ast.Module, names: tuple) -> list:
    return [n for n in tree.body if getattr(n, "name", "") in names]


def _has_icon(call: ast.Call) -> bool:
    return any(k.arg in ("icon", "icon_value") or k.arg is None for k in call.keywords)


def _bare_calls(tree: ast.AST, methods: tuple) -> list:
    """Calls ``x.operator(...)`` / ``x.menu(...)`` / ``x.label(...)`` in *tree* without an icon."""
    # ``icons.operator(...)`` is the wand-icon helper: it gives the icon (or its fallback) itself
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") in methods and not _has_icon(n)
            and getattr(n.func.value, "id", "") not in ("icons", "wand_icons")]


class Buttons(unittest.TestCase):
    def test_every_button_and_submenu_has_an_icon(self) -> None:
        total = 0
        for relative, names in _BUTTON_SCOPES:
            nodes = _scoped(_tree(relative), names)
            self.assertEqual(len(nodes), len(names), "{:s}: a scope vanished: {!r}".format(relative, names))
            for node in nodes:
                total += sum(1 for n in ast.walk(node) if isinstance(n, ast.Call)
                             and getattr(n.func, "attr", "") in ("operator", "menu"))
                self.assertEqual(_bare_calls(node, ("operator", "menu")), [],
                                 "{:s}: a button without an icon in {:s}".format(relative, node.name))
        self.assertGreater(total, 25)

    def test_the_checker_catches_a_button_without_an_icon(self) -> None:
        code = ast.parse("def d(layout):\n    layout.operator('a.b', text='X')\n"
                         "    layout.operator('a.c', icon='X')\n    layout.menu('M')\n")
        self.assertEqual(_bare_calls(code, ("operator", "menu")), [2, 4])


class Headers(unittest.TestCase):
    def test_every_section_header_has_an_icon(self) -> None:
        seen = 0
        for relative, names in _HEADER_SCOPES:
            nodes = _scoped(_tree(relative), names)
            self.assertEqual(len(nodes), len(names), relative)
            for node in nodes:
                labels = [n for n in ast.walk(node) if isinstance(n, ast.Call)
                          and getattr(n.func, "attr", "") == "label"]
                seen += len(labels)
                self.assertTrue(labels, "{:s} draws no header any more".format(node.name))
                for label in labels:
                    self.assertTrue(_has_icon(label), "{:s} line {:d}: a header without an icon".format(
                        relative, label.lineno))
        self.assertGreaterEqual(seen, 3)


class DropDowns(unittest.TestCase):
    @staticmethod
    def _table(name: str) -> tuple:
        for node in _tree("reframe.py").body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
                return ast.literal_eval(node.value)
        raise AssertionError(name)

    def test_reframe_entries_carry_a_tooltip_and_an_icon(self) -> None:
        for name in ("RESOLUTIONS", "MODES", "_ACTIONS"):
            rows = self._table(name)
            self.assertGreaterEqual(len(rows), 2, name)
            for row in rows:
                self.assertEqual(len(row), 4, "{:s} {!r}: (id, label, tooltip, icon)".format(name, row[0]))
                self.assertTrue(row[2] and row[3] and row[3].isupper(), "{:s} {!r}".format(name, row[0]))

    def test_the_operators_build_their_items_with_the_icons(self) -> None:
        source = (_ADDON / "reframe.py").read_text(encoding="utf-8")
        for table in ("RESOLUTIONS", "MODES", "_ACTIONS"):
            self.assertIn("items=_enum_items({:s})".format(table), source)
        self.assertIn("safe_icon(item.fallback)", (_ADDON / "reframe_layouts.py").read_text(encoding="utf-8"))

    def test_every_layout_has_a_fallback_icon(self) -> None:
        pkg = types.ModuleType("bfacw_rules_610_pkg")
        pkg.__path__ = [str(_ADDON)]
        sys.modules[pkg.__name__] = pkg
        rl = importlib.import_module(pkg.__name__ + ".wand.reframe_layout")
        for item in rl.LAYOUTS:
            self.assertTrue(item.fallback and item.description, item.id)


class Order(unittest.TestCase):
    def test_reset_framing_is_the_last_entry_of_the_reframe_menu(self) -> None:
        draw = next(f for c in _scoped(_tree("reframe.py"), ("BFACW_MT_reframe",)) for f in c.body
                    if getattr(f, "name", "") == "draw")
        ops = [n for n in ast.walk(draw) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "operator"]
        ops.sort(key=lambda n: (n.lineno, n.col_offset))
        self.assertIn("Reset Framing", ast.unparse(ops[-1]))

    def test_the_transcript_panels_wands_end_with_the_ones_that_remove(self) -> None:
        panel = _scoped(_tree("transcript_edit_ops.py"), ("BFACW_PT_transcript",))[0]
        calls = [n for n in ast.walk(panel) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_wand"]
        calls.sort(key=lambda n: n.lineno)
        names = [ast.unparse(n.args[1]) for n in calls]
        self.assertEqual(len(names), 4, names)
        self.assertIn("make_captions", names[0])
        self.assertIn("make_chapters", names[1])        # #542: what makes comes before what removes
        self.assertTrue(all("remove" in n for n in names[2:]), names)

    def test_a_trailing_dots_label_means_a_dialog_always_opens(self) -> None:
        tree = _tree("transcript_edit_ops.py")
        classes = {c.name: c for c in tree.body if isinstance(c, ast.ClassDef)}
        by_idname = {}
        for path in sorted(_ADDON.glob("*.py")):         # an operator of another module: found by its idname
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, ast.ClassDef):
                    for stmt in node.body:
                        if isinstance(stmt, ast.Assign) and getattr(stmt.targets[0], "id", "") == "bl_idname"                                 and isinstance(stmt.value, ast.Constant):
                            by_idname[stmt.value.value] = node
        panel = classes["BFACW_PT_transcript"]
        checked = 0
        for call in ast.walk(panel):
            if not (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "_wand"):
                continue
            arg = call.args[1]
            cls = classes[arg.value.id] if isinstance(arg, ast.Attribute) else by_idname[arg.value]   # BFACW_OT_x.bl_idname
            label = call.args[2].value
            invoke = next(f for f in cls.body if getattr(f, "name", "") == "invoke")
            src = ast.unparse(invoke)
            # a dialog opens every time: no early path that runs without one
            always = ("invoke_props_dialog" in src or "INVOKE_DEFAULT" in src) and "self.execute" not in src
            self.assertEqual(label.endswith("..."), always, "{:s}: '...' only for an always-dialog".format(label))
            checked += 1
        self.assertEqual(checked, 4)


if __name__ == "__main__":
    unittest.main()
