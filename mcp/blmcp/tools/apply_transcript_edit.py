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
from blmcp.tools.apply_transcript_edit_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Apply Transcript Edit",
            readOnlyHint=False,
        )
    )
    def apply_transcript_edit(
        strip: str | None = None,
        cut_lines: list[int] | None = None,
        text: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, object]:
        """
        Edit a Sequencer clip by editing its transcript (Edit by Transcript), one undo step.
        Read it first with get_transcript_text. Words taken out are cut from the whole edit
        (picture, sound and every other track together, the gap closed, captions trimmed);
        lines moved to another place move their clips; a changed word changes only the
        captions. Text with no audio behind it (new words, a line rewritten so it no longer
        matches what is said) cuts nothing and is listed in ``flags``. A locked strip in the
        way: nothing changes, ``refused`` names it.

        Args:
            strip: The sound or movie strip (none: the active strip, or the only clip with
                a transcript).
            cut_lines: Line numbers (from get_transcript_text) to cut whole. Simplest for
                "cut the part about ...".
            text: Or the whole edited text (get_transcript_text's ``text``, changed): delete
                words or lines, move lines, fix words. Time marks may be left or dropped.
            dry_run: Only say what would change (``ops``), change nothing.

        Returns:
            ``status`` (ok / nothing / refused / error), ``message``, ``cut_words``,
            ``seconds_removed``, ``moved``, ``retexted``, ``captions_updated``, ``flags``
            ([{line, text, reason, message}]), ``ops`` (the edit list), ``new_names`` (strip ->
            its pieces), ``text_now`` (the transcript as it plays now).
        """
        p = Params(strip=strip or "", cut_lines=tuple(int(n) for n in (cut_lines or ())),
                   text=text, dry_run=bool(dry_run))
        return send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True)
