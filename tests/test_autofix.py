"""
Tests for the auto-fix rules in addon/bfa_coworker/autofix.py.

Every rule is verified with a representative input/output pair, and the
function contract (``(code, fixes)`` tuple) is checked.

Run with::

    python -m unittest tests.test_autofix -v
"""

__all__ = ()

import os
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADDON_DIR = os.path.join(_REPO, "addon")


def _load_autofix():
    """Load autofix.py standalone (the package __init__ needs bpy)."""
    import types
    src_path = os.path.join(_ADDON_DIR, "bfa_coworker", "autofix.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        source = fh.read()
    mod = types.ModuleType("autofix_under_test")
    exec(compile(source, src_path, "exec"), mod.__dict__)
    return mod._autofix_code


_autofix_code = _load_autofix()


class TestAutofixContract(unittest.TestCase):
    """The function returns (fixed_code, fixes_list)."""

    def test_returns_tuple(self):
        code, fixes = _autofix_code("pass")
        self.assertIsInstance(code, str)
        self.assertIsInstance(fixes, list)

    def test_clean_code_untouched(self):
        code = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
        fixed, fixes = _autofix_code(code)
        self.assertEqual(fixed, code)
        self.assertEqual(fixes, [])


class TestAutofixRules(unittest.TestCase):
    """Each rule matches and replaces its target pattern."""

    CASES = [
        # (input, expected_substring, fix_description_fragment)
        ("bpy.data.lamps['Key'].data", "bpy.data.lights['Key'].data", "lamps -> lights"),
        ('render.engine = "EEVEE"', '"BLENDER_EEVEE"', "EEVEE -> BLENDER_EEVEE"),
        ("bpy.context.scene.render.eevee.taa_samples = 8",
         "scene.eevee.taa_samples", "render.eevee -> scene.eevee"),
        ("obj.data.use_auto_smooth = True", "obj.data.auto_smooth_angle", "auto_smooth"),
        ("for fc in action.fcurves: fc.keyframe_points", "keyframe_insert", "fcurves"),
        ("bpy.data.node_groups.new('N', 'ShaderNodeEnvironment')",
         "ShaderNodeTexEnvironment", "ShaderNodeEnvironment"),
        ("bpy.data.node_groups.new('N', 'ShaderNodeWorldOutput')",
         "ShaderNodeOutputWorld", "ShaderNodeWorldOutput"),
        ('mat.node_tree.nodes["Principled BSDF"]["Use Nodes"]', "use_nodes = True", "Use Nodes"),
        ("mod.subdivisions = 3", "mod.levels = 3", "subdivisions -> levels"),
        ("mat.node_tree.nodes['P'].base_color = (1, 0, 0)",
         "inputs['Base Color'].default_value", "base_color"),
        # The rule drops one extra arg per pass (regex is single-shot).
        ("bm.update_edit_mesh(mesh, True)", "update_edit_mesh(mesh)", "update_edit_mesh"),
        ("obj.data.material_slots[0].material", "obj.material_slots[0].material",
         "data.material_slots"),
    ]

    def test_each_rule(self):
        for raw, expected, desc in self.CASES:
            with self.subTest(rule=desc):
                fixed, fixes = _autofix_code(raw)
                self.assertIn(expected, fixed)
                self.assertTrue(any(desc in f for f in fixes),
                                "expected fix description {!r} in {!r}".format(desc, fixes))

    def test_multiple_rules_apply(self):
        raw = "bpy.data.lamps['Key'].use_auto_smooth = True"
        fixed, fixes = _autofix_code(raw)
        self.assertIn("bpy.data.lights", fixed)
        self.assertIn("auto_smooth_angle", fixed)
        self.assertEqual(len(fixes), 2)

    def test_no_side_effects_on_clean(self):
        """Clean code passes through byte-identical."""
        raw = "import bpy\nresult = {'status': 'ok'}"
        fixed, fixes = _autofix_code(raw)
        self.assertEqual(fixed, raw)
        self.assertEqual(fixes, [])


if __name__ == "__main__":
    unittest.main()
