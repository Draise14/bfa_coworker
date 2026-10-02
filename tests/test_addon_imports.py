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


class TestNoPathStrFormat(unittest.TestCase):
    """``Path`` objects must not be formatted with ``{:s}``.

    ``"{:s}".format(Path(...))`` raises
    ``TypeError: unsupported format string passed to WindowsPath.__format__``
    at runtime (Blender aborted the operator).  This guards the known
    Path-returning helpers: a bare ``helper()`` call is unsafe whenever it is
    used as a value (e.g. a ``.format()`` argument); it is safe only when
    wrapped in ``str(...)``, used with a path operator (``.parent`` / ``/``),
    or when the call is a definition / plain assignment.

    The scan is over the WHOLE source (not line by line) because the bug this
    pins had the format string and the offending call on different lines.
    """

    _PATH_HELPERS = (
        "_chat_history_path",
        "_chat_history_dir",
        "_session_memory_state_path",
        "_session_memory_archive_path",
        "_rules_dir",
        "_llama_server_log_path",
        "_get_bundled_llama_dir",
        "_get_models_dir",
    )

    def _unsafe_uses(self, src):
        pattern = re.compile(
            r"(?<![\w.])(%s)\(\s*\)" % "|".join(self._PATH_HELPERS))
        bad = []
        for m in pattern.finditer(src):
            prefix = src[:m.start()].rstrip()
            suffix = src[m.end():].lstrip()
            if prefix.endswith("str("):
                continue  # str(helper()) -- safe
            if prefix.endswith("def") or prefix.endswith("=") \
                    or prefix.endswith("return"):
                continue  # definition / assignment -- no formatting
            if suffix[:1] == "." or suffix.startswith("/"):
                continue  # path operator (.parent / ` / `) -- safe
            lineno = src.count("\n", 0, m.start()) + 1
            bad.append("{:d}: {:s}".format(lineno, m.group(0)))
        return bad

    def test_no_bare_path_in_str_format(self):
        offenders = {}
        for path in _modules():
            bad = self._unsafe_uses(_read(path))
            if bad:
                offenders[os.path.basename(path)] = bad
        self.assertEqual(
            offenders, {},
            "Path helper used as a bare value (needs str()): {!r}".format(offenders))


if __name__ == "__main__":
    unittest.main()
