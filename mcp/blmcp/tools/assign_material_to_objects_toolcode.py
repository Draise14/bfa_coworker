# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for assigning a material to one or more objects.
"""

__all__ = (
    "Result",
    "Params",
    "main",
)

from typing import Any, NamedTuple


class Result(NamedTuple):
    status: str
    message: str
    material_name: str
    assigned_to: list[str]


class Params(NamedTuple):
    material_name: str
    object_names: tuple[str, ...] = ()
    slot_index: int = 0


def main(params: Params) -> Result:
    """Assign an existing material to one or more objects.

    If object_names is empty, assigns to the active object.
    The material must already exist in the scene (load it first with
    load_asset_in_context or create it with setup_pbr_material).
    """
    material_name = params.material_name
    object_names = list(params.object_names)
    slot_index = params.slot_index

    import bpy  # pylint: disable=import-error

    if object_names is None:
        object_names = []

    # Find the material.
    mat = bpy.data.materials.get(material_name)
    if mat is None:
        return Result(
            status="error",
            message="Material '{:s}' not found in scene. Load it first.".format(material_name),
            material_name=material_name,
            assigned_to=[],
        )

    # If no object names specified, use active object.
    if not object_names:
        obj = bpy.context.active_object
        if obj is None:
            return Result(
                status="error",
                message="No active object and no object_names specified",
                material_name=material_name,
                assigned_to=[],
            )
        object_names = [obj.name]

    assigned = []
    errors = []

    for name in object_names:
        obj = bpy.data.objects.get(name)
        if obj is None:
            errors.append("Object '{:s}' not found".format(name))
            continue
        if obj.type not in ('MESH', 'CURVE', 'SURFACE', 'FONT', 'META'):
            errors.append("Object '{:s}' is type '{:s}', cannot assign material".format(name, obj.type))
            continue
        if obj.data is None:
            errors.append("Object '{:s}' has no data".format(name))
            continue

        # Ensure material slot exists at the index.
        while len(obj.data.materials) <= slot_index:
            obj.data.materials.append(None)
        obj.data.materials[slot_index] = mat
        assigned.append(obj.name)

    if errors:
        if assigned:
            return Result(
                status="partial",
                message="Assigned to {:d}/{:d} objects. Errors: {:s}".format(
                    len(assigned), len(object_names), "; ".join(errors)),
                material_name=material_name,
                assigned_to=assigned,
            )
        return Result(
            status="error",
            message="; ".join(errors),
            material_name=material_name,
            assigned_to=[],
        )

    return Result(
        status="ok",
        message="Material '{:s}' assigned to {:d} object(s)".format(material_name, len(assigned)),
        material_name=material_name,
        assigned_to=assigned,
    )
