def _autofix_code(raw_code):
    """Silently fix common LLM mistakes in raw_code."""
    import re as _re
    fixes = []
    code = raw_code

    def _fix(pat, repl, desc):
        nonlocal code
        new = _re.sub(pat, repl, code)
        if new != code:
            fixes.append(desc)
            code = new

    # Rename patterns -- safe, no side effects.
    _fix(r"bpy\.data\.lamps", "bpy.data.lights", "Auto-fix: lamps -> lights")
    _fix(r'"EEVEE"', '"BLENDER_EEVEE"', "Auto-fix: EEVEE -> BLENDER_EEVEE")
    _fix(r"render\.eevee\.", "scene.eevee.", "Auto-fix: render.eevee -> scene.eevee")
    _fix(r"\.use_auto_smooth", ".auto_smooth_angle", "Auto-fix: auto_smooth")
    _fix(r"action\.fcurves", "keyframe_insert", "Auto-fix: fcurves")
    _fix(r"ShaderNodeEnvironment", "ShaderNodeTexEnvironment", "Auto-fix: ShaderNodeEnvironment")
    _fix(r"ShaderNodeWorldOutput", "ShaderNodeOutputWorld", "Auto-fix: ShaderNodeWorldOutput")
    _fix(r"\["Use Nodes"\]", ".use_nodes = True", "Auto-fix: Use Nodes")
    _fix(r"\.subdivisions\s*=", ".levels =", "Auto-fix: subdivisions -> levels")
    _fix(r"\.(base_color|base_color_input)\s*=", ".inputs['Base Color'].default_value =", "Auto-fix: base_color")
    _fix(r"update_edit_mesh\(([^)]+),\s*[^)]+\)", r"update_edit_mesh()", "Auto-fix: update_edit_mesh")
    _fix(r"\.data\.material_slots", ".material_slots", "Auto-fix: data.material_slots")
    return code, fixes

