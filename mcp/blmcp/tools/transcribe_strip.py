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
from blmcp.tools.transcribe_strip_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Transcribe Strip",
            readOnlyHint=True,
        )
    )
    def transcribe_strip(
        strip: str,
        language: str = "",
        limit: int = 150,
        offset: int = 0,
        prompt: str = "",
        model: str = "",
    ) -> dict[str, object]:
        """
        The spoken words of a Sequencer sound or movie strip, each with its start and end.

        Transcribes the strip's media file locally with whisper.cpp (or the Remote
        provider when the user chose that in Preferences > Coworker > Speech) and caches
        the transcript next to the .blend, so the next call answers at once. A long clip
        answers with a ``job_id`` instead: wait for it with ``get_job_status(job_id)``,
        then call this again (it is cached by then).

        Only the words where the strip is visible are returned, a page at a time.

        Args:
            strip: The strip's name (a SOUND or MOVIE strip).
            language: Language code such as "en" or "de", or "auto" (default: the Speech setting).
            limit: Words per answer (default 150, at most 2000; 0 = no word list, the plain text).
            offset: Skip this many words (use ``next_offset`` from the previous answer).
            prompt: Names and terms to spell right (e.g. "Bforartists, Blender").
            model: Speech model (tiny, base, small, medium, large-v3-turbo; default: the Speech setting).

        Returns:
            ``words``: one row per word, ``[w, start, end, frame_start, frame_end]``
            (``columns``): start / end in seconds of the media file, frames on the
            timeline. Also ``word_count``, ``next_offset`` when there are more,
            ``language``, ``model``, ``cached``, ``transcript_path``; or ``job_id``
            while a long clip is transcribed; or a ``message`` saying what is missing
            (e.g. the speech server isn't downloaded).
        """
        p = Params(strip=strip, language=language, limit=int(limit), offset=int(offset),
                   prompt=prompt, model=model)
        return send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True)
