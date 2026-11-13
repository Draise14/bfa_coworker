# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Coworker works in Blender, not only in Bforartists (#456).

Bforartists has about 1,300 icons Blender lacks.  In Blender a layout call with one of
them fails, and a class with one (the Coworker editor's ``bl_icon = "WIZARD"``) stops
``register()`` part way.  Every Bforartists-only icon must go through
``shared.icon()`` (imported as ``safe_icon``), which falls back to a Blender icon.

1. No Bforartists-only icon is written anywhere except through the helper.
2. Every fallback is an icon Blender 5.1 and 5.2 have.
3. ``icon()`` keeps an icon the build has, falls back otherwise, and passes names
   through without bpy (tests).
4. ``is_bforartists()`` asks ``bpy.app.bfa_version_string``: ``VIEW3D_MT_view``,
   the old check, exists in Blender too.

The icon lists in ``tests/data`` come from Blender 5.1.2's ``UI_icons.hh``, Blender
5.2.4's ``UILayout`` icon enum and Bforartists 5.3's ``UI_icons.hh``.

Run with::

    python -m unittest tests.test_blender_icons -v
"""

__all__ = ()

import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_ADDON = _ROOT / "addon" / "bfa_coworker"
_DATA = Path(__file__).resolve().parent / "data"


def _load_shared():
    """shared.py on its own: the package's __init__ needs bpy."""
    spec = importlib.util.spec_from_file_location("bfacw_shared_for_icons", _ADDON / "shared.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SHARED = _load_shared()

# Names that match a Bforartists icon but are used as plain values here.
_NOT_ICONS = {
    "CENTER",       # layout.alignment
    "DELETE", "EDIT",   # queue actions (enum ids)
    "NEW",          # a status word
    "STRING",       # property types
    "TYPE",         # an @ suggestion category
    "UI",           # a region type
}
_HELPERS = {"safe_icon", "icon"}


def _names(file_name: str) -> set[str]:
    lines = (_DATA / file_name).read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def _is_gpu_action(parent: ast.AST, node: ast.AST) -> bool:
    """``("attach_screen", "MAKE_SCREENSHOT")``: the GPU chat's own buttons, drawn
    from its own icon images (ui_gpu/icons), never through Blender's icon enum."""
    if not isinstance(parent, ast.Tuple) or len(parent.elts) != 2 or parent.elts[1] is not node:
        return False
    first = parent.elts[0]
    return isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.islower()


def _unsafe_uses(bfa_only: set[str]) -> list[str]:
    found = []
    for path in sorted(_ADDON.rglob("*.py")):
        rel = path.relative_to(_ADDON).as_posix()
        if rel.startswith(("ui_gpu/", "vendor/")):
            continue        # the GPU chat draws its own icon images
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if node.value not in bfa_only or node.value in _NOT_ICONS:
                continue
            parent = parents.get(node)
            if isinstance(parent, ast.Set):
                continue        # self.report({"WARNING"}, ...): a report type, not an icon
            if isinstance(parent, ast.Call) and getattr(parent.func, "id", "") in _HELPERS:
                continue
            if isinstance(parent, ast.Dict) and rel == "shared.py":
                continue        # ICON_FALLBACKS itself
            if _is_gpu_action(parent, node):
                continue
            found.append("{:s}:{:d} {:s}".format(rel, node.lineno, node.value))
    return found


class TestNoBforartistsOnlyIcons(unittest.TestCase):

    def test_every_bforartists_only_icon_goes_through_the_helper(self):
        found = _unsafe_uses(_names("bforartists_only_icons.txt"))
        self.assertEqual(found, [], "use shared.icon() (safe_icon) and add a Blender fallback")

    def test_the_fallbacks_are_blender_icons(self):
        shared = _SHARED
        blender = _names("blender_icons.txt")
        for name, fallback in shared.ICON_FALLBACKS.items():
            self.assertIn(fallback, blender, name)
            self.assertNotIn(name, blender, "{:s} is in Blender: no fallback needed".format(name))


def _fake_bpy(icons, bfa=False):
    enum = types.SimpleNamespace(enum_items={name: None for name in icons})
    prop = types.SimpleNamespace(parameters={"icon": enum})
    bl_rna = types.SimpleNamespace(functions={"prop": prop})
    app = types.SimpleNamespace(**({"bfa_version_string": "5.2.3"} if bfa else {}))
    return types.SimpleNamespace(app=app, types=types.SimpleNamespace(UILayout=types.SimpleNamespace(bl_rna=bl_rna)))


class TestIconHelper(unittest.TestCase):

    def setUp(self):
        self.shared = _SHARED
        self._saved = sys.modules.get("bpy")
        _SHARED._ICONS = None

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = self._saved
        self.shared._ICONS = None

    def _with(self, bpy):
        sys.modules["bpy"] = bpy
        self.shared._ICONS = None

    def test_blender_gets_the_fallback(self):
        self._with(_fake_bpy({"GHOST_ENABLED", "ERROR", "WINDOW", "PLUGIN"}))
        self.assertEqual(self.shared.icon("WIZARD"), "GHOST_ENABLED")
        self.assertEqual(self.shared.icon("WARNING"), "ERROR")
        self.assertEqual(self.shared.icon("MAKE_SCREENSHOT"), "WINDOW")
        self.assertEqual(self.shared.icon("EXTENSION"), "PLUGIN")
        self.assertFalse(self.shared.is_bforartists())

    def test_bforartists_keeps_its_icons(self):
        self._with(_fake_bpy({"WIZARD", "WARNING", "GHOST_ENABLED"}, bfa=True))
        self.assertEqual(self.shared.icon("WIZARD"), "WIZARD")
        self.assertEqual(self.shared.icon("WARNING"), "WARNING")
        self.assertTrue(self.shared.is_bforartists())

    def test_an_unknown_icon_without_a_fallback_draws_none(self):
        self._with(_fake_bpy({"ERROR"}))
        self.assertEqual(self.shared.icon("NOT_AN_ICON"), "NONE")
        self.assertEqual(self.shared.icon("WIZARD"), "NONE", "the fallback missing too")

    def test_without_bpy_names_pass_through(self):
        sys.modules.pop("bpy", None)
        self.shared._ICONS = None
        self.assertEqual(self.shared.icon("WIZARD"), "WIZARD")


if __name__ == "__main__":
    unittest.main()
