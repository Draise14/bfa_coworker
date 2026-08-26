# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for loading an asset from the asset browser into the current context.
"""

__all__ = (
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Result(NamedTuple):
    status: str
    message: str
    asset_name: str
    asset_type: str
    loaded_into: str


def main(library_name: str, asset_name: str, asset_type: str = "") -> Result:
    """Load an asset from the asset browser into the current context."""
    import bpy  # pylint: disable=import-error
    import os

    # Find the library path.
    lib_path = None
    for lib in bpy.context.preferences.filepaths.asset_libraries:
        if lib.name == library_name:
            lib_path = str(lib.path) if lib.path else None
            break

    if not lib_path or not os.path.isdir(lib_path):
        return Result(
            status="error",
            message="Library '{:s}' not found or path invalid".format(library_name),
            asset_name=asset_name,
            asset_type=asset_type or "unknown",
            loaded_into="none",
        )

    # Find the blend file containing this asset.
    blend_path = None
    for root, _dirs, files in os.walk(lib_path):
        for f in files:
            if f.endswith(".blend"):
                candidate = os.path.join(root, f)
                try:
                    with bpy.data.libraries.load(candidate) as (data_from, _data_to):
                        all_names = (
                            list(data_from.materials)
                            + list(data_from.node_groups)
                            + list(data_from.objects)
                            + list(data_from.worlds)
                            + list(data_from.actions)
                            + list(data_from.collections)
                        )
                        if asset_name in all_names:
                            blend_path = candidate
                            break
                except Exception:
                    continue
        if blend_path:
            break

    if not blend_path:
        return Result(
            status="error",
            message="Asset '{:s}' not found in library '{:s}'".format(asset_name, library_name),
            asset_name=asset_name,
            asset_type=asset_type or "unknown",
            loaded_into="none",
        )

    # Auto-detect type if not provided.
    if not asset_type:
        try:
            with bpy.data.libraries.load(blend_path) as (data_from, _data_to):
                if asset_name in data_from.materials:
                    asset_type = "MATERIAL"
                elif asset_name in data_from.node_groups:
                    asset_type = "NODETREE"
                elif asset_name in data_from.collections:
                    asset_type = "COLLECTION"
                elif asset_name in data_from.objects:
                    asset_type = "OBJECT"
                elif asset_name in data_from.worlds:
                    asset_type = "WORLD"
                elif asset_name in data_from.actions:
                    asset_type = "ACTION"
                else:
                    asset_type = "UNKNOWN"
        except Exception:
            asset_type = "UNKNOWN"

    # Load based on type.
    try:
        if asset_type == "MATERIAL":
            # Load material and assign to active object.
            with bpy.data.libraries.load(blend_path) as (data_from, data_to):
                data_to.materials = [asset_name]
            mat = data_to.materials[0]
            if mat is None:
                return Result(
                    status="error",
                    message="Failed to load material '{:s}'".format(asset_name),
                    asset_name=asset_name,
                    asset_type=asset_type,
                    loaded_into="none",
                )
            obj = bpy.context.active_object
            if obj is None:
                # Create a cube if no active object.
                bpy.ops.mesh.primitive_cube_add()
                obj = bpy.context.active_object
                obj.name = "Asset_Target"
            if obj.data.materials:
                obj.data.materials[0] = mat
            else:
                obj.data.materials.append(mat)
            return Result(
                status="ok",
                message="Material '{:s}' loaded and assigned to '{:s}'".format(asset_name, obj.name),
                asset_name=asset_name,
                asset_type=asset_type,
                loaded_into="object:{:s}".format(obj.name),
            )

        elif asset_type == "NODETREE":
            # Load node group into active material's node tree.
            with bpy.data.libraries.load(blend_path) as (data_from, data_to):
                data_to.node_groups = [asset_name]
            ng = data_to.node_groups[0]
            if ng is None:
                return Result(
                    status="error",
                    message="Failed to load node group '{:s}'".format(asset_name),
                    asset_name=asset_name,
                    asset_type=asset_type,
                    loaded_into="none",
                )
            obj = bpy.context.active_object
            if obj and obj.active_material and obj.active_material.node_tree:
                node = obj.active_material.node_tree.nodes.new(type='ShaderNodeGroup')
                node.node_tree = ng
                return Result(
                    status="ok",
                    message="Node group '{:s}' loaded into material '{:s}'".format(
                        asset_name, obj.active_material.name),
                    asset_name=asset_name,
                    asset_type=asset_type,
                    loaded_into="material:{:s}".format(obj.active_material.name),
                )
            else:
                return Result(
                    status="ok",
                    message="Node group '{:s}' loaded (no active material to insert into)".format(asset_name),
                    asset_name=asset_name,
                    asset_type=asset_type,
                    loaded_into="data_only",
                )

        elif asset_type == "COLLECTION":
            # Append collection to scene.
            with bpy.data.libraries.load(blend_path) as (data_from, data_to):
                data_to.collections = [asset_name]
            col = data_to.collections[0]
            if col is None:
                return Result(
                    status="error",
                    message="Failed to load collection '{:s}'".format(asset_name),
                    asset_name=asset_name,
                    asset_type=asset_type,
                    loaded_into="none",
                )
            bpy.context.scene.collection.children.link(col)
            return Result(
                status="ok",
                message="Collection '{:s}' linked to scene".format(asset_name),
                asset_name=asset_name,
                asset_type=asset_type,
                loaded_into="scene",
            )

        elif asset_type == "OBJECT":
            # Append object to scene.
            with bpy.data.libraries.load(blend_path) as (data_from, data_to):
                data_to.objects = [asset_name]
            obj = data_to.objects[0]
            if obj is None:
                return Result(
                    status="error",
                    message="Failed to load object '{:s}'".format(asset_name),
                    asset_name=asset_name,
                    asset_type=asset_type,
                    loaded_into="none",
                )
            bpy.context.scene.collection.objects.link(obj)
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            return Result(
                status="ok",
                message="Object '{:s}' appended to scene".format(asset_name),
                asset_name=asset_name,
                asset_type=asset_type,
                loaded_into="scene",
            )

        elif asset_type == "WORLD":
            # Set as scene world.
            with bpy.data.libraries.load(blend_path) as (data_from, data_to):
                data_to.worlds = [asset_name]
            world = data_to.worlds[0]
            if world is None:
                return Result(
                    status="error",
                    message="Failed to load world '{:s}'".format(asset_name),
                    asset_name=asset_name,
                    asset_type=asset_type,
                    loaded_into="none",
                )
            bpy.context.scene.world = world
            return Result(
                status="ok",
                message="World '{:s}' set as scene world".format(asset_name),
                asset_name=asset_name,
                asset_type=asset_type,
                loaded_into="scene_world",
            )

        elif asset_type == "ACTION":
            # Assign to active object's animation data.
            with bpy.data.libraries.load(blend_path) as (data_from, data_to):
                data_to.actions = [asset_name]
            action = data_to.actions[0]
            if action is None:
                return Result(
                    status="error",
                    message="Failed to load action '{:s}'".format(asset_name),
                    asset_name=asset_name,
                    asset_type=asset_type,
                    loaded_into="none",
                )
            obj = bpy.context.active_object
            if obj is None:
                return Result(
                    status="error",
                    message="No active object to assign action to",
                    asset_name=asset_name,
                    asset_type=asset_type,
                    loaded_into="none",
                )
            if obj.animation_data is None:
                obj.animation_data_create()
            obj.animation_data.action = action
            return Result(
                status="ok",
                message="Action '{:s}' assigned to '{:s}'".format(asset_name, obj.name),
                asset_name=asset_name,
                asset_type=asset_type,
                loaded_into="object:{:s}".format(obj.name),
            )

        else:
            return Result(
                status="error",
                message="Unknown asset type '{:s}'".format(asset_type),
                asset_name=asset_name,
                asset_type=asset_type,
                loaded_into="none",
            )

    except Exception as ex:
        return Result(
            status="error",
            message="Failed to load asset: {:s}".format(str(ex)),
            asset_name=asset_name,
            asset_type=asset_type,
            loaded_into="none",
        )
