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
from blmcp.tools.place_asset_in_scene_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Place Asset In Scene",
            readOnlyHint=False,
        )
    )
    def place_asset_in_scene(
        library_name: str,
        asset_name: str,
        asset_type: str = "",
        link_mode: str = "APPEND",
        location: list[float] | None = None,
        rotation: list[float] | None = None,
        scale: list[float] | None = None,
        import_method: str = "auto",
    ) -> dict[str, object]:
        """
        Place a COLLECTION or OBJECT asset at an explicit world transform.

        Use this when the user wants the asset at a specific position,
        rotation, or scale (e.g. "add the brick wall at x=10 facing the
        camera").  For materials, node groups, worlds, or actions use
        ``load_asset_in_context`` instead.

        ``link_mode`` defaults to ``"APPEND"`` (full independent copy,
        positioned directly).  ``"LINK"`` keeps a shared reference: for
        collections this creates an empty + collection instance at the
        requested transform instead of moving the source objects.

        Args:
            library_name: Name of the asset library to load from.
            asset_name: Name of the asset to place.
            asset_type: ``"OBJECT"`` or ``"COLLECTION"`` (auto-detected if omitted).
            link_mode: ``"APPEND"`` (default) or ``"LINK"``. Used as the
                fallback when ``import_method="auto"`` has no asset metadata
                to consult.
            location: Optional [x, y, z] world position.
            rotation: Optional [x, y, z] Euler rotation in **degrees**.
            scale: Optional [x, y, z] scale factors.
            import_method: ``"auto"`` (default) = honour the asset's
                ``asset_data.preferred_import_method`` when metadata is
                available, else fall back to ``link_mode``. Explicit
                ``"append"``, ``"link"``, or ``"pack"`` overrides.

        Returns:
            Status, final transform, and how many objects were affected.
        """
        return send_code(
            toolcode_format_call(
                _TOOL_CALL,
                Params(
                    library_name=library_name,
                    asset_name=asset_name,
                    asset_type=asset_type,
                    link_mode=link_mode,
                    location=tuple(location) if location is not None else None,
                    rotation=tuple(rotation) if rotation is not None else None,
                    scale=tuple(scale) if scale is not None else None,
                    import_method=import_method,
                ),
            ),
            strict_json=True,
        )