"""
Tests for the preflight code validation in mcp_to_blender_server.py.
"""
__all__ = ()

import sys
import os
import unittest

# Add the addon directory to the path so we can import the module.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADDON_DIR = os.path.join(_REPO, "addon")
if _ADDON_DIR not in sys.path:
    sys.path.insert(0, _ADDON_DIR)

# Import the preflight function directly from the module source.
# We can't import the full module because it depends on Blender's bpy.
import importlib.util
import types


def _load_preflight():
    """Load _preflight_check from mcp_to_blender_server.py without full import."""
    src_path = os.path.join(_ADDON_DIR, "bfa_coworker", "mcp_to_blender_server.py")
    with open(src_path, "r", encoding="utf-8") as f:
        source = f.read()

    # Extract just the _preflight_check function and its imports.
    # The function only uses `re` from stdlib.
    func_start = source.find("\ndef _preflight_check(")
    if func_start < 0:
        raise ImportError("_preflight_check not found in source")
    func_start += 1  # skip the leading newline

    # Find the end of the function (next top-level def or class at same indent).
    func_end = len(source)
    search_from = func_start + 100  # skip past the def line
    for marker in ["\ndef _", "\nclass ", "\n# ---"]:
        idx = source.find(marker, search_from)
        if idx >= 0 and idx < func_end:
            func_end = idx

    func_source = source[func_start:func_end]

    # Create a module with just re available.
    mod = types.ModuleType("_preflight_test")
    mod.__dict__["re"] = __import__("re")
    exec(func_source, mod.__dict__)
    return mod._preflight_check


_preflight_check = _load_preflight()


class TestPreflightCheck(unittest.TestCase):
    """Tests for _preflight_check()."""

    def test_clean_code_passes(self):
        """Code with no issues passes the preflight check."""
        code = """
import bpy
bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.active_object
print(obj.name)
"""
        issues = _preflight_check(code)
        self.assertEqual(issues, [])

    def test_missing_bpy_import(self):
        """Code using bpy without import is caught."""
        code = "bpy.ops.mesh.primitive_cube_add()"
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("missing_bpy", names)

    def test_wrong_subdivisions_attr(self):
        """mod.subdivisions is caught."""
        code = """
import bpy
obj = bpy.ops.mesh.primitive_cube_add()
mod = obj.modifiers.new("Subsurf", 'SUBSURF')
mod.subdivisions = 3
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_subdiv_attr", names)

    def test_correct_levels_not_flagged(self):
        """mod.levels is NOT flagged (it's correct)."""
        code = """
import bpy
obj = bpy.ops.mesh.primitive_cube_add()
mod = obj.modifiers.new("Subsurf", 'SUBSURF')
mod.levels = 3
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("wrong_subdiv_attr", names)

    def test_wrong_base_color(self):
        """principled.base_color is caught."""
        code = """
import bpy
mat = bpy.data.materials.new("Test")
mat.use_nodes = True
bsdf = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
bsdf.base_color = (1, 0, 0)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_principled_attr", names)

    def test_correct_inputs_not_flagged(self):
        """principled.inputs['Base Color'] is NOT flagged."""
        code = """
import bpy
mat = bpy.data.materials.new("Test")
mat.use_nodes = True
bsdf = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
bsdf.inputs['Base Color'].default_value = (1, 0, 0, 1)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("wrong_principled_attr", names)

    def test_wrong_torus_kw(self):
        """ring_count on torus is caught."""
        code = """
import bpy
bpy.ops.mesh.primitive_torus_add(ring_count=32)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_torus_kw", names)

    def test_wrong_sequencer_api(self):
        """editor.sequences is caught."""
        code = """
import bpy
editor = bpy.context.scene.sequence_editor
for strip in editor.sequences:
    print(strip.name)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_sequencer_api", names)

    def test_wrong_auto_smooth(self):
        """use_auto_smooth is caught."""
        code = """
import bpy
bpy.context.active_object.data.use_auto_smooth = True
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_auto_smooth", names)

    def test_wrong_fcurves(self):
        """action.fcurves is caught."""
        code = """
import bpy
obj = bpy.context.active_object
for fc in obj.animation_data.action.fcurves:
    print(fc.data_path)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_fcurves", names)

    def test_multiple_issues(self):
        """Multiple issues are all reported."""
        code = """
bpy.ops.mesh.primitive_torus_add(ring_count=32)
bpy.context.active_object.data.use_auto_smooth = True
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("missing_bpy", names)
        self.assertIn("wrong_torus_kw", names)
        self.assertIn("wrong_auto_smooth", names)

    def test_no_output_warning(self):
        """Long code without print or result gets a warning."""
        code = """
import bpy
for i in range(100):
    obj = bpy.ops.mesh.primitive_cube_add()
    obj.location = (i * 2, 0, 0)
    mod = obj.modifiers.new("Subsurf", 'SUBSURF')
    mod.levels = 2
    mod = obj.modifiers.new("Bevel", 'BEVEL')
    mod.width = 0.1
    mod.segments = 3
    mat = bpy.data.materials.new(f"Mat_{i}")
    obj.data.materials.append(mat)
    mat.use_nodes = True
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("no_output", names)

    def test_short_code_no_output_warning_skipped(self):
        """Short code without print is NOT flagged (too aggressive)."""
        code = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("no_output", names)


    def test_wrong_lamps_api(self):
        """bpy.data.lamps is caught."""
        code = """
import bpy
lamp = bpy.data.lamps.new("Sun", 'SUN')
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_lamps_api", names)

    def test_correct_lights_not_flagged(self):
        """bpy.data.lights is NOT flagged (it's correct)."""
        code = """
import bpy
light = bpy.data.lights.new("Sun", 'SUN')
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("wrong_lamps_api", names)

    def test_wrong_eevee_name(self):
        """render.engine = 'EEVEE' is caught."""
        code = """
import bpy
bpy.context.scene.render.engine = "EEVEE"
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_eevee_name", names)

    def test_correct_eevee_name_not_flagged(self):
        """render.engine = 'BLENDER_EEVEE' is NOT flagged."""
        code = """
import bpy
bpy.context.scene.render.engine = "BLENDER_EEVEE"
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("wrong_eevee_name", names)

    def test_wrong_eevee_access(self):
        """scene.render.eevee is caught."""
        code = """
import bpy
bpy.context.scene.render.eevee.use_ssr = True
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_eevee_access", names)


if __name__ == "__main__":
    unittest.main()
