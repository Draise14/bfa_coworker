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
from blmcp.tools.reframe_vertical_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Reframe Vertical",
            readOnlyHint=False,
        )
    )
    def reframe_vertical(
        strips: list[str] | None = None,
        mode: str = "CROP",
        in_copy: bool = True,
        resolution: str = "1080x1920",
        reset: bool = False,
        follow: str = "",
        layout: str = "auto",
    ) -> dict[str, object]:
        """
        Make the Sequencer's edit vertical (9:16), one undo step.

        CROP frames each movie, image and scene strip with its own crop and transform
        (no compositor, so playback stays fast); BLUR_FILL gives it an editable "Reframe"
        compositor modifier (Offset X / Y from -1 to 1, Zoom 1 = just covers, Blur Amount,
        Background Dim). Strips that already have a framing keep it unless ``reset``.

        Args:
            strips: Strip names to reframe (empty = every visible movie / image / scene strip).
            mode: CROP (centre crop, fills the frame) or BLUR_FILL (the whole shot over a
                blurred, dimmed copy of itself).
            in_copy: Work in a copy of the scene named "<scene> Vertical" and show it in the
                Sequencer, leaving the 16:9 edit as it is.
            resolution: Output size, e.g. 1080x1920, 720x1280, 2160x3840.
            reset: Give strips that already have a framing fresh framing too.
            follow: "auto", "face" or "screen": find the subject (a face, what moves on screen)
                and key the crop to it; CROP only. Empty: off.
            layout: With follow: "auto" (two crops, top and bottom, when two subjects are apart),
                "single" or "split".

        Returns:
            ``status``, the ``scene`` used (and ``source_scene``, ``copied``), ``mode``,
            ``resolution``, ``strips_changed``, ``strips_kept`` and a ``message``.
        """
        p = Params(strips=list(strips or []), mode=mode, in_copy=in_copy, resolution=resolution, reset=reset, follow=follow, layout=layout)
        return send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True)
