# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

# pylint: disable=C0114  # See tool doc-string.

__all__ = (
    "register",
)

import base64
import json
import os

from blmcp.tools_helpers import (
    toolcode_format_call,
    toolcode_load_from_filepath,
    toolcode_wrap_with_calling_convention,
)
from blmcp.tools_helpers.connection import send_code
from blmcp.tools.render_vse_preview_toolcode import Params, _IMAGE_SIZE_LIMIT_IN_BYTES
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import (  # pylint: disable=import-error,no-name-in-module
    CallToolResult,
    ImageContent,
    TextContent,
    ToolAnnotations,
)

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def preview_content(response: dict[str, object]) -> tuple[dict[str, object], bytes | None]:
    """The tool's answer from the bridge *response*: ``(result, jpeg bytes or None)``.

    The image is read from the file Blender wrote (same computer as this server); a
    result without one (an error, a job still running, a file too big) is text only."""
    if response.get("status") != "ok":
        return {"status": "error", "message": str(response.get("message", "Unknown error"))}, None
    result = response.get("result")
    # A step the user approved first can come back wrapped once more ({"status", "result"}).
    while isinstance(result, dict) and "filepath" not in result and isinstance(result.get("result"), dict):
        result = result["result"]
    if not isinstance(result, dict):
        return {"status": "error", "message": "Blender answered without a result."}, None
    path = result.get("filepath")
    if result.get("status") != "ok" or result.get("job_id") or not isinstance(path, str):
        return result, None
    try:
        if os.path.getsize(path) > _IMAGE_SIZE_LIMIT_IN_BYTES:
            return result, None
        with open(path, "rb") as fh:
            return result, fh.read()
    except OSError as ex:
        out = dict(result)
        out["message"] = "{:s} The image could not be read here ({:s}).".format(
            str(result.get("message", "")), str(ex)).strip()
        return out, None


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Render Sequencer Preview",
            readOnlyHint=True,
        )
    )
    def render_vse_preview(
        frame: int | None = None,
        max_size: int = 1024,
        scene_name: str = "",
    ) -> CallToolResult:
        """
        Render one frame of the Video Sequencer's output and return it as an image to look at.

        Use it to check an edit: a title's position, what plays at a cut, a colour. The
        scene's render settings are left as they were; the image is a JPEG in Coworker's
        temp folder (``filepath``).

        Args:
            frame: The frame to render (default: the current frame).
            max_size: Longest side of the image in pixels (64-2048, default 1024).
            scene_name: Another scene (default: the scene the Sequencer shows).

        Returns:
            The image, and ``status``, ``scene``, ``frame``, ``filepath``, ``width`` /
            ``height``, ``message`` (a ``job_id`` instead when the render takes long:
            wait with ``get_job_status``).
        """
        p = Params(frame=frame, max_size=int(max_size or 0), scene_name=scene_name)
        result, image = preview_content(send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True))
        content: list[TextContent | ImageContent] = [
            TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
        if image is not None:
            content.append(ImageContent(type="image", data=base64.b64encode(image).decode("ascii"),
                                        mimeType="image/jpeg"))
        return CallToolResult(content=content, structuredContent=result,
                              isError=result.get("status") == "error")
