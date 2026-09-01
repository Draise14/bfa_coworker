# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for loading a node-group asset and wiring it into a node tree.

Deterministic by design: the LLM picks the insert mode and target from an
enumerated interface (see ``get_node_group_interface``), this module does
the wiring with socket-type validation and a pre-mutation undo push.
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
    library_name: str = ""
    asset_name: str = ""
    tree_type: str = ""
    node_tree_name: str = ""
    insert_mode: str = "add_top_level"
    target_node: str = ""
    from_node: str = ""
    from_socket: str = ""
    to_node: str = ""
    to_socket: str = ""
    link_mode: str = "APPEND"
    auto_map: bool = True


class Result(NamedTuple):
    status: str
    asset_name: str
    tree_type: str
    tree_name: str
    insert_mode: str
    group_node: str
    links_created: list[str]
    unmapped: list[str]
    message: str


_NODE_TYPE_BY_TREE = {
    "ShaderNodeTree": "ShaderNodeGroup",
    "CompositorNodeTree": "CompositorNodeGroup",
    "GeometryNodeTree": "GeometryNodeGroup",
}

_EDITOR_NAMES = {
    "GeometryNodeTree": "Geometry Nodes",
    "ShaderNodeTree": "Shader Editor",
    "CompositorNodeTree": "Compositor",
}

# Output node hints per tree type: (node_type, input_socket_name, output_socket_type)
_OUTPUT_HINTS = {
    "ShaderNodeTree": ("ShaderNodeOutputMaterial", ["Surface", "Volume", "Displacement"], "SHADER"),
    "CompositorNodeTree": ("CompositorNodeComposite", ["Image", "Alpha"], "IMAGE"),
    "GeometryNodeTree": ("NodeGroupOutput", ["Geometry"], "GEOMETRY"),
}


def main(params: Params) -> Result:
    """Load a node-group asset and wire it into a target tree."""
    import bpy  # pylint: disable=import-error
    import os

    try:
        ng = _load_group(params, bpy)
        if ng is None:
            return _error(params, "Node group '{:s}' not found".format(params.asset_name))

        tree_type = _tree_type_name(params.tree_type or str(ng.type))
        if tree_type not in _NODE_TYPE_BY_TREE:
            return _error(
                params,
                "Unsupported node tree type '{:s}' (expected one of {:s})".format(
                    tree_type, ", ".join(sorted(_NODE_TYPE_BY_TREE))),
            )

        tree = _resolve_tree(params, tree_type, bpy)
        if tree is None:
            return _error(
                params,
                "Could not resolve a target node tree for '{:s}'".format(tree_type),
            )

        # Undo point *before* any mutation.
        try:
            bpy.ops.ed.undo_push(message="wire_node_group:\n{:s}".format(params.asset_name))
        except Exception:
            pass  # No window/area — still proceed.

        node = tree.nodes.new(type=_NODE_TYPE_BY_TREE[tree_type])
        node.node_tree = ng
        node.label = params.asset_name if not node.label else node.label

        links_created: list[str] = []
        unmapped: list[str] = []

        insert_mode = params.insert_mode
        if insert_mode not in ("add_top_level", "replace_active", "insert_between", "connect_to_output"):
            return _error(
                params,
                "Unknown insert_mode '{:s}' (expected add_top_level, "
                "replace_active, insert_between, connect_to_output)".format(insert_mode),
            )

        if insert_mode == "add_top_level":
            _position_after_active(node, tree, bpy)

        elif insert_mode == "replace_active":
            links_created, unmapped = _replace_active(node, tree, params, bpy)

        elif insert_mode == "insert_between":
            links_created, unmapped = _insert_between(node, tree, params, bpy)

        elif insert_mode == "connect_to_output":
            links_created, unmapped = _connect_to_output(node, tree, tree_type, bpy)

        return Result(
            status="ok",
            asset_name=params.asset_name,
            tree_type=tree_type,
            tree_name=tree.name,
            insert_mode=insert_mode,
            group_node=node.name,
            links_created=links_created,
            unmapped=unmapped,
            message="Group '{:s}' wired into '{:s}' ({:s}) as '{:s}': {:d} links, {:d} unmapped".format(
                params.asset_name, tree.name, _EDITOR_NAMES.get(tree_type, tree_type),
                node.name, len(links_created), len(unmapped)),
        )

    except Exception as ex:
        return _error(params, "Failed to wire node group: {:s}".format(str(ex)))


