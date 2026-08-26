# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for getting detailed asset tags and metadata.
"""

__all__ = (
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Result(NamedTuple):
    status: str
    asset_name: str
    asset_type: str
    tags: list[str]
    editor_type: str
    color_tag: str
    description: str
    metadata: dict[str, Any]


def main(library_name: str, asset_name: str, asset_type: str = "") -> Result:
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
                # Get the node group type (editor type).
                editor_type = ng.type  # 'GeometryNodeTree', 'ShaderNodeTree', 'CompositorNodeTree'
                
                # Get color tag from asset metadata if available.
                if hasattr(ng, 'color_tag'):
                    color_tag = str(ng.color_tag)
                
                # Get description.
                if hasattr(ng, 'description'):
                    description = ng.description or ""
                
                # Get tags from asset data if available.
                if hasattr(ng, 'asset_data') and ng.asset_data:
                    if hasattr(ng.asset_data, 'tags'):
                        for tag in ng.asset_data.tags:
                            tags.append(tag.name)
                
                # Additional metadata for node groups.
                metadata = {
                    "node_count": len(ng.nodes),
                    "input_count": len(ng.inputs),
                    "output_count": len(ng.outputs),
                    "is_modifier": editor_type == "GeometryNodeTree",
                    "is_shader": editor_type == "ShaderNodeTree",
                    "is_compositor": editor_type == "CompositorNodeTree",
                }
                
                # Map editor type to human-readable name.
                editor_names = {
                    "GeometryNodeTree": "Geometry Nodes",
                    "ShaderNodeTree": "Shader Editor",
                    "CompositorNodeTree": "Compositor",
                }
                metadata["editor_name"] = editor_names.get(editor_type, editor_type)

        elif asset_type == "MATERIAL":
            with bpy.data.libraries.load(blend_path) as (data_from, data_to):
                data_to.materials = [asset_name]
            mat = data_to.materials[0]
            if mat is not None:
                if hasattr(mat, 'color_tag'):
                    color_tag = str(mat.color_tag)
                if hasattr(mat, 'description'):
                    description = mat.description or ""
                if hasattr(mat, 'asset_data') and mat.asset_data:
                    if hasattr(mat.asset_data, 'tags'):
                        for tag in mat.asset_data.tags:
                            tags.append(tag.name)
                metadata = {
                    "has_nodes": mat.use_nodes if hasattr(mat, 'use_nodes') else False,
                    "blend_method": str(mat.blend_method) if hasattr(mat, 'blend_method') else "",
                }

        elif asset_type == "OBJECT":
            with bpy.data.libraries.load(blend_path) as (data_from, data_to):
                data_to.objects = [asset_name]
            obj = data_to.objects[0]
            if obj is not None:
                if hasattr(obj, 'color_tag'):
                    color_tag = str(obj.color_tag)
                if hasattr(obj, 'description'):
                    description = obj.description or ""
                if hasattr(obj, 'asset_data') and obj.asset_data:
                    if hasattr(obj.asset_data, 'tags'):
                        for tag in obj.asset_data.tags:
                            tags.append(tag.name)
                metadata = {
                    "object_type": obj.type,
                }

        else:
            # For other types, just return basic info.
            metadata = {"note": "Detailed inspection not yet supported for {:s}".format(asset_type)}

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
