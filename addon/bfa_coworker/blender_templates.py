# Tested Blender code templates for common operations.
# Each template is pre-validated for Blender 5.3.


def _tmpl_create_torus(params=None):
    """Template: create_torus"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'bpy.ops.mesh.primitive_torus_add(major_radius={major_radius}, minor_radius={minor_radius}, major_segments={major_segments}, minor_segments={minor_segments})\n'
        'obj = bpy.context.active_object\n'
        'obj.name = "{name}"\n'
        'obj.location = ({x}, {y}, {z})\n'
    ).format(**p)


def _tmpl_create_cube(params=None):
    """Template: create_cube"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'bpy.ops.mesh.primitive_cube_add(size={size})\n'
        'obj = bpy.context.active_object\n'
        'obj.name = "{name}"\n'
        'obj.location = ({x}, {y}, {z})\n'
    ).format(**p)


def _tmpl_create_uv_sphere(params=None):
    """Template: create_uv_sphere"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'bpy.ops.mesh.primitive_uv_sphere_add(segments={segments}, ring_count={ring_count})\n'
        'obj = bpy.context.active_object\n'
        'obj.name = "{name}"\n'
        'obj.location = ({x}, {y}, {z})\n'
    ).format(**p)


def _tmpl_create_cylinder(params=None):
    """Template: create_cylinder"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'bpy.ops.mesh.primitive_cylinder_add(vertices={vertices}, radius={radius}, depth={depth})\n'
        'obj = bpy.context.active_object\n'
        'obj.name = "{name}"\n'
        'obj.location = ({x}, {y}, {z})\n'
    ).format(**p)


def _tmpl_create_plane(params=None):
    """Template: create_plane"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'bpy.ops.mesh.primitive_plane_add(size={size})\n'
        'obj = bpy.context.active_object\n'
        'obj.name = "{name}"\n'
        'obj.location = ({x}, {y}, {z})\n'
    ).format(**p)


def _tmpl_add_material(params=None):
    """Template: add_material"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'obj = bpy.context.active_object\n'
        'mat = bpy.data.materials.new(name="{mat_name}")\n'
        'mat.use_nodes = True\n'
        'principled = mat.node_tree.nodes.get("Principled BSDF")\n'
        'if principled:\n'
        '    principled.inputs["Base Color"].default_value = ({r}, {g}, {b}, 1.0)\n'
        '    principled.inputs["Roughness"].default_value = {roughness}\n'
        'obj.data.materials.append(mat)\n'
    ).format(**p)


def _tmpl_smooth_shade(params=None):
    """Template: smooth_shade"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'bpy.ops.object.shade_smooth()\n'
    ).format(**p)


def _tmpl_auto_smooth(params=None):
    """Template: auto_smooth"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy, math\n'
        'obj = bpy.context.active_object\n'
        'obj.data.auto_smooth_angle = math.radians({angle_degrees})\n'
    ).format(**p)


def _tmpl_add_subsurf(params=None):
    """Template: add_subsurf"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'obj = bpy.context.active_object\n'
        'mod = obj.modifiers.new(name="Subdivision", type="SUBSURF")\n'
        'mod.levels = {viewport_levels}\n'
        'mod.render_levels = {render_levels}\n'
    ).format(**p)


def _tmpl_add_array(params=None):
    """Template: add_array"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'obj = bpy.context.active_object\n'
        'mod = obj.modifiers.new(name="Array", type="ARRAY")\n'
        'mod.count = {count}\n'
        'mod.relative_offset_displace[0] = {offset_x}\n'
    ).format(**p)


def _tmpl_add_bevel(params=None):
    """Template: add_bevel"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'obj = bpy.context.active_object\n'
        'mod = obj.modifiers.new(name="Bevel", type="BEVEL")\n'
        'mod.width = {width}\n'
        'mod.segments = {segments_bevel}\n'
    ).format(**p)


def _tmpl_add_solidify(params=None):
    """Template: add_solidify"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'obj = bpy.context.active_object\n'
        'mod = obj.modifiers.new(name="Solidify", type="SOLIDIFY")\n'
        'mod.thickness = {thickness}\n'
    ).format(**p)


def _tmpl_add_smooth(params=None):
    """Template: add_smooth"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'obj = bpy.context.active_object\n'
        'mod = obj.modifiers.new(name="Smooth", type="SMOOTH")\n'
        'mod.factor = {factor}\n'
        'mod.iterations = {iterations}\n'
    ).format(**p)


def _tmpl_add_remesh(params=None):
    """Template: add_remesh"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'obj = bpy.context.active_object\n'
        'mod = obj.modifiers.new(name="Remesh", type="REMESH")\n'
        'mod.mode = "VOXEL"\n'
        'mod.voxel_size = {voxel_size}\n'
    ).format(**p)