def _error(params: Params, message: str) -> Result:
    return Result(
        status="error",
        asset_name=params.asset_name,
        tree_type=params.tree_type,
        tree_name=params.node_tree_name,
        insert_mode=params.insert_mode,
        group_node="",
        links_created=[],
        unmapped=[],
        message=message,
    )


# ---------------------------------------------------------------------------
# Group loading


def _load_group(params: Params, bpy) -> Any:
    """Return the node group datablock (asset or already-loaded)."""
    if params.asset_name in bpy.data.node_groups:
        return bpy.data.node_groups[params.asset_name]

    if not params.library_name:
        return None

    blend_path = _find_blend_path(params, bpy)
    if not blend_path:
        return None

    link = params.link_mode.upper() == "LINK"
    with bpy.data.libraries.load(blend_path, link=link) as (data_from, data_to):
        data_to.node_groups = [params.asset_name]
    return data_to.node_groups[0]


def _find_blend_path(params: Params, bpy) -> str | None:
    """Walk the library and return the blend containing the node group."""
    import os

    lib_path = None
    for lib in bpy.context.preferences.filepaths.asset_libraries:
        if lib.name == params.library_name:
            lib_path = str(lib.path) if lib.path else None
            break
    if not lib_path or not os.path.isdir(lib_path):
        return None

    for root, _dirs, files in os.walk(lib_path):
        for f in files:
            if not f.endswith(".blend"):
                continue
            candidate = os.path.join(root, f)
            try:
                with bpy.data.libraries.load(candidate) as (data_from, _data_to):
                    if params.asset_name in list(data_from.node_groups):
                        return candidate
            except Exception:
                continue
    return None


# ---------------------------------------------------------------------------
# Tree resolution


def _resolve_tree(params: Params, tree_type: str, bpy) -> Any:
    """Return the target node tree (explicit name or editor context)."""
    if params.node_tree_name:
        tree = bpy.data.node_groups.get(params.node_tree_name)
        if tree is not None and _tree_type_name(tree.type) != tree_type:
            return None
        return tree

    if tree_type == "ShaderNodeTree":
        obj = bpy.context.object if bpy.context.object else None
        mat = obj.active_material if obj and obj.active_material else None
        if mat is None:
            return None
        if not mat.use_nodes or mat.node_tree is None:
            mat.use_nodes = True
        return mat.node_tree

    if tree_type == "CompositorNodeTree":
        scene = bpy.context.scene
        if not scene.use_nodes or scene.node_tree is None:
            scene.use_nodes = True
        return scene.node_tree

    if tree_type == "GeometryNodeTree":
        obj = bpy.context.object if bpy.context.object else None
        if obj is None:
            return None
        for mod in obj.modifiers:
            if mod.type == "NODES" and mod.node_group is not None:
                return mod.node_group
        # No existing modifier — create one with the loaded group? No: wire
        # into an *existing* tree only; the caller should use the GN-modifier
        # fallback path in load_asset_in_context for first-use.
        return None

    return None


# ---------------------------------------------------------------------------
# Positioning & insert modes


def _position_after_active(node: Any, tree: Any, bpy) -> None:
    """Place *node* near the active node (or at origin)."""
    active = tree.nodes.active if tree.nodes.active else None
    if active is not None and hasattr(active, "location"):
        node.location = (active.location.x + 300.0, active.location.y)
    else:
        node.location = (0.0, 0.0)
    tree.nodes.active = node


