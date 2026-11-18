# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

__all__ = (
    "register",
)

from blmcp.tools_helpers import (
    toolcode_format_call,
    toolcode_load_from_filepath,
    toolcode_wrap_with_calling_convention,
)
from blmcp.tools_helpers.connection import send_code
from blmcp.tools.reframe_strips_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Reframe Strips",
            readOnlyHint=False,
        )
    )
    def reframe_strips(
        layout: str = "",
        strips: list[str] | None = None,
        swap: bool = False,
    ) -> dict[str, object]:
        """
        Change the vertical layout of movie, image or scene strips (Reframe), one undo step.
        This changes the scene (strips are added and removed), so it waits while a render or
        bake runs.

        Replaces whatever layout a strip had (a split's second strip, a blur, follow keys) with
        the new one; going through several layouts ends with the same strips as the last one
        alone. A split is two ordinary strips of the same source, the top one (the strip itself)
        and its duplicate below, each with its own crop and transform, so either can be moved.
        Captions, their word strips and the audio are never touched; Remove Silences and Edit by
        Transcript cut both parts together. Each strip's layout is stored on it
        (``coworker_layout``).

        Args:
            layout: ``full`` (the shot's centre fills the frame), ``full_blur`` (the whole shot
                over a blurred copy), ``full_tracked`` (follows the subject; the layout is made
                without a model, the tracking needs a vision-capable one), ``split_half`` (two
                crops, 50 / 50), ``split_top`` (two thirds on top, one third below),
                ``split_bottom`` (one third on top, two thirds below). ``track_all`` (each part of
                a split follows its own subject) needs subject finding and is not available yet.
                Empty with ``swap=true``: only swap.
            strips: Strip names (a split's either part stands for both). Empty: the selected strips.
            swap: Exchange which framing of the picture is shown top and bottom in the splits named
                (after building ``layout`` when one is given).

        Returns:
            ``status`` (ok / nothing / error), ``message``, ``layout``, ``changed`` (strips given
            the layout), ``created`` / ``removed`` (strip names), ``unchanged`` (already in that
            layout), ``skipped`` ([{strip, reason}]), ``swapped``, ``tracking`` and ``scene``.
        """
        p = Params(layout=layout, strips=tuple(strips or ()), swap=bool(swap))
        return send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True)
