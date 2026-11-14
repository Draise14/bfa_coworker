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
from blmcp.tools.add_captions_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Add Captions",
            readOnlyHint=False,
            destructiveHint=False,
        )
    )
    def add_captions(
        strips: list[str] | None = None,
        style: str = "CLEAN",
        language: str | None = None,
        transcript_path: str | None = None,
    ) -> dict[str, object]:
        """
        Caption sound or movie strips in the Video Sequencer: their speech becomes styled
        text strips (one per caption line, in sync) on a channel named "Captions".

        Running it again on the same clip replaces its captions; it is one undo step.

        Args:
            strips: Names of the sound / movie strips to caption (none: the selected ones).
            style: CLEAN, BOLD_POP, WORD_HIGHLIGHT (the spoken word lights up) or BOXED.
            language: Spoken language code ("en", "es"...) or "auto".
            transcript_path: A transcript JSON file (bfa_coworker.transcript/1) to use
                instead of transcribing.

        Returns:
            ``lines_created``, ``channel``, ``first_frame`` / ``last_frame`` and the style;
            ``status`` "error" with a ``message`` saying what's wrong.
        """
        return send_code(
            toolcode_format_call(
                _TOOL_CALL,
                Params(
                    strips=tuple(strips or ()),
                    style=style,
                    language=language,
                    transcript_path=transcript_path,
                ),
            ),
            strict_json=True,
        )
