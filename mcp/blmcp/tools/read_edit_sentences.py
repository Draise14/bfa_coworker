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
from blmcp.tools.read_edit_sentences_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Read Edit Sentences",
            readOnlyHint=True,
        )
    )
    def read_edit_sentences(
        first: int = 1,
        count: int = 150,
    ) -> dict[str, object]:
        """
        The speech of the whole Sequencer edit as numbered sentences, as it plays now: every sound
        strip, every media file, merged by time. Read it to choose pieces for propose_highlights.
        Needs transcripts (transcribe_strip / Auto Captions). Changes nothing.

        Args:
            first: The sentence number to start at (default 1).
            count: How many sentences (default 150); ``next_first`` says where the next page starts.

        Returns:
            ``text`` (one line per sentence: ``S12 [0:41] words``), ``total``, ``first``, ``last``,
            ``next_first`` (None on the last page), ``version`` (send it back to propose_highlights),
            ``windows`` (stretches that fit one request) and ``report`` (what was skipped and why).
        """
        p = Params(first=int(first), count=int(count))
        return send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True)
