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
from blmcp.tools.get_node_group_interface_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Node Group Interface",
            readOnlyHint=True,
        )
    )
    def get_node_group_interface(group_name: str) -> dict[str, object]:
        """
        Return the interface of a node group loaded in the current blend file.

        Lists the group's editor type (Geometry Nodes / Shader / Compositor)
        and every input/output socket with its socket type, default value,
        min/max range, and description. The asset-author convention is to
        name interface inputs ``Scale``, ``Seed``, ``Strength``, ``Color``
        and to put a one-line usage note in the asset description, which
        lets this tool double as the group's wiring manual.

        Args:
            group_name: Name of the node group in ``bpy.data.node_groups``
                (e.g. the group loaded by ``load_asset_in_context``).

        Returns:
            Status, editor type, input and output socket lists with types
            and defaults.
        """
        return send_code(
            toolcode_format_call(_TOOL_CALL, Params(group_name=group_name)),
            strict_json=True,
        )