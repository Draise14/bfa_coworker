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
from blmcp.tools.bake_textures_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Bake Textures",
            readOnlyHint=False,
            # It overwrites the target images' pixels outside the turn's undo step.
            destructiveHint=True,
        )
    )
    def bake_textures(
        bake_type: str = "DIFFUSE",
        object_names: list[str] | None = None,
        margin: int = -1,
    ) -> dict[str, object]:
        """
        Bake textures with Cycles in the background; returns a ``job_id`` at once.

        Each object needs a UV map and, in every material, an *active* Image Texture
        node holding the image to bake into (Blender's own bake workflow); the error
        says what to add when one is missing. The engine is switched to Cycles for the
        bake and back afterwards; the scene's Bake settings (passes, samples) apply.
        Wait for the result with ``get_job_status(job_id)``.

        The bake **overwrites the pixels of the target images**, and Revert / Undo
        can't bring them back (the bake runs outside the turn's undo step): bake into
        new images, or save the old ones first, when they matter.

        Args:
            bake_type: COMBINED, DIFFUSE, GLOSSY, TRANSMISSION, ROUGHNESS, EMIT, NORMAL,
                AO, SHADOW, POSITION, UV or ENVIRONMENT.
            object_names: Mesh objects to bake (empty = the selected meshes).
            margin: Pixels to extend the bake past UV island edges (-1 = the scene's).

        Returns:
            ``status``, ``job_id``, the ``objects`` and target ``images``, and a ``message``.
        """
        p = Params(bake_type=bake_type, object_names=list(object_names or []), margin=margin)
        return send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True)
