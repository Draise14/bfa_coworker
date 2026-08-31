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
            title="Load Asset In Context",
            readOnlyHint=False,
        )
    )
    def load_asset_in_context(
        library_name: str,
        asset_name: str,
        asset_type: str = "",
        link_mode: str = "APPEND",
        location: list[float] | None = None,
    ) -> dict[str, object]:
        """
        Load an asset from the asset browser into the current context.

        Type-aware loading:
        - Material: Assigns to active object (replaces slot 0 or appends)
        - Geometry Node Group: Adds as modifier on active mesh object
        - Shader Node Group: Adds to active material's node tree
        - Compositor Node Group: Adds to scene compositor node tree
        - Collection: Appends/links collection to scene (optionally at a position)
        - Object: Appends/links object to scene (optionally at a position)
        - World: Sets as scene world
        - Action: Assigns to active object's animation data

        Default is APPEND (full independent copy). Use LINK for shared
        references to source files (e.g. large collections you want to
        keep in sync).

        Args:
            library_name: Name of the asset library to load from.
            asset_name: Name of the asset to load.
            asset_type: Optional type hint (MATERIAL, NODETREE, COLLECTION, OBJECT, WORLD, ACTION).
                        Auto-detected if omitted.
            link_mode: "APPEND" (default, full copy) or "LINK" (shared reference).
            location: Optional [x, y, z] world position for COLLECTION and OBJECT assets.
        """
        params = {
            "library_name": library_name,
            "asset_name": asset_name,
            "link_mode": link_mode,
        }
        if asset_type:
            params["asset_type"] = asset_type
        if location is not None:
            params["location"] = tuple(location)
        return send_code(toolcode_format_call(_TOOL_CALL, params), strict_json=True)
