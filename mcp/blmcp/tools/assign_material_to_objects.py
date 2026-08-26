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
            title="Assign Material to Objects",
            destructiveHint=True,
        )
    )
    def assign_material_to_objects(
        material_name: str,
        object_names: list[str] = [],
        slot_index: int = 0,
    ) -> dict[str, object]:
        """
        Assign an existing material to one or more objects by name.

        The material must already exist in the scene (load it first with
        load_asset_in_context or create it with setup_pbr_material).

        Args:
            material_name: Name of the material datablock in the scene.
            object_names: List of object names to assign the material to.
                If empty, assigns to the active object.
            slot_index: Material slot index to assign to (default 0 = first slot).

        Returns:
            Status, assigned object list, and any errors.
        """
        return toolcode_format_call(
            _TOOL_CALL,
            send_code,
            material_name=material_name,
            object_names=object_names,
            slot_index=slot_index,
        )
