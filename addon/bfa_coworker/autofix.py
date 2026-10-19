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

    # Rename patterns -- safe, no side effects.  Only 1:1 renames belong here:
    # rewrites that change meaning (action.fcurves -> keyframe_insert,
    # use_auto_smooth -> auto_smooth_angle, ...) turned code into a NameError
    # or SyntaxError (#101); preflight explains those cases instead.
    _fix(r"bpy\.data\.lamps\b", "bpy.data.lights", "Auto-fix: lamps -> lights")
    _fix(r'"EEVEE"', '"BLENDER_EEVEE"', "Auto-fix: EEVEE -> BLENDER_EEVEE")
    _fix(r"render\.eevee\.", "scene.eevee.", "Auto-fix: render.eevee -> scene.eevee")
    _fix(r"ShaderNodeEnvironment\b", "ShaderNodeTexEnvironment", "Auto-fix: ShaderNodeEnvironment")
    _fix(r"ShaderNodeWorldOutput\b", "ShaderNodeOutputWorld", "Auto-fix: ShaderNodeWorldOutput")
    _fix(r"\.subdivisions\s*=", ".levels =", "Auto-fix: subdivisions -> levels")
    _fix(r"\.(base_color|base_color_input)\s*=", ".inputs['Base Color'].default_value =", "Auto-fix: base_color")
    _fix(r"update_edit_mesh\(([^)]+),\s*[^)]+\)", r"update_edit_mesh(\1)", "Auto-fix: update_edit_mesh")
    _fix(r"\.data\.material_slots\b", ".material_slots", "Auto-fix: data.material_slots")
    return code, fixes
