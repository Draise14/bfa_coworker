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
from blmcp.tools.get_vse_strip_detail_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Sequencer Strip Detail",
            readOnlyHint=True,
            idempotentHint=True,
        )
    )
    def get_vse_strip_detail(strip_name: str, scene_name: str = "") -> dict[str, object]:
        """
        Everything about one Video Sequencer strip.

        Frames and channel, content offsets, opacity and blend mode, transform and crop,
        the source file (movie, image, sound, scene), text and its style, colour, volume,
        speed (retiming keys), effect inputs, modifiers (colour balance values), proxy.

        Args:
            strip_name: The strip's exact name (get_vse_strips_summary lists them).
            scene_name: Another scene (default: the scene the Sequencer shows).

        Returns:
            ``status``, ``scene``, ``strip`` (the details), ``message``; when the name is
            not found, ``message`` lists the nearest names.
        """
        p = Params(strip_name=strip_name, scene_name=scene_name)
        return send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True)
