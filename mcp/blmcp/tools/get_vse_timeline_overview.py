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
from blmcp.tools.get_vse_timeline_overview_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Sequencer Timeline Overview",
            readOnlyHint=True,
            idempotentHint=True,
        )
    )
    def get_vse_timeline_overview(scene_name: str = "") -> dict[str, object]:
        """
        An overview of the Video Sequencer timeline; call it first for any editing request.

        Frame range, fps, resolution, current frame, the channels in use (strip count, name,
        mute, lock), gaps (frames where no picture plays), overlapping strips, markers and
        the active strip.

        Args:
            scene_name: Another scene (default: the scene the Sequencer shows).

        Returns:
            ``status``, ``scene``, ``sequencer_shows`` (the scene the editor shows),
            ``fps``, ``resolution`` [x, y, percent], ``frame_start`` / ``frame_end`` /
            ``current_frame``, ``strip_count``, ``content_range``, ``active_strip``,
            ``channels``, ``gaps`` ([first, last] frames), ``overlaps``, ``markers``, ``message``.
        """
        return send_code(toolcode_format_call(_TOOL_CALL, Params(scene_name=scene_name)), strict_json=True)