def _replace_active(node: Any, tree: Any, params: Params, bpy) -> tuple[list[str], list[str]]:
    """Wrap *target_node*: re-route its links through group *node*, then delete it."""
    target = None
    if params.target_node:
        target = tree.nodes.get(params.target_node)
        if target is None:
            raise LookupError(
                "Node '{:s}' not found in tree '{:s}'".format(params.target_node, tree.name))
    else:
        target = tree.nodes.active
    if target is None:
        raise LookupError("No target node given and no active node to wrap")

    if target.type == "FRAME":
        raise LookupError("Cannot wrap a FRAME node")

    node.location = (target.location.x - 200.0, target.location.y)
    links_created: list[str] = []
    unmapped: list[str] = []

    # Incoming links → group inputs.
    used_inputs: set[str] = set()
    for sock in target.inputs:
        for link in list(sock.links):
            from_sock = link.from_socket
            match = _pick_socket(node.inputs, from_sock, used_inputs, params.auto_map, "in")
            if match is None:
                unmapped.append("{:s} -> input of '{:s}'".format(
                    _link_str(link.from_node, from_sock), params.asset_name))
                continue
            used_inputs.add(match.name)
            tree.links.new(from_sock, match)
            links_created.append("{:s} -> {:s}.{:s}".format(
                _link_str(link.from_node, from_sock), node.name, match.name))

    # Outgoing links → group outputs.
    used_outputs: set[str] = set()
    for sock in target.outputs:
        for link in list(sock.links):
            to_sock = link.to_socket
            match = _pick_socket(node.outputs, sock, used_outputs, params.auto_map, "out")
            if match is None:
                unmapped.append("output '{:s}' of '{:s}': no room".format(
                    ".".join((target.name, sock.name)), params.asset_name))
                continue
            used_outputs.add(match.name)
            tree.links.new(match, to_sock)
            links_created.append("{:s}.{:s} -> {:s}".format(
                node.name, match.name, _link_str(link.to_node, to_sock)))

    tree.nodes.remove(target)
    tree.nodes.active = node
    return links_created, unmapped


def _insert_between(node: Any, tree: Any, params: Params, bpy) -> tuple[list[str], list[str]]:
    """Splice *node* into the link between from_node and to_node."""
    if not params.from_node or not params.to_node:
        raise LookupError("insert_between requires from_node and to_node names")

    from_n = tree.nodes.get(params.from_node)
    to_n = tree.nodes.get(params.to_node)
    if from_n is None:
        raise LookupError("Node '{:s}' not found in tree".format(params.from_node))
    if to_n is None:
        raise LookupError("Node '{:s}' not found in tree".format(params.to_node))

    # Locate the exact link (socket names optional).
    link = _find_link(tree, from_n, params.from_socket, to_n, params.to_socket)
    if link is None:
        raise LookupError(
            "No link between '{:s}' and '{:s}' (with socket '{:s}' -> '{:s}')".format(
                params.from_node, params.to_node, params.from_socket, params.to_socket))

    from_sock = link.from_socket
    to_sock = link.to_socket

    node.location = (
        (from_n.location.x + to_n.location.x) / 2.0 + 200.0,
        (from_n.location.y + to_n.location.y) / 2.0,
    )

    # Upstream → group input.
    match_in = _pick_socket(node.inputs, from_sock, set(), params.auto_map, "in")
    if match_in is None:
        raise LookupError(
            "Group '{:s}' has no input socket compatible with '{:s}' ({:s})".format(
                params.asset_name, from_sock.name, from_sock.type))
    # Group output → downstream.
    match_out = _pick_socket(node.outputs, to_sock, set(), params.auto_map, "out")
    if match_out is None:
        raise LookupError(
            "Group '{:s}' has no output socket compatible with '{:s}' ({:s})".format(
                params.asset_name, to_sock.name, to_sock.type))

    tree.links.remove(link)
    tree.links.new(from_sock, match_in)
    tree.links.new(match_out, to_sock)
    tree.nodes.active = node
    return [
        "{:s} -> {:s}.{:s}".format(_link_str(from_n, from_sock), node.name, match_in.name),
        "{:s}.{:s} -> {:s}".format(node.name, match_out.name, _link_str(to_n, to_sock)),
    ], []


