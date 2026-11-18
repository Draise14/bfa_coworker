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
        List the catalogs of asset libraries: their paths and UUIDs.

        Reads each library's catalog file (blender_assets.cats.txt), the same source that
        jump_to_asset_browser uses, so a path returned here can be passed to it as catalog_path.
        Folders on disk are not catalogs. A library without a catalog file answers with an
        empty list and a message saying so. When the library's metadata index is ready, each
        catalog also gets its asset count and up to 10 sample asset names, and each library the
        number of assets without a catalog (the first call starts building the index).

        Args:
            library_name: Limit to a specific library. Empty = all libraries.
                          LOCAL = the current file's catalogs, ESSENTIALS = the built-in library.

        Returns:
            catalogs (path, uuid, library, and assets / counts / asset_names when known),
            libraries (catalog count and assets without a catalog), asset_counts, message.
        """
        return send_code(
            toolcode_format_call(_TOOL_CALL, Params(library_name=library_name)),
            strict_json=True,
        )
