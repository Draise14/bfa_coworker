# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Static import guards for the addon's Blender modules.

A whole class of import-time crashes slipped through because the unit tests
that load ``agent_controller`` by exec'ing its source pre-seed the module
namespace, masking missing imports:

* ``agent_controller`` used ``types.SimpleNamespace`` without ``import types``
  (the transport split dropped the import) — ``NameError`` at import.
* ``ui_chat`` used the bare ``IntProperty`` without importing it from
  ``bpy.props`` — ``NameError`` when ``register()`` imported the module.

These checks scan the real source so a missing import fails here rather than
in Blender.  They are deliberately conservative (qualified ``bpy.props.X`` and
stdlib modules used inside strings/comments are not counted).

Run with::

    python -m unittest tests.test_addon_imports -v
"""

__all__ = ()

import glob
import os
import re
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADDON_DIR = os.path.join(_REPO, "addon", "bfa_coworker")

_PROP_TYPES = (
    "BoolProperty", "IntProperty", "FloatProperty", "StringProperty",
    "EnumProperty", "PointerProperty", "FloatVectorProperty",
    "IntVectorProperty", "CollectionProperty",
)


def _modules():
    return sorted(glob.glob(os.path.join(_ADDON_DIR, "*.py")))


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _imported_prop_names(src):
    """Names imported from ``bpy.props`` (parenthesised and plain forms)."""
    names = set()
    for m in re.finditer(r"from bpy\.props import \(([^)]*)\)", src, re.S):
        for n in re.split(r"[,\s]+", m.group(1)):
            if n.strip():
                names.add(n.strip())
    for m in re.finditer(r"from bpy\.props import ([A-Za-z0-9_, ]+)", src):
        for n in m.group(1).split(","):
            if n.strip():
                names.add(n.strip())
    return names


class TestBpyPropImports(unittest.TestCase):
    """Every bare bpy.props call must have its name imported."""

    def test_no_missing_bpy_prop_imports(self):
        offenders = {}
        for path in _modules():
            src = _read(path)
            imported = _imported_prop_names(src)
            used = set()
            for prop in _PROP_TYPES:
                # Bare usage only (not ``bpy.props.IntProperty``).
                if re.search(r"(?<![\w.])" + prop + r"\s*\(", src):
                    used.add(prop)
            missing = sorted(used - imported)
            if missing:
                offenders[os.path.basename(path)] = missing
        self.assertEqual(offenders, {}, "missing bpy.props imports: {!r}".format(offenders))


class TestCoreModuleImports(unittest.TestCase):
    """Names used at import time must actually be imported."""

    def test_agent_controller_imports_types(self):
        src = _read(os.path.join(_ADDON_DIR, "agent_controller.py"))
        if "types.SimpleNamespace(" in src:
            self.assertRegex(
                src, r"(?m)^import types$",
                "agent_controller uses types.* but does not 'import types'")

    def test_every_addon_module_parses(self):
        import ast
        for path in _modules():
            with self.subTest(module=os.path.basename(path)):
                ast.parse(_read(path))


if __name__ == "__main__":
    unittest.main()
