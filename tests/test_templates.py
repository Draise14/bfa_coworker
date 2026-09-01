"""
Tests for the template system in addon/bfa_coworker/blender_templates.py.

Covers:
- All 18 registered templates render valid Python (compile() passes).
- Default parameters fill in when params are omitted.
- ``_plan_to_code`` assembles multi-step plans into executable code.
- Unknown template names / malformed steps degrade gracefully.

Run with::

    python -m unittest tests.test_templates -v
"""

__all__ = ()

import os
import types
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADDON_DIR = os.path.join(_REPO, "addon")


def _load_templates():
    """Load blender_templates.py standalone (the package __init__ needs bpy)."""
    src_path = os.path.join(_ADDON_DIR, "bfa_coworker", "blender_templates.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        source = fh.read()
    mod = types.ModuleType("templates_under_test")
    exec(compile(source, src_path, "exec"), mod.__dict__)
    return mod


_mod = _load_templates()
_TEMPLATES = _mod._TEMPLATES
_DEFAULTS = _mod._TEMPLATE_DEFAULTS
_render_template = _mod._render_template
_plan_to_code = _mod._plan_to_code
_TEMPLATE_META = _mod._TEMPLATE_META
_get_templates_for_editor = _mod.get_templates_for_editor


class TestTemplateRegistry(unittest.TestCase):
    """Registry shape: 18 templates, all callable, defaults present."""

    def test_count(self):
        self.assertGreaterEqual(len(_TEMPLATES), 18)

    def test_all_render_valid_python(self):
        for name, tmpl in _TEMPLATES.items():
            with self.subTest(template=name):
                self.assertTrue(callable(tmpl))
                code = _render_template(name)
                self.assertIsInstance(code, str)
                self.assertIn("import bpy", code)
                # Must be syntactically valid Python.
                compile(code, "<template:{:s}>".format(name), "exec")

    def test_defaults_used_when_params_omitted(self):
        code = _render_template("create_cube")
        self.assertIn("size=1.0", code)

    def test_params_override_defaults(self):
        code = _render_template("create_cube", {"size": 2.5})
        self.assertIn("size=2.5", code)

    def test_unknown_template_returns_none(self):
        self.assertIsNone(_render_template("no_such_template"))

    def test_template_defaults_coverage(self):
        """Every default key referenced by the templates is in _TEMPLATE_DEFAULTS."""
        for name in _TEMPLATES:
            code = _render_template(name, {})
            # Anything still left as a literal {key} placeholder means a
            # missing default — the render functions format with defaults,
            # so a leftover brace is a bug.
            self.assertNotIn("{", code.replace("{:d}", "").replace("{:s}", ""),
                             "template {:s} left an unresolved placeholder".format(name))


class TestPlanToCode(unittest.TestCase):
    """_plan_to_code assembles steps into runnable code."""

    def test_single_template_step(self):
        code = _plan_to_code([{"template": "create_cube", "params": {"name": "TestCube"}}])
        self.assertIn("import bpy", code)
        self.assertIn("primitive_cube_add", code)
        self.assertIn('"TestCube"', code)
        compile(code, "<plan>", "exec")

    def test_multi_step_plan(self):
        steps = [
            {"template": "create_cube", "params": {"name": "A"}},
            {"template": "add_material", "params": {"mat_name": "Red"}},
            {"code": "result = {'status': 'ok'}"},
        ]
        code = _plan_to_code(steps)
        self.assertIn("primitive_cube_add", code)
        self.assertIn("bpy.data.materials.new(name=\"Red\")", code)
        self.assertIn("result = {'status': 'ok'}", code)
        compile(code, "<plan>", "exec")

    def test_unknown_template_step_skipped(self):
        code = _plan_to_code([{"template": "no_such", "params": {}}])
        self.assertIn("import bpy", code)

    def test_empty_plan_returns_header(self):
        code = _plan_to_code([])
        self.assertIn("import bpy", code)
        self.assertIn("import math", code)

    def test_material_template_creates_material(self):
        code = _render_template("add_material", {"mat_name": "Gold", "r": 1.0, "g": 0.8, "b": 0.0})
        self.assertIn('"Gold"', code)
        compile(code, "<template:add_material>", "exec")


class TestTemplateMetadata(unittest.TestCase):
    """Per-template metadata (Tier 4 prep): tier, editor, mode, flags."""

    def test_all_templates_have_metadata(self):
        for name in _TEMPLATES:
            with self.subTest(template=name):
                meta = _TEMPLATE_META.get(name)
                self.assertIsNotNone(meta, "missing metadata for " + name)
                self.assertIn("tier", meta)
                self.assertIn("editor", meta)
                self.assertIn("mode", meta)
                self.assertIn("creates_datablocks", meta)
                self.assertIn("is_destructive", meta)
                self.assertIn("chainable", meta)

    def test_get_templates_for_view3d(self):
        names = _get_templates_for_editor("VIEW_3D")
        self.assertGreaterEqual(len(names), 16)
        self.assertIn("create_cube", names)
        self.assertIn("add_subsurf", names)

    def test_get_templates_unknown_editor(self):
        self.assertEqual(_get_templates_for_editor("NO_SUCH_EDITOR"), [])

    def test_metadata_matches_registry(self):
        """Every metadata entry names a real template, and vice versa."""
        self.assertEqual(set(_TEMPLATE_META.keys()), set(_TEMPLATES.keys()))


if __name__ == "__main__":
    unittest.main()
