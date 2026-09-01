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
from blmcp.tools.jump_to_asset_browser_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Open Asset Browser",
            destructiveHint=True,
        )
    )
    def jump_to_asset_browser(
        library_name: str = "",
        catalog_path: str = "",
        allow_edits: bool = True,
    ) -> dict[str, object]:
        """
        Switch to (or create) the Asset Browser editor.

        If an Asset Browser area is already open in any workspace it is
        reused.  Otherwise, with *allow_edits* (default), a new workspace
        is created by duplicating the current one and converting its main
        area into the Asset Browser - so the user's current workspace is
        left untouched.

        *library_name* optionally preselects the asset library shown in the
        browser (best-effort: if the name matches a configured library or a
        built-in reference such as ``"LOCAL"`` / ``"USER"`` it is applied).
        *catalog_path* optionally selects a catalog/folder, best-effort.

        Args:
            library_name: Asset library to select (empty = leave as-is).
            catalog_path: Catalog path or catalog UUID to select (empty = leave as-is).
            allow_edits: Allow creating a new workspace/area when no Asset
                Browser is open.

        Returns:
            Status, workspace/area created or reused, and library/catalog applied.
        """
        return send_code(
            toolcode_format_call(
                _TOOL_CALL,
                Params(
                    library_name=library_name,
                    catalog_path=catalog_path,
                    allow_edits=allow_edits,
                ),
            ),
            strict_json=True,
        )