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
        version: str | None = None,
        lines: list[dict[str, object]] | None = None,
        confirm_large_cut: bool = False,
    ) -> dict[str, object]:
        """
        Edit a Sequencer clip by editing its transcript (Edit by Transcript), one undo step.
        Read it first with get_transcript_text. Words taken out are cut from the whole edit
        (picture, sound and every other track together, the gap closed, captions trimmed);
        lines moved to another place move their clips; a changed word changes only the
        captions. Text with no audio behind it (new words, a line rewritten so it no longer
        matches what is said, a repeated word or line the text can't tell apart) cuts nothing
        and is listed in ``flags``. A locked strip in the way: nothing changes, ``refused``
        names it. Nothing is cut by guessing: only text you removed, matched to its words.

        Args:
            strip: The sound or movie strip (none: the active strip, or the only clip with
                a transcript).
            cut_lines: Line numbers (from get_transcript_text) to cut whole. Simplest for
                "cut the part about ...". Send it with ``version`` (get_transcript_text's
                ``version``) or give ``lines`` instead; if the text changed since you read it
                the answer is ``refused`` with the current ``text_now``. Not together with ``text``.
            text: Or the whole edited text (get_transcript_text's ``text``, changed): delete
                words or lines, move lines, fix words. Keep each line's time mark: it tells the
                line (a repeated sentence) apart. Not together with ``cut_lines``.
            dry_run: Only say what would change (``ops``, ``seconds``), change nothing, create nothing.
            version: The ``version`` of the text you read (see cut_lines).
            lines: Lines to cut with what you read: ``[{"line": 3, "text": "..."}]`` (the same
                check as ``version``, line by line).
            confirm_large_cut: An edit that cuts more than half of the words or the time is
                ``refused`` (``needs_confirm``) unless this is true: only after the user agreed.

        Returns:
            ``status`` (ok / nothing / refused / error), ``message``, ``cut_words``,
            ``seconds`` (what the edit takes out), ``seconds_removed``, ``moved``, ``retexted``,
            ``captions_updated``, ``flags`` ([{line, text, reason, message}]), ``ops`` (the edit
            list), ``needs_confirm``, ``new_names`` (strip ->
            its pieces), ``text_now`` and ``version`` (the transcript as it plays now).
        """
        p = Params(strip=strip or "", cut_lines=tuple(int(n) for n in (cut_lines or ())),
                   text=text, dry_run=bool(dry_run), version=version or None,
                   lines=tuple(dict(x) for x in (lines or ())), confirm_large_cut=bool(confirm_large_cut))
        return send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True)
