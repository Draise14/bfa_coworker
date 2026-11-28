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
from blmcp.tools.make_short_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Make Short",
            readOnlyHint=False,
        )
    )
    def make_short(
        pick: str | int,
        steps: list[str] | None = None,
    ) -> dict[str, object]:
        """
        Build one of the Highlights picks as its own scene (a copy of the edit with its range set
        to the pick, vertical, captioned, ready to render), one undo step. The long edit is never
        changed and the user's view stays on it: tell them the new scene's name. Rendering is
        theirs. The pick is placed again first; one that no longer fits the edit is refused.
        Changes the scene (scenes and strips are added), so it waits while a render or bake runs.

        Args:
            pick: The pick's ``id`` from suggest_highlights / propose_highlights (or its number in
                the list, 1 is the first, or its title).
            steps: The polish steps to run, any of ``silences`` (cut the pauses), ``vertical``
                (9:16), ``captions`` (Auto Captions, needs the transcript), ``hook`` (the hook line
                as a title over the first seconds). Left out: all four; ``[]``: only the copy
                with its range.

        Returns:
            ``scene`` (the short's name), ``frame_start`` / ``frame_end``, ``seconds`` and ``steps``
            (one line per step, a step that could not run says why and the rest went on).
        """
        p = Params(pick=str(pick), steps=None if steps is None else tuple(str(s) for s in steps))
        return send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True)
