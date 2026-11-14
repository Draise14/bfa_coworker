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
from blmcp.tools.get_node_tree_as_code_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Node Tree As Code",
            readOnlyHint=True,
        )
    )
    def get_node_tree_as_code(
        tree_type: str = "",
        node_tree_name: str = "",
        material_name: str = "",
        object_name: str = "",
        modifier_name: str = "",
        nodes: list[str] | None = None,
        include_groups: bool = True,
        max_chars: int = 6000,
        node_offset: int = 0,
    ) -> dict[str, object]:
        """
        Read a whole node tree as short build code (Geometry Nodes, shader or compositor).

        One line per node, only the settings and input values that differ from a
        new node's, links written as references, e.g.::

            tree "Scatter" (GeometryNodeTree, is_modifier=True)
            input Geometry: Geometry
            input Density: Float(min_value=0.0) = 10.0
            output Geometry: Geometry
            "Group Input" = GroupInput()
            points = DistributePointsOnFaces(Mesh="Group Input".Geometry, Density="Group Input".Density)
            "Group Output" = GroupOutput(Geometry=points.Points)

        Edit the text and send it to ``build_node_tree_from_code``.  The node groups the
        tree uses come first, as their own ``tree`` blocks.  Read only: a missing tree is
        reported, never created.

        Which tree (like ``get_active_node_tree``): ``node_tree_name`` (a node group),
        ``material_name``, or ``object_name`` (+ ``modifier_name``) for its Geometry Nodes
        modifier / active material; nothing = the active object's material, then its
        Geometry Nodes modifier, then the scene's compositor.

        Args:
            tree_type: "geometry", "shader" or "compositor" (or GeometryNodeTree...).
            node_tree_name: A node group's name.
            material_name: A material's name (its node tree).
            object_name: An object: its Geometry Nodes modifier's group, or its material.
            modifier_name: Which Geometry Nodes modifier of the object.
            nodes: Only these nodes (by name).
            include_groups: Also print the node groups the tree uses.
            max_chars: Size limit; whole nodes are cut and the result says how to get the rest.
            node_offset: Start at this node (to read the rest after a cut).

        Returns:
            ``code`` (the text), ``node_count``, ``cut_nodes`` and ``next_node_offset``
            (-1 = nothing cut), the ``groups`` printed.
        """
        return send_code(
            toolcode_format_call(
                _TOOL_CALL,
                Params(
                    tree_type=tree_type,
                    node_tree_name=node_tree_name,
                    material_name=material_name,
                    object_name=object_name,
                    modifier_name=modifier_name,
                    nodes=list(nodes) if nodes else None,
                    include_groups=include_groups,
                    max_chars=max_chars,
                    node_offset=node_offset,
                ),
            ),
            strict_json=True,
        )
