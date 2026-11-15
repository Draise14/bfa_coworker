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
from blmcp.tools.render_viewport_to_path_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Render Viewport to Path",
            readOnlyHint=True,
        )
    )
    def render_viewport_to_path(
            output_path: str, background: bool = False, quality: str = "preview",
    ) -> dict[str, object]:
        """
        Render the scene's camera view to *output_path*.

        ``quality="preview"`` (the default) is for looking and checking your work: at
        most 1024 px, few samples, a Cycles time limit, Simplify on, no compositing,
        Sequencer, motion blur or persistent data; the scene's settings are put back
        afterwards. ``quality="viewport"`` is quicker still: solid shading (OpenGL)
        from the camera, no materials or lighting. ``quality="final"`` renders with the
        scene's own settings (full size and samples, can take minutes or hours): use it
        only when the user asked for the real render.

        A render that takes longer than about 45 s answers with a ``job_id`` instead
        of the file, and carries on: wait for it with ``get_job_status(job_id)``.
        For a final render you know is long, pass ``background=True`` to get the
        ``job_id`` at once.

        Args:
            output_path: File name for the image (it is written to Coworker's temp folder).
            background: Start the render and return a ``job_id`` right away.
            quality: ``"preview"`` (default), ``"viewport"`` or ``"final"``.

        Returns:
            ``status``; ``filepath`` (where the image is, or will be, written); a
            ``job_id`` when the render goes on in the background; ``quality`` and
            ``caps`` (what the preview limited); ``message`` (also on an error: the
            quality and limits it ran with).
        """
        p = Params(output_path=output_path, background=bool(background), quality=str(quality or "preview"))
        code = toolcode_format_call(_TOOL_CALL, p)
        return send_code(code, strict_json=True)
