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
from blmcp.tools.load_asset_in_context_toolcode import Params
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
        object_name: str = "",
        tree_name: str = "",
        import_method: str = "auto",
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
                        Used as the fallback when ``import_method="auto"`` has
                        no asset metadata to consult.
            location: Optional [x, y, z] world position for COLLECTION and OBJECT assets.
            object_name: Explicit target object for MATERIAL / Geometry-Nodes /
                ACTION loads (defaults to the active object). Useful when no
                editor context exists (e.g. background mode).
            tree_name: Explicit target tree for node-group loads: a material
                or scene name (defaults to the active context). For shader
                groups this is a material name (or ShaderNodeTree name); for
                compositor groups a scene name.
            import_method: "auto" (default) = honour the asset's
                ``asset_data.preferred_import_method`` when metadata is
                available, else fall back to ``link_mode``. Explicit
                "append", "link", or "pack" overrides everything.
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
                    object_name=object_name,
                    tree_name=tree_name,
                    import_method=import_method,
                ),
            ),
            strict_json=True,
        )
