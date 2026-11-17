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
        reused.  Otherwise, with *allow_edits* (default), the largest area of
        the current workspace (not the Coworker chat) is turned into an Asset
        Browser.  No workspace is created, renamed or switched; the result's
        ``changed_area`` and message name the editor that was replaced, so it
        can be set back.

        *library_name* optionally preselects the asset library shown in the
        browser (best-effort: if the name matches a configured library or a
        built-in reference such as ``"LOCAL"`` / ``"USER"`` it is applied).
        *catalog_path* optionally selects a catalog: a path such as
        ``"Props/Chairs"`` (looked up in the library's ``blender_assets.cats.txt``;
        for ``"LOCAL"`` the one next to the saved .blend) or a catalog UUID.
        An unknown path is not applied and the nearest catalog paths come back
        in ``nearest_catalogs``; anything that could not be applied is
        explained in ``message``.

        Args:
            library_name: Asset library to select (empty = leave as-is).
            catalog_path: Catalog path or catalog UUID to select (empty = leave as-is).
            allow_edits: Allow turning an area of the current workspace into an
                Asset Browser when none is open.

        Returns:
            Status, workspace, whether an area was converted (and which), the library and
            catalog (path and UUID) applied, and what could not be applied.
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