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
from blmcp.tools.list_asset_catalogs_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="List Asset Catalogs",
            readOnlyHint=True,
        )
    )
    def list_asset_catalogs(
        library_name: str = "",
    ) -> dict[str, object]:
        """
        List the catalog/directory structure of asset libraries.

        Shows how assets are organized into folders within each library,
        with counts of materials, node groups, objects, worlds, and actions
        in each directory.

        Args:
            library_name: Limit to a specific library. Empty = all libraries.

        Returns:
            Catalogs with paths and asset counts per directory.
        """
        return send_code(
            toolcode_format_call(_TOOL_CALL, Params(library_name=library_name)),
            strict_json=True,
        )
