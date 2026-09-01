# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for serializing a node tree for the LLM.
"""

__all__ = (
    "Params",
    "Result",
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


class Params(NamedTuple):
    tree_type: str = ""
    node_tree_name: str = ""


class Result(NamedTuple):
    status: str
    tree_name: str
    tree_type: str
    editor_name: str
    node_count: int
    link_count: int
    frame_count: int
    nodes: list[dict[str, Any]]
    links: list[dict[str, Any]]
    frames: list[dict[str, Any]]
    message: str


_EDITOR_NAMES = {
    "GeometryNodeTree": "Geometry Nodes",
    "ShaderNodeTree": "Shader Editor",
    "CompositorNodeTree": "Compositor",
}


def main(params: Params) -> Result:
    """Serialize the resolved node tree.

    *tree_type* must be "ShaderNodeTree", "CompositorNodeTree" or
    "GeometryNodeTree" (empty = first available in context resolution
    order: shader, geometry nodes, compositor).  If *node_tree_name* is
    given it takes precedence and *tree_type* is only used for the
    editor name.
    """
    import bpy  # pylint: disable=import-error

    tree, editor_name = _resolve_tree(params)
    if tree is None:
        return Result(
            status="error",
            tree_name="",
            tree_type=params.tree_type,
            editor_name="",
            node_count=0,
            link_count=0,
            frame_count=0,
            nodes=[],
            links=[],
            frames=[],
            message=editor_name,  # Error message filled by resolver.
        )

    nodes = [_node_info(n) for n in tree.nodes]
    links = [_link_info(l) for l in tree.links]
    tree_type = _tree_type_name(tree.type)
    frames = [
        {
            "name": f.name,
            "label": f.label or "",
            "node_ids": [n.name for n in f.nodes],
        }
        for f in tree.nodes
        if f.type == "FRAME"
    ]

    return Result(
        status="ok",
        tree_name=tree.name,
        tree_type=tree_type,
        editor_name=_EDITOR_NAMES.get(tree_type, tree_type),
        node_count=len(nodes),
        link_count=len(links),
        frame_count=len(frames),
        nodes=nodes,
        links=links,
        frames=frames,
        message="Serialized '{:s}' ({:s}): {:d} nodes, {:d} links".format(
            tree.name, _EDITOR_NAMES.get(tree_type, tree_type),
            len(nodes), len(links)),
    )


def _resolve_tree(params: Params):
    """Return (tree, editor_name) or (None, error_message)."""
    import bpy  # pylint: disable=import-error

    if params.node_tree_name:
        tree = bpy.data.node_groups.get(params.node_tree_name)
        if tree is None:
            return None, "Node group '{:s}' not found".format(params.node_tree_name)
        return tree, _EDITOR_NAMES.get(_tree_type_name(tree.type), _tree_type_name(tree.type))

    tree_type = params.tree_type or ""
    if tree_type not in ("ShaderNodeTree", "CompositorNodeTree", "GeometryNodeTree"):
        # Auto-detect in context-resolution order.
        for candidate in ("ShaderNodeTree", "GeometryNodeTree", "CompositorNodeTree"):
            tree, editor_name = _resolve_by_type(candidate)
            if tree is not None:
                return tree, editor_name
        return None, (
            "Could not resolve a node tree: no active material with nodes, "
            "no Geometry Nodes modifier, and no compositor tree."
        )

    return _resolve_by_type(tree_type)


def _resolve_by_type(tree_type: str):
    """Resolve a tree by editor type using context (like the editors do)."""
    import bpy  # pylint: disable=import-error

    if tree_type == "ShaderNodeTree":
        obj = bpy.context.object if bpy.context.object else None
        mat = obj.active_material if obj and obj.active_material else None
        if mat is None:
            return None, "No active object with a material"
        if not mat.use_nodes or mat.node_tree is None:
            mat.use_nodes = True
        return mat.node_tree, "Shader Editor"

    if tree_type == "GeometryNodeTree":
        obj = bpy.context.object if bpy.context.object else None
        if obj is None:
            return None, "No active object"
        for mod in obj.modifiers:
            if mod.type == "NODES" and mod.node_group is not None:
                return mod.node_group, "Geometry Nodes"
        return None, "Active object has no Geometry Nodes modifier"

    if tree_type == "CompositorNodeTree":
        scene = bpy.context.scene
        if not scene.use_nodes or scene.node_tree is None:
            scene.use_nodes = True
        return scene.node_tree, "Compositor"

    return None, "Unknown tree type '{:s}'".format(tree_type)


def _node_info(node: Any) -> dict[str, Any]:
    """Compact serialization of a node."""
    has_mute = hasattr(node, "mute")
    info: dict[str, Any] = {
        "name": node.name,
        "type": node.type,
        "bl_idname": node.bl_idname,
        "label": node.label or "",
    }
    try:
        info["location"] = [round(v, 2) for v in node.location]
    except AttributeError:
        pass
    if has_mute:
        info["mute"] = bool(node.mute)
    try:
        info["inputs"] = [
            {
                "name": s.name,
                "type": s.type,
                "default": _jsonable(s.default_value),
            }
            for s in node.inputs
            if not s.hide
        ]
    except AttributeError:
        info["inputs"] = []
    try:
        info["outputs"] = [
            {
                "name": s.name,
                "type": s.type,
            }
            for s in node.outputs
            if not s.hide
        ]
    except AttributeError:
        info["outputs"] = []
    return info


def _link_info(link: Any) -> dict[str, Any]:
    """Serialize a link by node + socket names (stable across sessions)."""
    from_socket = link.from_socket
    to_socket = link.to_socket
    return {
        "from_node": link.from_node.name,
        "from_socket": from_socket.name,
        "from_type": from_socket.type,
        "to_node": link.to_node.name,
        "to_socket": to_socket.name,
        "to_type": to_socket.type,
    }


def _jsonable(value: Any) -> Any:
    """Convert a socket default (float / color / vector) to a plain value."""
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    try:
        return list(value)
    except TypeError:
        return repr(value)