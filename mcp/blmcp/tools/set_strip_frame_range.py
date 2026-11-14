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
from blmcp.tools.set_strip_frame_range_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Set Strip Frame Range",
            destructiveHint=True,
            idempotentHint=True,
        )
    )
    def set_strip_frame_range(
        strip_name: str,
        start: int | None = None,
        end: int | None = None,
        channel: int | None = None,
        trim: bool = False,
        scene_name: str = "",
    ) -> dict[str, object]:
        """
        Move or trim a Video Sequencer strip, or put it on another channel (one undo step).

        "Move the title strip to frame 50" is ``start=50``. Give any of start, end, channel:
        - ``start`` alone moves the whole strip so it begins there (its length is kept);
        - ``end`` sets the frame after its last one (trims or extends the end);
        - ``trim=True`` makes ``start`` move only the left handle (the content stays put);
        - ``channel`` moves it to that channel (1-128).
        A change that would overlap another strip is refused and names a free channel.

        Args:
            strip_name: The strip's exact name (get_vse_strips_summary lists them).
            start: New first frame.
            end: New end frame (exclusive: a strip on frames 1-10 has end 11).
            channel: New channel.
            trim: Trim the start instead of moving the strip.
            scene_name: Another scene (default: the scene the Sequencer shows).

        Returns:
            ``status``, ``scene``, ``strip``, ``before`` / ``after`` (start, end, channel),
            ``undo_step`` (one named step was pushed), ``message``.
        """
        p = Params(strip_name=strip_name, start=start, end=end, channel=channel, trim=bool(trim),
                   scene_name=scene_name)
        return send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True)
