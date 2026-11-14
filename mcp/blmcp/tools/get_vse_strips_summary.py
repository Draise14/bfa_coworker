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
from blmcp.tools.get_vse_strips_summary_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Sequencer Strips Summary",
            readOnlyHint=True,
            idempotentHint=True,
        )
    )
    def get_vse_strips_summary(
        channel: int = 0,
        strip_type: str = "",
        frame_start: int | None = None,
        frame_end: int | None = None,
        name_contains: str = "",
        limit: int = 50,
        scene_name: str = "",
    ) -> dict[str, object]:
        """
        List the Video Sequencer's strips: name, type, channel, frames, mute, lock, selection.

        Use it to find a strip by name or to see what is on a channel ("what's on channel 3?").
        Frames are Blender's: ``start`` is the first frame shown, ``end`` the frame after the
        last one (a strip on frames 1-10 has start 1, end 11). Strips inside a meta strip
        name it in ``meta``.

        Args:
            channel: Only this channel (0 = all).
            strip_type: Only this type, e.g. TEXT, COLOR, MOVIE, IMAGE, SOUND, SCENE, META.
            frame_start: Only strips still showing at or after this frame.
            frame_end: Only strips starting at or before this frame.
            name_contains: Only names containing this text (any case).
            limit: At most this many strips (default 50, at most 500).
            scene_name: Another scene (default: the scene the Sequencer shows).

        Returns:
            ``status``, ``scene``, ``total`` / ``matched`` / ``shown``, ``more`` (strips
            left out by ``limit``), ``strips`` (rows sorted by channel and start), ``message``.
        """
        p = Params(
            channel=int(channel or 0), strip_type=strip_type, frame_start=frame_start, frame_end=frame_end,
            name_contains=name_contains, limit=int(limit or 0), scene_name=scene_name,
        )
        return send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True)
