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
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Set Collection Color Tag",
            readOnlyHint=False,
        )
    )
    def set_collection_color_tag(
        collection_name: str,
        color: str,
    ) -> dict[str, object]:
        """
        Set the color tag of a collection in the current scene.

        Args:
            collection_name: Name of the collection to modify.
            color: Color tag to set. One of: NONE, COLOR_01, COLOR_02, COLOR_03,
                   COLOR_04, COLOR_05, COLOR_06, COLOR_07, COLOR_08.

        Returns status and the new color tag value.
        """
        return send_code(
            toolcode_format_call(_TOOL_CALL, {
                "collection_name": collection_name,
                "color": color,
            }),
            strict_json=True,
        )
