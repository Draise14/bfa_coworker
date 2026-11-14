# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Select chat text with the mouse (#451, #455).

The chat view draws each selectable text -- your message, each block of a
reply, the live Thinking box -- as a **unit**, and the canvas keeps every line
it drew of one as a :class:`layout.TextLine`.  A selection is a
:class:`ChatSelection` from an *anchor* to a *focus*, each a position
``(turn, unit, offset)``; positions compare as tuples, which is reading order
(older turns first, then units top to bottom, then characters).

* :func:`point_to_pos` -- the position under, or nearest to, a point (the
  prompt's ``InputBox.place_cursor`` generalised to many lines and fonts);
* :func:`highlight_rects` -- where the highlight goes, line by line, and at
  which command of the frame (behind the text);
* :func:`flow_tokens` -- the text a run of markdown spans shows (markers
  stripped, spaces as drawn), with each token's offset in it;
* :func:`word_bounds` -- the word around an offset, for a double-click.

Free of ``bpy``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .layout import Measure, Rect, TextLine

__all__ = (
    "CHIP_PAD",
    "ChatSelection",
    "flow_tokens",
    "highlight_rects",
    "join_units",
    "offset_at",
    "over_text",
    "point_to_pos",
    "word_bounds",
    "x_at",
)

Pos = tuple  # (turn, unit, offset); unit is a (section, block) tuple
CHIP_PAD = 2.0   # an inline-code run's background reaches this far past its text on each side


@dataclass
class ChatSelection:
    """From *anchor* (where the press was) to *focus* (where the mouse is)."""

    anchor: Pos
    focus: Pos
    sig: Any = None             # what the selected turns were when it was made (see ChatView)

    def ordered(self) -> tuple[Pos, Pos]:
        return (self.anchor, self.focus) if self.anchor <= self.focus else (self.focus, self.anchor)

    @property
    def empty(self) -> bool:
        return self.anchor == self.focus


def flow_tokens(spans: Iterable[Any]) -> tuple[str, list[tuple[str, Any, int]]]:
    """The text styled *spans* show, and its tokens as ``(token, span, offset)``.

    The same tokens ``ChatView._flow`` lays out: words, and each run of white space as
    one space -- none before the first word."""
    out: list[tuple[str, Any, int]] = []
    pos = 0
    for span in spans:
        for token in re.split(r"(\s+)", span.text):
            if not token:
                continue
            if token.isspace():
                if not out:
                    continue
                token = " "
            out.append((token, span, pos))
            pos += len(token)
    return "".join(t for t, _s, _o in out), out


def word_bounds(text: str, pos: int) -> tuple[int, int]:
    """The word (a run without white space) at *pos* in *text*; on white space, that run."""
    pos = max(0, min(pos, len(text)))
    if pos < len(text) and text[pos].isspace() and (pos == 0 or text[pos - 1].isspace()):
        a = b = pos
        while a > 0 and text[a - 1].isspace() and text[a - 1] != "\n":
            a -= 1
        while b < len(text) and text[b].isspace() and text[b] != "\n":
            b += 1
        return a, b
    a = b = pos
    while a > 0 and not text[a - 1].isspace():
        a -= 1
    while b < len(text) and not text[b].isspace():
        b += 1
    return a, b


# -- geometry ---------------------------------------------------------------------------

def x_at(line: TextLine, offset: int, measure: Measure) -> float:
    """The x of the caret before *offset* (clamped to the line)."""
    for off, text, x, _w, font, size, _cmd in line.pieces:
        if offset <= off + len(text):
            return x + measure(text[:max(0, offset - off)], font, size)
    return line.right


def offset_at(line: TextLine, x: float, measure: Measure) -> int:
    """The character boundary nearest *x* on *line*."""
    if not line.pieces:
        return line.start
    pieces = line.pieces
    for n, (off, text, px, w, font, size, _cmd) in enumerate(pieces):
        if x >= px + w and n < len(pieces) - 1:
            continue
        lo, hi = 0, len(text)        # the first boundary at or right of x (widths only grow)
        while lo < hi:
            mid = (lo + hi) // 2
            if px + measure(text[:mid], font, size) < x:
                lo = mid + 1
            else:
                hi = mid
        if lo > 0:
            left = px + measure(text[:lo - 1], font, size)
            right = px + measure(text[:lo], font, size)
            if x - left < right - x:
                lo -= 1
        return off + lo
    return line.end


def _visible(line: TextLine) -> tuple[float, float] | None:
    """(bottom, top) of the part of *line* inside its clip, or None."""
    top, bottom = line.top, line.bottom
    if line.clip is not None:
        top, bottom = min(top, line.clip.top), max(bottom, line.clip.y)
    return (bottom, top) if top > bottom else None


def point_to_pos(lines: Iterable[TextLine], x: float, y: float, measure: Measure) -> Pos | None:
    """The position at (x, y): on the line under it, else on the nearest line on screen
    (above or below, then left or right); None when no line is on screen."""
    best, best_score = None, None
    for line in lines:
        band = _visible(line)
        if band is None:
            continue
        bottom, top = band
        dy = 0.0 if bottom <= y < top else min(abs(y - top), abs(y - bottom))
        dx = 0.0 if line.x <= x <= line.right else min(abs(x - line.x), abs(x - line.right))
        score = (dy, dx)
        if best_score is None or score < best_score:
            best, best_score = line, score
    if best is None:
        return None
    return (best.key[0], best.key[1], offset_at(best, x, measure))


def over_text(lines: Iterable[TextLine], x: float, y: float) -> bool:
    """Is (x, y) on a line of selectable text?"""
    for line in lines:
        band = _visible(line)
        if band is not None and band[0] <= y < band[1] and line.x <= x <= line.right:
            return True
    return False


def highlight_rects(lines: Iterable[TextLine], selection: ChatSelection,
                    measure: Measure) -> list[tuple[int, Rect, tuple]]:
    """``(command index, rect, key)`` per highlight: one per selected line, inserted at
    the line's first command (behind its text), and one more over each selected run that
    has a background of its own (inline code)."""
    if selection.empty:
        return []
    lo, hi = selection.ordered()
    out: list[tuple[int, Rect, tuple]] = []
    for line in lines:
        key = line.key
        if key < lo[:2] or key > hi[:2]:
            continue
        a = lo[2] if key == lo[:2] else -1
        b = hi[2] if key == hi[:2] else 1 << 30
        s, e = line.start, line.end
        if a > e or b < s:
            continue
        ca, cb = max(a, s), min(b, e)
        # The selection goes on past the end: a little more, for the space or line break there.
        tail = line.size * 0.3 if b > e and (a < e or s == e) else 0.0
        if ca >= cb and not tail:
            continue
        xa = x_at(line, ca, measure)
        xb = (x_at(line, cb, measure) if cb > ca else xa) + tail
        out.append((line.cmd, Rect(xa, line.bottom, xb - xa, line.top - line.bottom), key))
        for off, text, _x, _w, _font, _size, cmd in line.pieces:
            if cmd < 0:
                continue
            pa, pb = max(ca, off), min(cb, off + len(text))
            if pa < pb:         # over its background too where it reaches that end, or its edge shows
                x0 = x_at(line, pa, measure) - (CHIP_PAD if pa == off else 0.0)
                x1 = x_at(line, pb, measure) + (CHIP_PAD if pb == off + len(text) else 0.0)
                out.append((cmd, Rect(x0, line.bottom, x1 - x0, line.top - line.bottom), key))
    return out


def join_units(units: Iterable[tuple[int, tuple, str, str]], selection: ChatSelection) -> str:
    """The selected text, from ``(turn, unit, text, kind)`` in reading order: a part of
    the first and last unit, all of those between; blocks apart by a blank line, list
    items by a line break."""
    if selection.empty:
        return ""
    lo, hi = selection.ordered()
    parts: list[str] = []
    prev: tuple | None = None
    for turn, unit, text, kind in units:
        key = (turn, unit)
        if key < lo[:2] or key > hi[:2]:
            continue
        a = lo[2] if key == lo[:2] else 0
        b = hi[2] if key == hi[:2] else len(text)
        piece = text[max(0, a):max(0, b)]
        if not piece:
            continue
        if prev is not None:
            same_list = prev[0] == turn and prev[1][0] == unit[0] and prev[2] == kind == "item"
            parts.append("\n" if same_list else "\n\n")
        parts.append(piece)
        prev = (turn, unit, kind)
    return "".join(parts)
