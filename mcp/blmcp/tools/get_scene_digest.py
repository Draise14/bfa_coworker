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
from blmcp.tools.get_scene_digest_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Scene Digest",
            readOnlyHint=True,
        )
    )
    def get_scene_digest(
        since: str = "",
    ) -> dict[str, object]:
        """
        A short summary of the scene (counts, active and selected objects, up to 40 objects)
        with a ``hash``. Call it first instead of a full listing.

        Called again with ``since`` = the hash you got, it answers ``unchanged`` or only
        what changed (objects added, removed, changed), so re-checking the scene is cheap.

        Args:
            since: The ``hash`` from an earlier call (empty = the whole summary).

        Returns:
            Status and the digest: ``hash``, scene, frame, mode, active, and either
            ``unchanged``, ``changes`` or ``objects``.
        """
        return send_code(
            toolcode_format_call(_TOOL_CALL, Params(since=since)),
            strict_json=True,
        )
