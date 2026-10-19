# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
How a running turn shows its status, shared by the panel chat and the GPU
chat (Tier 4, task "GPU 2"): the animated braille "thinking" spinner, the
phase wording ("Reading your message" / "Dreaming, one moment" /
"Thinking") and turn durations.  Moved unchanged from ``ui_chat``.
Free of ``bpy``.
"""

from __future__ import annotations

import random

__all__ = (
    "fmt_duration",
    "phase_text",
    "spinner_char",
    "spinner_glyph",
)


# Animated "thinking" spinner frames, assembled from named phase groups: a
# braille dot grows into a full cell and shrinks back (diagonal and bar
# variants), the classic multi-dot "wormy" orbit, a travelling "wavy" ripple,
# and a "block" gap rotating through a full cell.  Rather than replaying the
# same order every loop, the groups are shuffled per cycle (see
# _ordered_phases) so a long wait keeps looking new.  Written as \u escapes so
# the source stays ASCII; rendered as glyphs by Blender's UI.
_EXPAND: tuple[str, ...] = (
    "\u2801", "\u2803", "\u2807", "\u280f", "\u281f", "\u283f",
)
_CONTRACT: tuple[str, ...] = (
    "\u281f", "\u280f", "\u2807", "\u2803", "\u2801",
)
# "Wormy": the classic multi-dot braille orbit -- dots chase each other around.
_WORMY: tuple[str, ...] = (
    "\u280b", "\u2819", "\u2839", "\u2838", "\u283c",
    "\u2834", "\u2826", "\u2827", "\u2807", "\u280f",
)
# "Wavy": a ripple travelling along the braille columns.
_WAVY: tuple[str, ...] = (
    "\u2809", "\u280b", "\u2819", "\u281a", "\u2812", "\u2802", "\u2802",
    "\u2812", "\u2832", "\u2834", "\u2826", "\u2816", "\u2812", "\u2810",
)
# "Growing bar": fills the left column top-to-bottom, then the right column
# bottom-to-top, then unwinds -- a bar that grows out and collapses back.
_GROW: tuple[str, ...] = (
    "\u2801", "\u2803", "\u2807", "\u2827", "\u2837",
    "\u283f", "\u2837", "\u2827", "\u2807", "\u2803",
)
# "Block": a full cell with a gap that rotates around it.
_BLOCK: tuple[str, ...] = (
    "\u28ff", "\u28f7", "\u28ef", "\u28df", "\u287f",
    "\u28bf", "\u28fb", "\u28fd", "\u28fe", "\u28ff",
)
# "Sweep": a diagonal blade that grows corner-to-corner, then melts away.
_SWEEP: tuple[str, ...] = (
    "\u2800", "\u2840", "\u28c0", "\u28c4", "\u28e4", "\u28e6",
    "\u28f6", "\u28f7", "\u28ff", "\u28bf", "\u283f", "\u283b",
    "\u281b", "\u2819", "\u2809", "\u2808", "\u2800",
)
_PHASES: tuple[tuple[str, ...], ...] = (
    _EXPAND, _CONTRACT, _WORMY, _WAVY, _GROW, _BLOCK, _SWEEP,
)
_PHASE_TOTAL = sum(len(_phase) for _phase in _PHASES)


def _ordered_phases(cycle: int) -> list[tuple[str, ...]]:
    """Phase play order for cycle number *cycle*, deterministically shuffled.

    Seeding on the cycle number gives every widget drawn in the same frame the
    same order, and a fresh shuffle each loop -- variety with no shared mutable
    state and no mid-frame flicker.
    """
    order = list(_PHASES)
    random.Random(cycle * 2654435761 & 0xFFFFFFFF).shuffle(order)
    return order


def spinner_glyph(tick: int) -> str:
    """Return the spinner glyph for monotonic *tick* (wraps across all phases)."""
    cycle, pos = divmod(tick, _PHASE_TOTAL)
    for phase in _ordered_phases(cycle):
        if pos < len(phase):
            return phase[pos]
        pos -= len(phase)
    return _PHASES[0][0]  # Unreachable: pos < _PHASE_TOTAL always resolves.


def spinner_char(state) -> str:
    """Return the current animated spinner glyph for *state*."""
    return spinner_glyph(int(getattr(state, "thinking_dots", 0) or 0))


def phase_text(state) -> str:
    """Human label for the pre-first-token activity phase.

    So a send does not read as "frozen": "Reading your message" while the
    prompt is assembled, "Dreaming, one moment" while a local server loads,
    then "Thinking" once tokens arrive.
    """
    phase = str(getattr(state, "turn_phase", "") or "")
    if phase == "reading":
        return "Reading your message"
    if phase == "warming":
        return "Dreaming, one moment"
    return "Thinking"


def fmt_duration(seconds: float) -> str:
    """Format a duration for display, rolling up into minutes and hours.

    Long local turns run into the minutes, so raw seconds ("742s") read badly.
    Examples: ``45s``, ``2m 05s``, ``18m``, ``1h 02m``.  Sub-second values
    render as ``0s``; negatives are clamped to ``0s``.
    """
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return ""
    if total < 0:
        total = 0
    if total < 60:
        return "{:d}s".format(total)
    if total < 3600:
        mins, secs = divmod(total, 60)
        return "{:d}m {:02d}s".format(mins, secs) if secs else "{:d}m".format(mins)
    hours, rem = divmod(total, 3600)
    mins = rem // 60
    return "{:d}h {:02d}m".format(hours, mins)