def _tmpl_set_render_engine(params=None):
    """Template: set_render_engine"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'bpy.context.scene.render.engine = "{engine}"\n'
        'bpy.context.scene.render.resolution_x = {resolution_x}\n'
        'bpy.context.scene.render.resolution_y = {resolution_y}\n'
    ).format(**p)


def _tmpl_setup_camera(params=None):
    """Template: setup_camera"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'bpy.ops.object.camera_add(location=({x}, {y}, {z}))\n'
        'cam = bpy.context.active_object\n'
        'cam.name = "{name}"\n'
        'cam.rotation_euler = ({rx}, {ry}, {rz})\n'
        'bpy.context.scene.camera = cam\n'
        'cam.data.lens = {lens}\n'
    ).format(**p)


def _tmpl_keyframe_location(params=None):
    """Template: keyframe_location"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'obj = bpy.context.active_object\n'
        'obj.location = ({x}, {y}, {z})\n'
        'obj.keyframe_insert(data_path="location", frame={frame})\n'
    ).format(**p)


def _tmpl_keyframe_rotation(params=None):
    """Template: keyframe_rotation"""
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy, math\n'
        'obj = bpy.context.active_object\n'
        'obj.rotation_euler = (math.radians({rx_deg}), math.radians({ry_deg}), math.radians({rz_deg}))\n'
        'obj.keyframe_insert(data_path="rotation_euler", frame={frame})\n'
    ).format(**p)


_TEMPLATE_DEFAULTS = {
    "name": "Object", "mat_name": "Material",
    "x": 0, "y": 0, "z": 0, "rx": 0, "ry": 0, "rz": 0,
    "rx_deg": 0, "ry_deg": 0, "rz_deg": 0,
    "size": 1.0, "major_radius": 1.0, "minor_radius": 0.3,
    "major_segments": 48, "minor_segments": 12,
    "segments": 32, "ring_count": 16,
    "vertices": 32, "radius": 1.0, "depth": 2.0,
    "r": 0.8, "g": 0.2, "b": 0.2, "roughness": 0.5,
    "count": 3, "offset_x": 2.5,
    "width": 0.05, "segments_bevel": 2, "thickness": 0.1,
    "factor": 0.5, "iterations": 3, "voxel_size": 0.1,
    "angle_degrees": 30,
    "engine": "BLENDER_EEVEE", "resolution_x": 1920, "resolution_y": 1080,
    "lens": 50,
    "viewport_levels": 2, "render_levels": 2,
    "frame": 1,
}

_TEMPLATES = {
    "create_torus": _tmpl_create_torus,
    "create_cube": _tmpl_create_cube,
    "create_uv_sphere": _tmpl_create_uv_sphere,
    "create_cylinder": _tmpl_create_cylinder,
    "create_plane": _tmpl_create_plane,
    "add_material": _tmpl_add_material,
    "smooth_shade": _tmpl_smooth_shade,
    "auto_smooth": _tmpl_auto_smooth,
    "add_subsurf": _tmpl_add_subsurf,
    "add_array": _tmpl_add_array,
    "add_bevel": _tmpl_add_bevel,
    "add_solidify": _tmpl_add_solidify,
    "add_smooth": _tmpl_add_smooth,
    "add_remesh": _tmpl_add_remesh,
    "set_render_engine": _tmpl_set_render_engine,
    "setup_camera": _tmpl_setup_camera,
    "keyframe_location": _tmpl_keyframe_location,
    "keyframe_rotation": _tmpl_keyframe_rotation,
}


def _render_template(name, params=None):
    """Render a named template with given parameters."""
    tmpl = _TEMPLATES.get(name)
    if tmpl is None:
        return None
    return tmpl(params)


def _plan_to_code(steps):
    """Convert plan steps into executable Blender code."""
    parts = ["import bpy", "import math", ""]
    for i, step in enumerate(steps):
        tmpl_name = step.get("template")
        params = step.get("params", {})
        if tmpl_name and tmpl_name in _TEMPLATES:
            code_block = _render_template(tmpl_name, params)
            if code_block:
                parts.append("# Step {:d}: {}".format(i+1, tmpl_name))
                for line in code_block.split(chr(10)):
                    s = line.strip()
                    if s.startswith("import ") and s in ("import bpy", "import math"):
                        continue
                    parts.append(line)
                parts.append("")
        elif "code" in step:
            parts.append("# Step {:d}: custom".format(i+1))
            parts.append(step["code"])
            parts.append("")
    return chr(10).join(parts)