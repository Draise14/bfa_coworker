# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for getting detailed asset tags and metadata.
"""

__all__ = (
    "Result",
    "Params",
    "main",
)

from typing import Any, NamedTuple


_TREE_TYPE_ALIASES = {
    "SHADER": "ShaderNodeTree",
    "COMPOSITING": "CompositorNodeTree",
    "GEOMETRY": "GeometryNodeTree",
    "TEXTURE": "TextureNodeTree",
}


def _tree_type_name(value: Any) -> str:
    """Map real Blender ``NodeTree.type`` values (SHADER/COMPOSITING/GEOMETRY)
    to the friendly names used across the asset tools."""
    name = str(value)
    return _TREE_TYPE_ALIASES.get(name, name)


class Result(NamedTuple):
    status: str
    asset_name: str
    asset_type: str
    tags: list[str]
    editor_type: str
    color_tag: str
    description: str
    metadata: dict[str, Any]


class Params(NamedTuple):
    library_name: str
    asset_name: str
    asset_type: str = ""


def main(params: Params) -> Result:
    library_name, asset_name, asset_type = (
        params.library_name, params.asset_name, params.asset_type,
    )
    """Get detailed tags and metadata for an asset."""
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
            status="error: Library '{:s}' not found or path invalid".format(library_name),
            asset_name=asset_name,
            asset_type=asset_type or "unknown",
            tags=[],
            editor_type="",
            color_tag="NONE",
            description="",
            metadata={},
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
            status="error: Asset '{:s}' not found in library '{:s}'".format(asset_name, library_name),
            asset_name=asset_name,
            asset_type=asset_type or "unknown",
            tags=[],
            editor_type="",
            color_tag="NONE",
            description="",
            metadata={},
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

    # Load the asset to inspect its properties.
    tags = []
    editor_type = ""
    color_tag = "NONE"
    description = ""
    metadata = {}

    try:
        if asset_type == "NODETREE":
            # Load node group to inspect its type.
            with bpy.data.libraries.load(blend_path) as (data_from, data_to):
                data_to.node_groups = [asset_name]
            ng = data_to.node_groups[0]
            if ng is not None:
                editor_type = _tree_type_name(ng.type)  # 'GeometryNodeTree' / 'ShaderNodeTree' / 'CompositorNodeTree'

                if hasattr(ng, "color_tag"):
                    color_tag = str(ng.color_tag)

                if hasattr(ng, "description"):
                    description = ng.description or ""

                if hasattr(ng, "asset_data") and ng.asset_data:
                    if hasattr(ng.asset_data, "tags"):
                        for tag in ng.asset_data.tags:
                            tags.append(tag.name)

                metadata = {
                    "node_count": len(ng.nodes),
                    "input_count": _socket_count(ng, "INPUT"),
                    "output_count": _socket_count(ng, "OUTPUT"),
                    "is_modifier": editor_type == "GeometryNodeTree",
                    "is_shader": editor_type == "ShaderNodeTree",
                    "is_compositor": editor_type == "CompositorNodeTree",
                }

                editor_names = {
                    "GeometryNodeTree": "Geometry Nodes",
                    "ShaderNodeTree": "Shader Editor",
                    "CompositorNodeTree": "Compositor",
                }
                metadata["editor_name"] = editor_names.get(editor_type, editor_type)

                # Preview image path.
                metadata["preview_image_path"] = _get_preview_path(ng)

        elif asset_type == "MATERIAL":
            with bpy.data.libraries.load(blend_path) as (data_from, data_to):
                data_to.materials = [asset_name]
            mat = data_to.materials[0]
            if mat is not None:
                if hasattr(mat, "color_tag"):
                    color_tag = str(mat.color_tag)
                if hasattr(mat, "description"):
                    description = mat.description or ""
                if hasattr(mat, "asset_data") and mat.asset_data:
                    if hasattr(mat.asset_data, "tags"):
                        for tag in mat.asset_data.tags:
                            tags.append(tag.name)
                metadata = {
                    "has_nodes": mat.use_nodes if hasattr(mat, "use_nodes") else False,
                    "blend_method": str(mat.blend_method) if hasattr(mat, "blend_method") else "",
                }
                metadata["preview_image_path"] = _get_preview_path(mat)

        elif asset_type == "OBJECT":
            with bpy.data.libraries.load(blend_path) as (data_from, data_to):
                data_to.objects = [asset_name]
            obj = data_to.objects[0]
            if obj is not None:
                if hasattr(obj, "color_tag"):
                    color_tag = str(obj.color_tag)
                if hasattr(obj, "description"):
                    description = obj.description or ""
                if hasattr(obj, "asset_data") and obj.asset_data:
                    if hasattr(obj.asset_data, "tags"):
                        for tag in obj.asset_data.tags:
                            tags.append(tag.name)
                metadata = {
                    "object_type": obj.type,
                    "vertex_count": len(obj.data.vertices) if hasattr(obj, "data") and hasattr(obj.data, "vertices") else 0,
                }
                metadata["preview_image_path"] = _get_preview_path(obj)

        elif asset_type == "COLLECTION":
            with bpy.data.libraries.load(blend_path) as (data_from, data_to):
                data_to.collections = [asset_name]
            col = data_to.collections[0]
            if col is not None:
                if hasattr(col, "color_tag"):
                    color_tag = str(col.color_tag)
                if hasattr(col, "description"):
                    description = col.description or ""
                if hasattr(col, "asset_data") and col.asset_data:
                    if hasattr(col.asset_data, "tags"):
                        for tag in col.asset_data.tags:
                            tags.append(tag.name)
                metadata = {
                    "object_count": len(col.objects),
                    "child_collection_count": len(col.children),
                    "objects": [o.name for o in col.objects[:10]],
                }
                metadata["preview_image_path"] = _get_preview_path(col)

        elif asset_type == "WORLD":
            with bpy.data.libraries.load(blend_path) as (data_from, data_to):
                data_to.worlds = [asset_name]
            world = data_to.worlds[0]
            if world is not None:
                if hasattr(world, "color_tag"):
                    color_tag = str(world.color_tag)
                if hasattr(world, "description"):
                    description = world.description or ""
                if hasattr(world, "asset_data") and world.asset_data:
                    if hasattr(world.asset_data, "tags"):
                        for tag in world.asset_data.tags:
                            tags.append(tag.name)
                metadata = {
                    "use_nodes": world.use_nodes if hasattr(world, "use_nodes") else False,
                    "node_count": len(world.node_tree.nodes) if world.use_nodes and world.node_tree else 0,
                }
                metadata["preview_image_path"] = _get_preview_path(world)

        elif asset_type == "ACTION":
            with bpy.data.libraries.load(blend_path) as (data_from, data_to):
                data_to.actions = [asset_name]
            action = data_to.actions[0]
            if action is not None:
                if hasattr(action, "color_tag"):
                    color_tag = str(action.color_tag)
                if hasattr(action, "description"):
                    description = action.description or ""
                if hasattr(action, "asset_data") and action.asset_data:
                    if hasattr(action.asset_data, "tags"):
                        for tag in action.asset_data.tags:
                            tags.append(tag.name)
                metadata = {
                    "frame_range": [action.frame_range[0], action.frame_range[1]],
                    "fcurves_count": len(action.fcurves),
                }

        else:
            metadata = {"note": "Detailed inspection not supported for {:s}".format(asset_type)}

    except Exception as ex:
        return Result(
            status="error: Failed to inspect asset: {:s}".format(str(ex)),
            asset_name=asset_name,
            asset_type=asset_type,
            tags=[],
            editor_type="",
            color_tag="NONE",
            description="",
            metadata={},
        )

    return Result(
        status="ok",
        asset_name=asset_name,
        asset_type=asset_type,
        tags=tags,
        editor_type=editor_type,
        color_tag=color_tag,
        description=description,
        metadata=metadata,
    )


def _socket_count(ng, direction: str) -> int:
    """Count interface sockets in a direction, across API generations.

    Blender 5.x removed the legacy ``node_tree.inputs/outputs`` shortcut
    collections; the interface is read from ``ng.interface.items_tree``.
    """
    interface = getattr(ng, "interface", None)
    if interface is not None and hasattr(interface, "items_tree"):
        count = 0
        for item in interface.items_tree:
            if type(item).__name__ != "NodeTreeInterfaceSocket":
                continue
            if getattr(item, "in_out", "") == direction:
                count += 1
        return count
    try:
        return len(getattr(ng, "inputs" if direction == "INPUT" else "outputs"))
    except (AttributeError, TypeError):
        return 0


def _get_preview_path(datablock) -> str:
    """Return the preview image path for a datablock, or empty string."""
    try:
        if hasattr(datablock, "preview") and datablock.preview:
            # Blender preview image — try to get the file path.
            preview = datablock.preview
            if hasattr(preview, "image_size_raw"):
                return ""  # In-memory only, no file path.
    except Exception:
        pass
    return ""
