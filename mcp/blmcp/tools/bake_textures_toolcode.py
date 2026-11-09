# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for bake_textures: a Cycles bake as a background job (Tools 6, #309).

The entry point the Bake wizard (Tier 5) builds on.  It bakes into each
material's *active Image Texture node* (Blender's own workflow), checks that
up front so the model gets a fix it can act on (a bake that fails inside its
job only says so in the Info editor), then starts ``object.bake`` with
``INVOKE_DEFAULT`` and returns a job id for ``get_job_status``.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    bake_type: str
    object_names: Any
    margin: int


class Result(NamedTuple):
    status: str
    message: str
    job_id: str | None = None
    objects: Any = None
    images: Any = None


# @include_begin: _coworker_api_shared.py
# @include_end


# ``object.bake`` pass types (rna_enum_bake_pass_type_items).
_BAKE_TYPES = ("COMBINED", "AO", "SHADOW", "POSITION", "NORMAL", "UV", "ROUGHNESS",
               "EMIT", "ENVIRONMENT", "DIFFUSE", "GLOSSY", "TRANSMISSION")


def _target_images(ob: Any) -> tuple[list[Any], str]:
    """The images *ob* bakes into, or ``([], why not)``."""
    if ob.type != 'MESH':
        return [], "'{:s}' is a {:s}, not a mesh; bake works on mesh objects.".format(ob.name, ob.type.lower())
    if not ob.data.uv_layers:
        return [], "'{:s}' has no UV map. Unwrap it first (e.g. bpy.ops.uv.smart_project()).".format(ob.name)
    if not any(slot.material for slot in ob.material_slots):
        return [], ("'{:s}' has no material. Add one with an Image Texture node holding a new image, "
                    "make that node active, then bake.".format(ob.name))
    images = []
    for slot in ob.material_slots:
        mat = slot.material
        if mat is None:
            continue
        nodes = mat.node_tree.nodes if mat.node_tree else None
        node = nodes.active if nodes else None
        if node is None or node.bl_idname != 'ShaderNodeTexImage' or node.image is None:
            return [], ("Material '{:s}' on '{:s}' has no active Image Texture node with an image to bake "
                        "into. Add an Image Texture node with a new image (e.g. bpy.data.images.new(name, "
                        "1024, 1024)), set mat.node_tree.nodes.active to it, then bake.".format(mat.name, ob.name))
        if node.image not in images:
            images.append(node.image)
    return images, ""


def main(params: Params) -> Any:
    import bpy  # pylint: disable=import-error,no-name-in-module
    context = bpy.context
    scene = context.scene
    bake_type = str(params.bake_type or "").upper()
    if bake_type not in _BAKE_TYPES:
        return Result(status="error", message="Unknown bake_type '{:s}'. Use one of: {:s}.".format(
            str(params.bake_type), ", ".join(_BAKE_TYPES)))

    names = [str(n) for n in (params.object_names or []) if str(n).strip()]
    if names:
        missing = [n for n in names if bpy.data.objects.get(n) is None]
        if missing:
            return Result(status="error", message="No object named {:s}. Check the names with "
                          "get_objects_summary.".format(", ".join(repr(n) for n in missing)))
        objects = [bpy.data.objects[n] for n in names]
    else:
        objects = [ob for ob in context.selected_objects if ob.type == 'MESH']
        if not objects:
            return Result(status="error", message="Nothing to bake: pass object_names (mesh objects).")

    images: list[Any] = []
    for ob in objects:
        found, why = _target_images(ob)
        if why:
            return Result(status="error", message=why)
        images += [img for img in found if img not in images]

    if bpy.app.is_job_running('OBJECT_BAKE') or bpy.app.is_job_running('RENDER'):
        return Result(status="error", message=(
            "Blender is already baking or rendering. Wait for it (get_job_status, or the job bar "
            "at the bottom), then bake again."))

    # Bake needs Cycles; switch for the bake and switch back when it ends.
    orig_engine = scene.render.engine
    if orig_engine != 'CYCLES':
        scene.render.engine = 'CYCLES'

    def restore() -> None:
        if scene.render.engine != orig_engine:
            scene.render.engine = orig_engine

    bake_args: dict[str, Any] = {"type": bake_type, "target": 'IMAGE_TEXTURES', "save_mode": 'INTERNAL'}
    if params.margin is not None and int(params.margin) >= 0:
        bake_args["margin"] = int(params.margin)
    use_job = not bpy.app.background
    wm = context.window_manager
    window = context.window or (wm.windows[0] if wm.windows else None)
    override: dict[str, Any] = {"active_object": objects[0], "object": objects[0],
                                "selected_objects": objects, "selected_editable_objects": objects}
    if window is not None:
        override.update(window=window, screen=window.screen)
    # A bake that fails inside its job only reports to the Info editor: if no image
    # was dirty before and none is after, nothing was baked.
    all_clean_before = not any(img.is_dirty for img in images)
    try:
        with context.temp_override(**override):
            ret = bpy.ops.object.bake('INVOKE_DEFAULT' if use_job else 'EXEC_DEFAULT', **bake_args)
    except (RuntimeError, TypeError) as ex:
        restore()
        return Result(status="error", message=str(ex).strip())
    if 'CANCELLED' in ret:
        restore()
        return Result(status="error", message="Blender didn't start the bake (another bake or render may be running).")

    image_names = [img.name for img in images]
    object_names = [ob.name for ob in objects]
    done_message = ("Baked {:s} into {:s}. The images are in memory: save them (image.save() after setting "
                    "filepath_raw, or pack them) to keep them. Undo / Revert can't bring back the old pixels.".format(
                        bake_type, ", ".join(image_names)))
    if not use_job:
        restore()
        return Result(status="ok", message=done_message, objects=object_names, images=image_names)

    def check_is_finished() -> dict[str, object] | None:
        if bpy.app.is_job_running('OBJECT_BAKE'):
            return None
        restore()
        if all_clean_before and not any(img.is_dirty for img in images):
            return {"status": "error", "objects": object_names, "images": image_names, "message": (
                "The bake ended but no image changed; Blender's Info editor has the reason.")}
        return {"status": "ok", "objects": object_names, "images": image_names, "message": done_message}

    label = "Bake {:s}: {:s}".format(bake_type.lower(), ", ".join(object_names)[:60])
    job_id = _coworker_api().start_job('OBJECT_BAKE', label, check_is_finished, restore)
    return Result(status="ok", job_id=job_id, objects=object_names, images=image_names, message=(
        "Baking in the background. Call get_job_status(job_id) to wait for it."))
