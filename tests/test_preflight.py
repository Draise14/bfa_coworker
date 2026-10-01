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

    # Create a module with just re available.
    mod = types.ModuleType("_preflight_test")
    mod.__dict__["re"] = __import__("re")

    # ``_preflight_check`` calls ``_imports_module``, so extract that first.
    helper_start = source.find("\ndef _imports_module(")
    if helper_start < 0:
        raise ImportError("_imports_module not found in source")
    helper_start += 1
    helper_end = len(source)
    for marker in ["\ndef _preflight_check(", "\nclass ", "\n# ---"]:
        idx = source.find(marker, helper_start + 100)
        if idx >= 0 and idx < helper_end:
            helper_end = idx
    exec(source[helper_start:helper_end], mod.__dict__)

    # Extract just the _preflight_check function and its imports.
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

    exec(func_source, mod.__dict__)
    return mod._preflight_check


_preflight_check = _load_preflight()


def _load_code_uses_bpy():
    """Load _code_uses_bpy + its regex from mcp_to_blender_server.py."""
    src_path = os.path.join(_ADDON_DIR, "bfa_coworker", "mcp_to_blender_server.py")
    with open(src_path, "r", encoding="utf-8") as f:
        source = f.read()
    mod = types.ModuleType("_bpy_guard_test")
    mod.__dict__["re"] = __import__("re")
    const_start = source.find("\n_BPY_MODULE_REF_RE = ")
    func_start = source.find("\ndef _code_uses_bpy(")
    if const_start < 0 or func_start < 0:
        raise ImportError("_code_uses_bpy not found in source")
    func_end = len(source)
    for marker in ["\ndef _", "\nclass ", "\n# ---"]:
        idx = source.find(marker, func_start + 100)
        if idx >= 0 and idx < func_end:
            func_end = idx
    # Slice covers the regex assignment + the function definition.
    exec(source[const_start + 1:func_end], mod.__dict__)
    return mod._code_uses_bpy


_code_uses_bpy = _load_code_uses_bpy()


