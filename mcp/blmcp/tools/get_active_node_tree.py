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
from blmcp.tools.get_active_node_tree_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Active Node Tree",
            readOnlyHint=True,
        )
    )
    def get_active_node_tree(
        tree_type: str = "",
        node_tree_name: str = "",
    ) -> dict[str, object]:
        """
        Serialize a node tree for the LLM.

        Resolves the target tree the same way the Shader/Geometry
        Nodes/Compositor editors do:

        - ``"ShaderNodeTree"`` - the active object's active material.
        - ``"GeometryNodeTree"`` - the active object's active Geometry
          Nodes modifier.
        - ``"CompositorNodeTree"`` - the scene's compositor tree
          (enables ``use_nodes`` if needed).
        - ``node_tree_name`` - overrides resolution and reads that exact
          tree from ``bpy.data.node_groups``.

        Returns a compact summary: nodes (name, type, label, location,
        muted state), their input/output sockets (name + type + default),
        links (from-to by node and socket name), and frames.

        Args:
            tree_type: One of ``"ShaderNodeTree"``, ``"CompositorNodeTree"``,
                ``"GeometryNodeTree"`` (empty = auto-detect first available).
            node_tree_name: Optional explicit ``bpy.data.node_groups`` name.

        Returns:
            Status, tree metadata, and node/link/frame lists.
        """
        return send_code(
            toolcode_format_call(
                _TOOL_CALL,
                Params(tree_type=tree_type, node_tree_name=node_tree_name),
            ),
            strict_json=True,
        )