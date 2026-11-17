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
from blmcp.tools.get_transcript_text_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Transcript Text",
            readOnlyHint=True,
        )
    )
    def get_transcript_text(
        strip: str | None = None,
    ) -> dict[str, object]:
        """
        Read a Sequencer clip's transcript as text: what it says, as it plays on the
        timeline now (cuts already made are left out), one line per sentence with a
        line number and its time. Use it to find a part ("the bit about pricing") before
        apply_transcript_edit. The clip must have been transcribed (transcribe_strip).

        Args:
            strip: The sound or movie strip (none: the active strip, or the only clip
                with a transcript).

        Returns:
            ``lines`` ([{line, time, frame, text}]), ``text`` (the whole text, each line
            starting with its time mark), ``strip``, ``media``, ``text_block`` (the Text
            Editor text of it, if open) and ``pending_edits`` (it has edits not applied).
        """
        p = Params(strip=strip or "")
        return send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True)