def _load_autofix():
    """Load _autofix_code from autofix.py (standalone, no bpy)."""
    import importlib.util
    import types
    src_path = os.path.join(_ADDON_DIR, "bfa_coworker", "autofix.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        source = fh.read()
    mod = types.ModuleType("autofix_under_test")
    exec(compile(source, src_path, "exec"), mod.__dict__)
    return mod._autofix_code


_autofix_code = _load_autofix()


class TestPreflightCheck(unittest.TestCase):
    """Tests for _preflight_check()."""

    def test_clean_code_passes(self):
        """Code with no issues passes the preflight check."""
        code = """
import bpy
bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.view_layer.objects.active
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

    def test_gn_socket_membership_caught(self):
        """`x in modifier` (removed in 5.2) is caught."""
        code = """
import bpy
mod = bpy.context.object.modifiers['GeometryNodes']
if 'Socket_3' in mod:
    print('has socket')
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("gn_socket_membership", names)

    def test_plain_membership_not_caught(self):
        """A normal `x in some_list` must NOT be flagged."""
        code = """
import bpy
names = [o.name for o in bpy.data.objects]
if 'Ground' in names:
    print('found')
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("gn_socket_membership", names)

    def test_manual_channelbag_caught(self):
        """Manual channelbag/slot construction (crashes) is caught."""
        code = """
import bpy
action = bpy.data.actions.new('A')
cb = action.layers[0].strips[0].channelbag(action.slots.new())
print(cb)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("manual_channelbag", names)

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


    def test_wrong_bsdf_input_name(self):
        """Wrong Principled BSDF input name is caught."""
        code = """
import bpy
mat = bpy.data.materials.new("Test")
mat.use_nodes = True
bsdf = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
bsdf.inputs['Subsurface Color'].default_value = (0.8, 0.2, 0.2, 1.0)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_bsdf_input", names)

    def test_correct_bsdf_input_not_flagged(self):
        """Correct Principled BSDF input name is NOT flagged."""
        code = """
import bpy
mat = bpy.data.materials.new("Test")
mat.use_nodes = True
bsdf = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
bsdf.inputs['Base Color'].default_value = (0.8, 0.2, 0.2, 1.0)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("wrong_bsdf_input", names)

    def test_wrong_collection_active(self):
        """bpy.data.lights.active is caught."""
        code = """
import bpy
light = bpy.data.lights.active
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_collection_active", names)


    def test_wrong_mode_set_pose(self):
        """mode_set(mode='POSE') without armature context is flagged."""
        code = """
import bpy
bpy.ops.object.mode_set(mode='POSE')
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_mode_set", names)

    def test_hallucinated_module(self):
        """Importing mcp_toolkit is caught."""
        code = """
import bpy
import mcp_toolkit
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("hallucinated_module", names)

    def test_wrong_mode_enum(self):
        """mode_set(mode='INVALID') is caught."""
        code = """
import bpy
bpy.ops.object.mode_set(mode='INVALID')
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_mode_enum", names)

    def test_valid_mode_enum_not_flagged(self):
        """mode_set(mode='EDIT') is NOT flagged."""
        code = """
import bpy
bpy.ops.object.mode_set(mode='EDIT')
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("wrong_mode_enum", names)


    def test_wrong_material_slots_on_mesh(self):
        """mesh.data.material_slots is caught (should be obj.material_slots)."""
        code = """
import bpy
mesh = bpy.context.active_object.data
slots = mesh.data.material_slots
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_material_hierarchy", names)

    def test_correct_material_slots_not_flagged(self):
        """obj.material_slots is NOT flagged (it's correct)."""
        code = """
import bpy
obj = bpy.context.active_object
slots = obj.material_slots
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("wrong_material_hierarchy", names)


    def test_wrong_node_type_environment(self):
        """ShaderNodeEnvironment is caught."""
        code = """
import bpy
world = bpy.data.worlds.new("TestWorld")
world.use_nodes = True
nodes = world.node_tree.nodes
env = nodes.new(type="ShaderNodeEnvironment")
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_node_type", names)

    def test_correct_node_type_not_flagged(self):
        """ShaderNodeTexEnvironment is NOT flagged."""
        code = """
import bpy
world = bpy.data.worlds.new("TestWorld")
world.use_nodes = True
nodes = world.node_tree.nodes
env = nodes.new(type="ShaderNodeTexEnvironment")
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("wrong_node_type", names)


    def test_mcp_tool_as_function(self):
        """Calling setup_pbr_material() inside code is caught."""
        code = """
import bpy
setup_pbr_material("TestMat", "0.8, 0.2, 0.2, 1.0")
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("mcp_tool_as_function", names)

    def test_wrong_world_property(self):
        """world["Use Nodes"] is caught."""
        code = """
import bpy
world = bpy.data.worlds.new("Test")
world["Use Nodes"] = True
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("wrong_world_property", names)

    def test_prim_transform_kwarg(self):
        """rotation_euler on primitive add is caught."""
        code = """
import bpy
bpy.ops.mesh.primitive_cube_add(rotation_euler=(1, 0, 0))
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("prim_transform_kwarg", names)

    def test_prim_location_kwarg_ok(self):
        """location/rotation on primitive add is accepted in 5.3 (not flagged)."""
        code = """
import bpy
bpy.ops.mesh.primitive_uv_sphere_add(location=(1, 2, 3), rotation=(0.1, 0.2, 0.3))
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("prim_transform_kwarg", names)

    def test_prim_no_transform_ok(self):
        """Primitive add without transform kwargs is NOT flagged."""
        code = """
import bpy
bpy.ops.mesh.primitive_cube_add(size=2.0)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("prim_transform_kwarg", names)

    def test_context_active_object_caught(self):
        """bpy.context.active_object is flagged (unavailable in bridge thread)."""
        code = """
import bpy
bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.active_object
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("context_active_object_thread", names)

    def test_view_layer_active_not_caught(self):
        """bpy.context.view_layer.objects.active is NOT flagged."""
        code = """
import bpy
bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.view_layer.objects.active
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("context_active_object_thread", names)


    # ── Tests for checks 24-27: bmesh, vector, update_edit_mesh ──────────

    def test_missing_bmesh_import(self):
        """Code using bmesh without import is caught."""
        code = """
import bpy
obj = bpy.context.active_object
bm = bmesh.from_edit_mesh(obj.data)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("missing_bmesh_import", names)

    def test_bmesh_imported_ok(self):
        """Code with bmesh import is NOT flagged."""
        code = """
import bpy
import bmesh
obj = bpy.context.active_object
bm = bmesh.from_edit_mesh(obj.data)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("missing_bmesh_import", names)
    def test_bmesh_imported_in_comma_list_ok(self):
        """``import bpy, bmesh, math`` must count as importing bmesh.

        A plain substring test for 'import bmesh' misses this form, which
        made the check fire on valid code and cost the model a wasted retry.
        """
        code = """
import bpy, bmesh, math
obj = bpy.context.active_object
bm = bmesh.from_edit_mesh(obj.data)
"""
        issues = _preflight_check(code)
        names = [i[0] for i in issues]
        self.assertNotIn("missing_bmesh_import", names)

    def test_bmesh_imported_with_alias_ok(self):
        """``import bmesh as bm`` must count as importing bmesh."""
        code = """
import bpy
import bmesh as bm
obj = bpy.context.active_object
mesh = bmesh.from_edit_mesh(obj.data)
"""
        issues = _preflight_check(code)
        names = [i[0] for i in issues]
        self.assertNotIn("missing_bmesh_import", names)

    def test_bmesh_from_import_ok(self):
        """``from bmesh import ops`` must count as importing bmesh."""
        code = """
import bpy
from bmesh import ops
obj = bpy.context.active_object
"""
        issues = _preflight_check(code)
        names = [i[0] for i in issues]
        self.assertNotIn("missing_bmesh_import", names)

    def test_bpy_imported_in_comma_list_ok(self):
        """``import bpy, bmesh`` must count as importing bpy."""
        code = """
import bpy, bmesh
bpy.ops.mesh.primitive_cube_add()
"""
        issues = _preflight_check(code)
        names = [i[0] for i in issues]
        self.assertNotIn("missing_bpy", names)

    def test_similar_module_name_does_not_count(self):
        """``import bmesh_utils`` must NOT satisfy the bmesh import check."""
        code = """
import bpy
import bmesh_utils
obj = bpy.context.active_object
bm = bmesh.from_edit_mesh(obj.data)
"""
        issues = _preflight_check(code)
        names = [i[0] for i in issues]
        self.assertIn("missing_bmesh_import", names)
    def test_bmesh_editmode_mismatch(self):
        """from_edit_mesh without mode_set is caught."""
        code = """
import bpy
import bmesh
obj = bpy.context.active_object
bm = bmesh.from_edit_mesh(obj.data)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("bmesh_editmode_mismatch", names)

    def test_bmesh_editmode_ok(self):
        """from_edit_mesh with mode_set is NOT flagged."""
        code = """
import bpy
import bmesh
bpy.ops.object.mode_set(mode='EDIT')
obj = bpy.context.active_object
bm = bmesh.from_edit_mesh(obj.data)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("bmesh_editmode_mismatch", names)

    def test_vector_arithmetic(self):
        """vert.co += offset + float is caught."""
        code = """
import bmesh
for vert in bm.verts:
    vert.co += offset + random.uniform(-0.1, 0.1)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("vector_arithmetic", names)

    def test_update_edit_mesh_extra_args(self):
        """update_edit_mesh with 2+ args is caught."""
        code = """
import bmesh
bm.update_edit_mesh(mesh, True, True)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("update_edit_mesh_args", names)

    def test_update_edit_mesh_one_arg_ok(self):
        """update_edit_mesh with 1 arg is NOT flagged."""
        code = """
import bmesh
bm.update_edit_mesh(mesh)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("update_edit_mesh_args", names)


    def test_primitive_loop_allowed(self):
        """Creating primitives in a loop is legitimate -- NOT flagged."""
        code = """
bpy.ops.object.select_all(action='SELECT')
for i in range(3):
    bpy.ops.mesh.primitive_torus_add(major_radius=1.0, minor_radius=0.3)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("no_existence_check", names)

    def test_data_new_loop_static_name_flagged(self):
        """objects.new in a loop with a static name and no guard IS flagged."""
        code = """
for i in range(5):
    bpy.data.objects.new("Cube", mesh)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertIn("no_existence_check", names)

    def test_data_new_loop_unique_name_allowed(self):
        """Unique per-iteration names are fine -- NOT flagged."""
        code = """
for i in range(5):
    bpy.data.objects.new(name=f"cube_{i}", object_data=mesh)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("no_existence_check", names)

    def test_data_new_loop_with_guard_allowed(self):
        """Existence check via get() is fine -- NOT flagged."""
        code = """
for i in range(5):
    if bpy.data.objects.get("Cube_" + str(i)) is None:
        bpy.data.objects.new("Cube_" + str(i), mesh)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("no_existence_check", names)

    def test_material_loop_not_flagged(self):
        """Material/appending loops unrelated to object creation -- NOT flagged."""
        code = """
for obj in bpy.data.objects:
    mat = bpy.data.materials.new(name="M")
    obj.data.materials.append(mat)
"""
        issues = _preflight_check(code)
        names = [name for name, _ in issues]
        self.assertNotIn("no_existence_check", names)

class TestAutofixBeforePreflight(unittest.TestCase):
    """Auto-fix runs before preflight, so corrected code passes.

    Mirrors the wiring in ``_execute_code``: ``_autofix_code`` is applied
    first, then ``_preflight_check`` sees the corrected source.
    """

    def test_subdivisions_fixed_then_passes(self):
        raw = """import bpy
obj = bpy.ops.mesh.primitive_cube_add()
mod = obj.modifiers.new("Subsurf", 'SUBSURF')
mod.subdivisions = 3
"""
        fixed, fixes = _autofix_code(raw)
        self.assertTrue(any("subdivisions -> levels" in f for f in fixes))
        issues = _preflight_check(fixed)
        names = [name for name, _ in issues]
        self.assertNotIn("wrong_subdiv_attr", names)

    def test_base_color_fixed_then_passes(self):
        raw = """import bpy
mat = bpy.data.materials.new("M")
mat.use_nodes = True
n = mat.node_tree.nodes["Principled BSDF"]
n.base_color = (1, 0, 0)
"""
        fixed, fixes = _autofix_code(raw)
        self.assertTrue(any("base_color" in f for f in fixes))
        issues = _preflight_check(fixed)
        names = [name for name, _ in issues]
        self.assertNotIn("wrong_base_color", names)

    def test_unfixable_issue_still_flagged(self):
        """Code autofix cannot repair is still rejected by preflight."""
        # Preflight flags missing bpy import; autofix cannot repair that.
        raw = "bpy.ops.mesh.primitive_cube_add()"
        fixed, fixes = _autofix_code(raw)
        self.assertEqual(fixes, [])
        issues = _preflight_check(fixed)
        names = [name for name, _ in issues]
        self.assertIn("missing_bpy", names)


class TestOperatorContextPreflight(unittest.TestCase):
    """Scene-safety Phase 2: operator context preconditions."""

    def _names(self, code):
        return [name for name, _ in _preflight_check(code)]

    def test_join_without_selection_flagged(self):
        code = "import bpy\nbpy.ops.object.join()\n"
        self.assertIn("op_requires_selection", self._names(code))

    def test_join_with_explicit_selection_passes(self):
        code = (
            "import bpy\n"
            "objs = [bpy.data.objects.get('A'), bpy.data.objects.get('B')]\n"
            "for o in objs:\n    o.select_set(True)\n"
            "bpy.context.view_layer.objects.active = objs[0]\n"
            "bpy.ops.object.join()\n"
        )
        self.assertNotIn("op_requires_selection", self._names(code))

    def test_modifier_apply_without_active_flagged(self):
        code = "import bpy\nbpy.ops.object.modifier_apply(modifier='S')\n"
        self.assertIn("op_requires_active_object", self._names(code))

    def test_modifier_apply_with_active_passes(self):
        code = (
            "import bpy\n"
            "obj = bpy.data.objects.get('Cube')\n"
            "bpy.context.view_layer.objects.active = obj\n"
            "bpy.ops.object.modifier_apply(modifier='S')\n"
        )
        self.assertNotIn("op_requires_active_object", self._names(code))

    def test_unrelated_get_does_not_suppress_active_check(self):
        # Fetching some object with .get() elsewhere must not hide the fact
        # that no active object is set before the operator.
        code = (
            "import bpy\n"
            "x = bpy.data.objects.get('Other')\n"
            "bpy.ops.object.modifier_apply(modifier='S')\n"
        )
        self.assertIn("op_requires_active_object", self._names(code))


class TestUnguardedIndexPreflight(unittest.TestCase):
    """Scene-safety Phase 3: unguarded literal indexing of collections."""

    def _names(self, code):
        return [name for name, _ in _preflight_check(code)]

    def test_selected_objects_index_flagged(self):
        code = "import bpy\nobj = bpy.context.selected_objects[0]\nprint(obj)\n"
        self.assertIn("unguarded_list_index", self._names(code))

    def test_guarded_index_passes(self):
        code = (
            "import bpy\n"
            "objs = bpy.context.selected_objects\n"
            "if len(objs):\n    print(objs[0])\n"
        )
        self.assertNotIn("unguarded_list_index", self._names(code))

    def test_non_collection_index_not_flagged(self):
        # A plain Python list index must not trip the collection-root check.
        code = "import bpy\nverts = [1, 2, 3]\nprint(verts[0])\n"
        self.assertNotIn("unguarded_list_index", self._names(code))

    def test_unrelated_if_does_not_suppress_index_check(self):
        # A bare `if` elsewhere must not defeat the check (the previous
        # broad suppression hid the exact selected_objects[0] class).
        code = (
            "import bpy\n"
            "if True:\n    print('hi')\n"
            "obj = bpy.context.selected_objects[0]\n"
        )
        self.assertIn("unguarded_list_index", self._names(code))

    def test_len_on_same_root_suppresses_index_check(self):
        code = (
            "import bpy\n"
            "if len(bpy.context.selected_objects) > 0:\n"
            "    obj = bpy.context.selected_objects[0]\n"
        )
        self.assertNotIn("unguarded_list_index", self._names(code))

    def test_data_collection_index_flagged(self):
        code = "import bpy\nmat = bpy.data.materials[0]\n"
        self.assertIn("unguarded_list_index", self._names(code))


class TestCodeUsesBpy(unittest.TestCase):
    """_code_uses_bpy gates main-thread (inline) execution.

    Code that touches Blender's C API must NOT run on the MCP worker thread:
    that races Blender's global Python context counter and spams the
    "Python context internal state bug" message.  This predicate decides it.
    """

    def test_detects_bpy_api_use(self):
        self.assertTrue(_code_uses_bpy("import bpy\nbpy.data.objects.new('x', None)"))

    def test_detects_import_forms(self):
        self.assertTrue(_code_uses_bpy("from bpy import data"))
        self.assertTrue(_code_uses_bpy("import bmesh"))

    def test_detects_other_c_backed_modules(self):
        self.assertTrue(_code_uses_bpy("v = mathutils.Vector((1, 2, 3))"))

    def test_pure_python_is_false(self):
        self.assertFalse(_code_uses_bpy("total = sum(range(10))\nprint(total)"))

    def test_no_partial_word_false_positive(self):
        # 'bpy' embedded in a longer identifier must not match (word bounds).
        self.assertFalse(_code_uses_bpy("label = 'unbpyable'"))


if __name__ == "__main__":
    unittest.main()
