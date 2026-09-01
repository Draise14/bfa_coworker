# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for loading an asset from the asset browser into the current context.
"""

__all__ = (
    "Result",
    "Params",
    "main",
)

from typing import Any, NamedTuple, Optional

# @include_begin: _asset_index_shared.py
# @include_end


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
    message: str
    asset_name: str
    asset_type: str
    loaded_into: str


class Params(NamedTuple):
    library_name: str
    asset_name: str
    asset_type: str = ""
    link_mode: str = "APPEND"
    location: Optional[tuple[float, float, float]] = None
    object_name: str = ""
    tree_name: str = ""
    import_method: str = "auto"


def main(params: Params) -> Result:
    library_name, asset_name, asset_type, link_mode, location = (
        params.library_name, params.asset_name, params.asset_type,
        params.link_mode, params.location,
    )
    """Load an asset from the asset browser into the current context.

    *link_mode* — ``"APPEND"`` (default, full independent copy) or
    ``"LINK"`` (shared reference to the source file).  Append is
    preferred for materials, node groups, and small assets.  Link is
    useful for large collections you want to keep in sync with the
    source library.

    *location* — optional ``(x, y, z)`` world position for COLLECTION
    and OBJECT assets.  Ignored for other types.
    """
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

    # Determine append vs link for bpy.data.libraries.load().
    do_link, do_pack = _resolve_load_mode(params, lib_path, bpy)

    # Load based on type.
    try:
        if asset_type == "MATERIAL":
            # Load material and assign to active object.
            with bpy.data.libraries.load(blend_path, link=do_link, pack=do_pack) as (data_from, data_to):
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
            obj = _resolve_object(bpy, params)
            if obj is None:
                # Create a cube if no active object.
                bpy.ops.mesh.primitive_cube_add()
                obj = _active_object(bpy)
                obj.name = "Asset_Target"
            if obj.data and hasattr(obj.data, "materials"):
                if obj.data.materials:
                    # Replace first material slot.
                    obj.data.materials[0] = mat
                else:
                    obj.data.materials.append(mat)
            return Result(
                status="ok",
                message="Material '{:s}' loaded ({:s}) and assigned to '{:s}'".format(
                    asset_name, "linked" if do_link else "appended", obj.name),
                asset_name=asset_name,
                asset_type=asset_type,
                loaded_into="object:{:s}".format(obj.name),
            )

        elif asset_type == "NODETREE":
            # Load node group. Detect editor type for smart placement.
            with bpy.data.libraries.load(blend_path, link=do_link, pack=do_pack) as (data_from, data_to):
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

            ng_type = _tree_type_name(ng.type)  # GeometryNodeTree / ShaderNodeTree / CompositorNodeTree

            # --- Geometry Nodes → add as modifier on active object ---
            if ng_type == "GeometryNodeTree":
                obj = _resolve_object(bpy, params)
                if obj and obj.type == "MESH":
                    mod = obj.modifiers.new(name=asset_name, type="NODES")
                    mod.node_group = ng
                    return Result(
                        status="ok",
                        message="Geometry Nodes '{:s}' added as modifier on '{:s}'".format(
                            asset_name, obj.name),
                        asset_name=asset_name,
                        asset_type=asset_type,
                        loaded_into="modifier:{:s}".format(obj.name),
                    )
                else:
                    return Result(
                        status="ok",
                        message="Geometry Nodes '{:s}' loaded (no mesh object for modifier)".format(
                            asset_name),
                        asset_name=asset_name,
                        asset_type=asset_type,
                        loaded_into="data_only",
                    )

            # --- Compositor → add to compositor node tree ---
            elif ng_type == "CompositorNodeTree":
                scene = _resolve_scene(bpy, params)
                scene.use_nodes = True
                comp_tree = scene.node_tree
                if comp_tree:
                    node = comp_tree.nodes.new(type="CompositorNodeGroup")
                    node.node_tree = ng
                    node.location = (0, 0)
                    return Result(
                        status="ok",
                        message="Compositor node group '{:s}' added to scene compositor".format(
                            asset_name),
                        asset_name=asset_name,
                        asset_type=asset_type,
                        loaded_into="compositor",
                    )
                else:
                    return Result(
                        status="ok",
                        message="Compositor node group '{:s}' loaded (no compositor tree)".format(
                            asset_name),
                        asset_name=asset_name,
                        asset_type=asset_type,
                        loaded_into="data_only",
                    )

            # --- Shader → add to a material's node tree ---
            else:
                shader_tree = _resolve_shader_tree(bpy, params)
                if shader_tree is not None:
                    node = shader_tree.nodes.new(type="ShaderNodeGroup")
                    node.node_tree = ng
                    return Result(
                        status="ok",
                        message="Shader node group '{:s}' loaded into material '{:s}'".format(
                            asset_name, shader_tree.name),
                        asset_name=asset_name,
                        asset_type=asset_type,
                        loaded_into="material:{:s}".format(shader_tree.name),
                    )
                else:
                    return Result(
                        status="ok",
                        message="Node group '{:s}' loaded ({:s}, no material to insert into)".format(
                            asset_name, ng_type),
                        asset_name=asset_name,
                        asset_type=asset_type,
                        loaded_into="data_only",
                    )

        elif asset_type == "COLLECTION":
            # Load collection (append or link).
            with bpy.data.libraries.load(blend_path, link=do_link, pack=do_pack) as (data_from, data_to):
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
            # Position if location provided — move all objects in the collection.
            if location is not None:
                _set_collection_location(col, location)
            return Result(
                status="ok",
                message="Collection '{:s}' {:s} to scene{:s}".format(
                    asset_name,
                    "linked" if do_link else "appended",
                    " at ({:.1f}, {:.1f}, {:.1f})".format(*location) if location else ""),
                asset_name=asset_name,
                asset_type=asset_type,
                loaded_into="scene",
            )

        elif asset_type == "OBJECT":
            # Load object (append or link).
            with bpy.data.libraries.load(blend_path, link=do_link, pack=do_pack) as (data_from, data_to):
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
            # Position if location provided.
            if location is not None:
                obj.location = location
            return Result(
                status="ok",
                message="Object '{:s}' {:s} to scene{:s}".format(
                    asset_name,
                    "linked" if do_link else "appended",
                    " at ({:.1f}, {:.1f}, {:.1f})".format(*location) if location else ""),
                asset_name=asset_name,
                asset_type=asset_type,
                loaded_into="scene",
            )

        elif asset_type == "WORLD":
            # Set as scene world.
            with bpy.data.libraries.load(blend_path, link=do_link, pack=do_pack) as (data_from, data_to):
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
            with bpy.data.libraries.load(blend_path, link=do_link, pack=do_pack) as (data_from, data_to):
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
            obj = _resolve_object(bpy, params)
            if obj is None:
                return Result(
                    status="error",
                    message="No object to assign action to",
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


def _set_collection_location(col, location: tuple[float, float, float]) -> None:
    """Move all objects in a collection to the given world location."""
    import bpy  # pylint: disable=import-error
    x, y, z = location
    for obj in col.objects:
        obj.location = (obj.location.x + x, obj.location.y + y, obj.location.z + z)


def _active_object(bpy):
    """Return the active object, resilient to the Blender 5.x removal of
    ``bpy.context.active_object``."""
    try:
        obj = getattr(bpy.context, "object", None)
        if obj is not None:
            return obj
    except Exception:
        pass
    try:
        active = bpy.context.view_layer.objects.active
        if active is not None:
            return active
    except Exception:
        pass
    # Legacy Blender 4.x (and the test stub) still expose the alias.
    try:
        return bpy.context.active_object
    except Exception:
        return None


def _resolve_object(bpy, params: Params):
    """Return the explicit *object_name* target or the active object."""
    if params.object_name:
        obj = bpy.data.objects.get(params.object_name)
        if obj is not None:
            return obj
    return _active_object(bpy)


def _resolve_scene(bpy, params: Params):
    """Return the explicit *tree_name* scene (for compositor) or the active scene."""
    if params.tree_name:
        scene = bpy.data.scenes.get(params.tree_name)
        if scene is not None:
            return scene
    return bpy.context.scene


def _resolve_shader_tree(bpy, params: Params):
    """Return the explicit shader node tree (material or node-group name) or
    the active material's tree."""
    if params.tree_name:
        mat = bpy.data.materials.get(params.tree_name)
        if mat is not None:
            if not mat.use_nodes or mat.node_tree is None:
                mat.use_nodes = True
            return mat.node_tree
        ng = bpy.data.node_groups.get(params.tree_name)
        if ng is not None and _tree_type_name(ng.type) == "ShaderNodeTree":
            return ng
    obj = _active_object(bpy)
    mat = obj.active_material if obj and obj.active_material else None
    if mat is None:
        return None
    if not mat.use_nodes or mat.node_tree is None:
        mat.use_nodes = True
    return mat.node_tree


def _resolve_load_mode(params: Params, lib_path: str = "", bpy: Any = None) -> tuple[bool, bool]:
    """Map *import_method* / *link_mode* to ``(link, pack)`` load flags.

    ``auto`` honours the asset's ``preferred_import_method`` when the
    metadata index knows it (wired in Tier 3d Phase C); otherwise it falls
    back to *link_mode* (default ``APPEND``).
    """
    method = (params.import_method or "auto").lower()
    if method not in ("append", "link", "pack"):
        method = "auto"
    if method == "auto" and lib_path and bpy is not None:
        entry = _blmcp_index_lookup(lib_path, params.asset_name, bpy, params.asset_type)
        preferred = entry.get("preferred_import_method", "") if isinstance(entry, dict) else ""
        if isinstance(preferred, str) and preferred.lower() in ("append", "link", "pack"):
            method = preferred.lower()
    if method == "auto":
        method = (params.link_mode or "APPEND").lower()
    if method == "pack":
        return True, True
    if method == "link":
        return True, False
    return False, False
