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
from blmcp.tools.remove_silences_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Remove Silences",
            readOnlyHint=False,
        )
    )
    def remove_silences(
        strips: list[str] | None = None,
        threshold_db: float = -40.0,
        min_silence: float = 0.4,
        padding: float = 0.15,
        min_keep: float = 0.1,
        close_gaps: bool = True,
        update_scene_end: bool = True,
    ) -> dict[str, object]:
        """
        Cut the quiet parts out of the Sequencer's edit (Remove Silences), one undo step.

        Listens to the named sound strips (a movie strip brings its own sound). With
        sounds from several files, only the time where all of them are quiet goes. The
        same cuts go through every strip at that level, selected or not (picture,
        voice-over, music, titles, captions), so nothing drifts; captions over a cut
        are trimmed, and one left too short to read is removed. When a locked,
        retimed, scene or meta strip is in the way nothing changes: ``refused`` says
        which strip and why. Strips cut in pieces keep their name on the first piece;
        ``new_names`` lists every piece. A second call with the same settings finds
        nothing more to cut.

        Args:
            strips: Sound or movie strips to listen to (empty: the selected ones; nothing
                selected: every sound strip).
            threshold_db: Quieter than this (dB below full scale, -120..0) counts as silence.
            min_silence: Only quiet stretches at least this long (seconds) are cut.
            padding: Quiet time (seconds) kept before and after the sound, so words aren't clipped.
            min_keep: What's left between two cuts goes too when shorter than this (seconds).
            close_gaps: Move everything after each cut left (ripple); off leaves gaps.
            update_scene_end: When the scene ended at the end of the edit, it ends at the new end
                (and the preview range).

        Returns:
            ``status`` (ok / refused / error), ``message``, ``cuts``, ``seconds_removed``,
            ``cut_ranges`` (frames before the cut), ``new_names`` (original strip -> its pieces;
            [] when all of it went), ``end_frame`` (the scene's end now), ``content_end``,
            ``refused`` ([{strip, reason}]), ``problems``, ``captions_trimmed``,
            ``captions_dropped``.
        """
        p = Params(strips=tuple(strips or ()), threshold_db=threshold_db, min_silence=min_silence, padding=padding,
                   min_keep=min_keep, close_gaps=close_gaps, update_scene_end=update_scene_end)
        return send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True)