def _connect_to_output(node: Any, tree: Any, tree_type: str, bpy) -> tuple[list[str], list[str]]:
    """Attach the group to the tree's output node (Material/Composite/Group Output)."""
    hints = _OUTPUT_HINTS[tree_type]
    out_node = None
    for n in tree.nodes:
        if n.bl_idname == hints[0]:
            out_node = n
            break
    if out_node is None:
        out_node = tree.nodes.new(type=hints[0])
        out_node.location = (node.location.x + 300.0, node.location.y)

    target_sock = None
    for sname in hints[1]:
        try:
            sock = out_node.inputs[sname]
        except (KeyError, IndexError):
            continue
        target_sock = sock
        if not sock.links:
            break
    if target_sock is None:
        # Fresh GeometryNodeTrees start with an empty interface, so the
        # Group Output node has no sockets yet. Create the plumbing socket
        # on the tree interface (this is the "wire it up" smarts).
        if tree_type == "GeometryNodeTree":
            # in_out is relative to the *tree*: an OUTPUT item surfaces as
            # an input on the Group Output node (an INPUT item would land on
            # the Group Input node instead), matching the asset's output.
            try:
                tree.interface.new_socket(
                    name="Geometry", in_out="OUTPUT",
                    socket_type="NodeSocketGeometry",
                )
                target_sock = out_node.inputs["Geometry"]
            except Exception as exc:
                return [], ["could not create output socket: {:s}".format(str(exc))]
        else:
            return [], ["output socket of '{:s}' already in use".format(out_node.label or out_node.name)]
    # Deterministic output attach: the asset drives the output, so replace
    # any existing link on the chosen socket (undo was already pushed).
    for link in list(target_sock.links):
        tree.links.remove(link)

    source_sock = _pick_socket(node.outputs, target_sock, set(), True, "out")
    if source_sock is None:
        return [], [
            "no output of type {:s} available on group".format(target_sock.type)]

    tree.links.new(source_sock, target_sock)
    tree.nodes.active = node
    return [
        "{:s}.{:s} -> {:s}.{:s}".format(
            node.name, source_sock.name, out_node.name, target_sock.name),
    ], []


# ---------------------------------------------------------------------------
# Socket matching


def _pick_socket(sockets: Any, hint_socket: Any, used: set[str], auto_map: bool, direction: str) -> Any:
    """Pick a target socket for *hint_socket* from *sockets*.

    Order: exact name, fuzzy name (case/space-stripped), then first unused
    socket of a compatible type.  Returns ``None`` when nothing fits.

    *direction* is ``"in"`` when picking a group **input** to receive from
    the hint socket (source → destination check) and ``"out"`` when picking
    a group **output** that feeds the hint socket (source → destination
    check from the group's side).
    """
    hint = hint_socket.name
    hint_type = hint_socket.type

    def _compatible(s: Any) -> bool:
        if direction == "in":
            # Group input *s* receives from the hint (source) socket.
            return _sockets_compatible(hint_type, s.type)
        # Group output *s* feeds the hint (destination) socket.
        return _sockets_compatible(s.type, hint_type)

    # 1) Exact name (case-insensitive).
    for s in sockets:
        if s.name.lower() == hint.lower() and s.name not in used:
            if _compatible(s):
                return s

    # 2) Fuzzy: strip non-alphanumerics and compare / substring.
    if auto_map:
        hint_norm = _normalize(hint)
        for s in sockets:
            if s.name in used or not _compatible(s):
                continue
            s_norm = _normalize(s.name)
            if s_norm == hint_norm or hint_norm in s_norm or s_norm in hint_norm:
                return s
        # 3) First unused socket of a compatible type.
        for s in sockets:
            if s.name in used:
                continue
            if _compatible(s):
                return s

    return None


def _sockets_compatible(from_type: str, to_type: str) -> bool:
    """Blender link compatibility (implicit conversions included).

    ``from_type`` feeds ``to_type``; VALUE feeds VECTOR/RGBA via Blender's
    implicit scalar conversion.
    """
    if from_type == to_type:
        return True
    if from_type == "VALUE" and to_type in ("VECTOR", "RGBA"):
        return True
    if from_type in ("INT", "BOOLEAN") and to_type == "VALUE":
        return True
    return False


def _find_link(tree: Any, from_n: Any, from_sock: str, to_n: Any, to_sock: str) -> Any:
    """Find a link between two nodes; socket names optional."""
    for link in tree.links:
        if link.from_node is not from_n or link.to_node is not to_n:
            continue
        if from_sock and link.from_socket.name != from_sock:
            continue
        if to_sock and link.to_socket.name != to_sock:
            continue
        return link
    return None


def _link_str(node: Any, socket: Any) -> str:
    return "{:s}.{:s}".format(node.name, socket.name)


def _normalize(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", name.lower())