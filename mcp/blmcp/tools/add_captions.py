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
        replace_edited: bool = False,
        animation: str = "STYLE",
        position: str = "BOTTOM",
        margin: float = 0,
        speakers: bool = False,
        speaker_labels: bool = False,
        speaker_settings: list[dict[str, str]] | None = None,
        case: str = "STYLE",
        font: str | None = None,
        bold: bool | None = None,
        italic: bool | None = None,
        size: float = 1.0,
        text_color: str | list[float] | None = None,
        highlight_color: str | list[float] | None = None,
    ) -> dict[str, object]:
        """
        Caption sound or movie strips in the Video Sequencer: their speech becomes styled
        text strips (one per caption line, in sync) on a channel named "Captions".

        Every given strip gets the captions of the part of its media it shows (each piece of a
        cut clip, a clip used twice).  A movie strip is heard through the sound strip of the same
        file over its time.  Muted and retimed strips, and strips with no speech or no
        transcript, are skipped and named in ``strips_skipped``.  Running it again replaces the
        captions over those strips, but keeps captions edited by hand (``kept_edited``) unless
        ``replace_edited``; it is one undo step.  Animation, position and the style choices (case, font, bold, italic, size, colours) work with every style. Word highlights follow the caption's font, bold, italic, size and position by themselves.

        Args:
            strips: Names of the sound / movie strips to caption (none: the selected ones).
            style: CLEAN, BOLD_POP, WORD_HIGHLIGHT (the spoken word lights up) or BOXED.
            language: Spoken language code ("en", "es"...) or "auto".
            transcript_path: A transcript JSON file (bfa_coworker.transcript/1) to use
                instead of transcribing.
            replace_edited: Also replace captions changed by hand since they were made.
            animation: STYLE (the style's own: BOLD_POP pops, the others don't move), NONE,
                FADE, SLIDE_UP, TYPEWRITER (typed out letter by letter, one line per caption)
                or POP.
            position: BOTTOM, TOP (e.g. off the UI a tutorial shows at the bottom) or MIDDLE.
            margin: Distance from that edge in percent of the frame's height, 0-45 (0: the
                safe area of a wide or vertical frame).
            speakers: One caption channel per sound file (one file per speaker), named after
                the speaker; their captions may overlap in time.
            speaker_labels: Each caption starts with its speaker's name ("Ana: ...").
            speaker_settings: Per speaker ``{"strip": a strip of theirs, "name", "style",
                "position"}`` (each but "strip" optional); turns ``speakers`` on.
            case: Letter case of the caption text: STYLE (the style's own: capitals for BOLD_POP,
                as spoken for the others), AS_SPOKEN, UPPER, LOWER or TITLE.
            font: A font already in this file (its name) or the path of a font file; none: the
                style's own (Blender's bundled Inter).
            bold: True / False; none: as the style has it (bold only for BOLD_POP).
            italic: True / False; none: as the style has it (upright).
            size: A scale of the style's own text size, 0.25-4 (1: as the style has it).
            text_color: The text colour: "#RRGGBB" as seen on screen, or 3-4 numbers 0-1 (Blender's
                own values); none: the style's.
            highlight_color: The same for the spoken word of WORD_HIGHLIGHT; none: the style's.

        Returns:
            ``lines_created``, ``channel``, ``first_frame`` / ``last_frame``, the style,
            ``strips_captioned`` (names), ``strips_skipped`` (``{strip, reason}``: "no speech",
            "silent", "retimed", "no transcript" or "muted") and ``kept_edited``; with speakers
            ``speakers`` (``{name, channel, lines_created, style, position}``); ``message`` says it all
            in a sentence.  ``status`` "error" with a ``message`` saying what's wrong.
        """
        return send_code(
            toolcode_format_call(
                _TOOL_CALL,
                Params(
                    strips=tuple(strips or ()),
                    style=style,
                    language=language,
                    transcript_path=transcript_path,
                    replace_edited=bool(replace_edited),
                    animation=animation,
                    position=position,
                    margin=margin,
                    speakers=bool(speakers),
                    speaker_labels=bool(speaker_labels),
                    speaker_settings=tuple(dict(s) for s in (speaker_settings or ()) if isinstance(s, dict)),
                    case=case,
                    font=font,
                    bold=bold,
                    italic=italic,
                    size=size,
                    text_color=text_color if text_color is None or isinstance(text_color, str) else tuple(text_color),
                    highlight_color=(highlight_color if highlight_color is None or isinstance(highlight_color, str)
                                     else tuple(highlight_color)),
                ),
            ),
            strict_json=True,
        )
