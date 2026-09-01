# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

# pylint: disable=C0114  # See tool doc-string.

__all__ = (
    "register",
)

from blmcp.tools_helpers import (
    toolcode_format_call,
    toolcode_load_from_filepath,
    toolcode_wrap_with_calling_convention,
)
from blmcp.tools_helpers.connection import send_code
from blmcp.tools.wire_node_group_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Wire Node Group",
            destructiveHint=True,
        )
    )
    def wire_node_group(
        library_name: str = "",
        asset_name: str = "",
        tree_type: str = "",
        node_tree_name: str = "",
        insert_mode: str = "add_top_level",
        target_node: str = "",
        from_node: str = "",
        from_socket: str = "",
        to_node: str = "",
        to_socket: str = "",
        link_mode: str = "APPEND",
        auto_map: bool = True,
    ) -> dict[str, object]:
        """
        Load a node-group asset and wire it into a target node tree.

        Unlike ``load_asset_in_context`` (which drops the group unconnected
        at top level), this tool splices the group **into the graph** with
        validated, undo-able links.

        ``insert_mode``:
        - ``"add_top_level"`` - place the group unconnected near the active
          node (fallback; inspect the tree with ``get_active_node_tree``
          first for the other modes).
        - ``"replace_active"`` - wrap ``target_node`` (default: the active
          node): its incoming links re-route through the group's inputs and
          its outgoing links through the group's outputs, then the target
          node is removed.
        - ``"insert_between"`` - splice into the link between
          ``from_node``/``from_socket`` and ``to_node``/``to_socket``
          (socket names optional - matched automatically).
        - ``"connect_to_output"`` - attach the group to the tree's output:
          a SHADER output to Material Output *Surface*, an IMAGE output to
          Composite *Image*, or a GEOMETRY output to Group Output *Geometry*.

        Socket matching is deterministic: exact socket name first, then
        fuzzy name, then first unused socket of a compatible type.  Any
        sockets that cannot be mapped are reported in ``unmapped`` instead
        of failing silently.  ``bpy.ops.ed.undo_push`` is called before
        mutating so a bad wire is one undo away.

        Args:
            library_name: Asset library to load from (empty = group must
                already exist in ``bpy.data.node_groups``, e.g. loaded by
                ``load_asset_in_context``).
            asset_name: Node group asset name.
            tree_type: ``"ShaderNodeTree"`` / ``"CompositorNodeTree"`` /
                ``"GeometryNodeTree"`` (empty = the group's own type).
            node_tree_name: Explicit target tree name; empty = resolve from
                context (active material / compositor / GN modifier).
            insert_mode: How to wire the group (see above).
            target_node: Node name for ``replace_active`` (empty = active).
            from_node, from_socket, to_node, to_socket: Link endpoints for
                ``insert_between``.
            link_mode: ``"APPEND"`` (default) or ``"LINK"``.
            auto_map: Enable deterministic interface auto-mapping.

        Returns:
            Status, the created node name, links created, and any
            unmapped sockets.
        """
        return send_code(
            toolcode_format_call(
                _TOOL_CALL,
                Params(
                    library_name=library_name,
                    asset_name=asset_name,
                    tree_type=tree_type,
                    node_tree_name=node_tree_name,
                    insert_mode=insert_mode,
                    target_node=target_node,
                    from_node=from_node,
                    from_socket=from_socket,
                    to_node=to_node,
                    to_socket=to_socket,
                    link_mode=link_mode,
                    auto_map=auto_map,
                ),
            ),
            strict_json=True,
        )