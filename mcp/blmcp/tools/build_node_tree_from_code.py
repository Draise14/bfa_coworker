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
from blmcp.tools.build_node_tree_from_code_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Build Node Tree From Code",
            destructiveHint=True,
        )
    )
    def build_node_tree_from_code(
        code: str,
        mode: str = "create",
        dry_run: bool = False,
        tree_name: str = "",
        layout: str = "",
    ) -> dict[str, object]:
        """
        Build or rebuild a whole node tree from the text ``get_node_tree_as_code`` prints.

        One line per node: ``name = NodeType(setting=value, Socket=value, "Socket Name"=other.Output)``.
        Node types are the bl_idname without its prefix (``SetPosition``, ``Math``,
        ``BsdfPrincipled``, ``GroupInput``) or the full bl_idname; a bare key that is a node
        property is a setting, every other key (and every quoted key) an input socket, by
        name or identifier.  Links: ``other.Output``, ``other["Socket Name"]``, a list for a
        multi-input socket, ``muted(other.Output)``.  Interface: ``input Name: Float(min_value=0)
        = 1.0`` / ``output Geometry: Geometry``; ``frame f (label="Inputs"): a, b``; zones:
        ``paired_output=other`` on the zone's input node; ``del name`` removes a node (update).
        Node groups the tree uses go first, as their own ``tree "Name" (Type)`` blocks.

        Modes: ``create`` (a new node group / material; an existing name is an error),
        ``replace`` (rebuild the existing tree in place: same datablock, its users and
        interface sockets kept), ``update`` (change or add only the nodes in the text).
        Everything is checked against Blender first (a copy is built): an unknown node type,
        setting, socket or node returns the nearest valid names and changes nothing.
        Values Blender refuses are listed in ``not_set``.  One undo step.

        Args:
            code: The text (one or more ``tree`` / ``material`` blocks; the last is the target).
            mode: "create", "replace" or "update".
            dry_run: Only check and list the changes.
            tree_name: Name for the target instead of the one in its header (e.g. to build a
                copy), or the tree to update when the text has no header.
            layout: "auto" (arrange left to right by links; the default for create) or "keep"
                (nodes that already exist keep their place; the default for replace / update).

        Returns:
            ``status`` ("ok", "dry_run" or "error"), ``changes``, ``not_set``, ``errors``.
        """
        return send_code(
            toolcode_format_call(
                _TOOL_CALL,
                Params(code=code, mode=mode, dry_run=dry_run, tree_name=tree_name, layout=layout),
            ),
            strict_json=True,
        )
