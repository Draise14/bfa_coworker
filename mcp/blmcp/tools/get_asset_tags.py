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
            title="Get Asset Tags",
            readOnlyHint=True,
        )
    )
    def get_asset_tags(
        library_name: str,
        asset_name: str,
        asset_type: str = "",
    ) -> dict[str, object]:
        """
        Get detailed tags and metadata for an asset, including node group editor type.

        For NODETREE assets, returns the editor type (GeometryNodeTree, ShaderNodeTree,
        CompositorNodeTree) and other metadata like color tags.

        Args:
            library_name: Name of the asset library containing the asset.
            asset_name: Name of the asset to inspect.
            asset_type: Optional type hint (MATERIAL, NODETREE, OBJECT, WORLD, ACTION).
                        Auto-detected if omitted.

        Returns:
            Dict with tags, editor_type (for node groups), color_tag, and other metadata.
        """
        params = {
            "library_name": library_name,
            "asset_name": asset_name,
        }
        if asset_type:
            params["asset_type"] = asset_type
        return send_code(toolcode_format_call(_TOOL_CALL, params), strict_json=True)
