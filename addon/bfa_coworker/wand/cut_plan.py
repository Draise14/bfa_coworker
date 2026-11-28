# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Remove Silences across the whole edit (#539): which frames go, and what happens to
every strip, so nothing drifts.  Free of ``bpy``; ``wand/sequencer.py`` reads the
strips into plain dicts, asks here, and then changes the scene.

1. :func:`common_cuts`: with sounds from several files, only the frames where every
   sound that plays there is quiet go (and at least one sound plays there).
2. :func:`plan`: the same cuts for every strip at that level, selected or not.  A
   strip that can't follow (locked, retimed, a scene or meta strip, a transition)
   and that a cut touches refuses the whole edit, before anything changes.  Text
   strips (captions, titles) are not cut in pieces but trimmed to the time they
   keep (:func:`map_frame`), so one caption stays one caption; one left shorter
   than :data:`READABLE_S` (or than half its length, for a short word) is dropped,
   with its word strips.
3. :func:`piece_names`: the first piece keeps the strip's name, the others are
   numbered from it, so a name an agent holds still points at the strip.
4. :func:`follow_end`: the scene end (or the preview end) follows the edit when it
   was at the end of the content before.
5. Speed Up (#542): :func:`sped_length` and :class:`SpeedMap`: the same ranges, played
   *factor* times faster instead of cut; where a frame lands once they are, which
   follows the edit for the strips that are not cut in pieces (captions).
"""

from __future__ import annotations

__all__ = (
    "Cuts",
    "READABLE_S",
    "SPEED_FACTORS",
    "SpeedMap",
    "common_cuts",
    "follow_end",
    "map_frame",
    "overlapping",
    "piece_names",
    "plan",
    "sped_length",
    "stretch_edges",
    "subtract",
    "within",
)

import bisect
import math
from typing import Any, Iterable, Sequence

READABLE_S = 0.3        # a caption shorter than this can't be read: it goes


def _merge(ranges: Iterable[tuple]) -> list[tuple]:
    out: list[list] = []
    for start, end in sorted(r for r in ranges if r[1] > r[0]):
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(s, e) for s, e in out]


def subtract(ranges: Iterable[tuple], holes: Iterable[tuple]) -> list[tuple]:
    """*ranges* without *holes* (both ``(start, end)``, end exclusive), merged and sorted."""
    holes = _merge(holes)
    out = []
    for start, end in _merge(ranges):
        cursor = start
        for a, b in holes:
            if b <= cursor:
                continue
            if a >= end:
                break
            if a > cursor:
                out.append((cursor, a))
            cursor = max(cursor, b)
            if cursor >= end:
                break
        if cursor < end:
            out.append((cursor, end))
    return out


def within(ranges: Iterable[tuple], first: int, end: int) -> list[tuple]:
    """*ranges* cut down to the frames ``first..end`` (end exclusive): the "this frame range only" mode of
    Remove Silences (#615).  What lies outside is dropped, what crosses a border is shortened."""
    out = []
    for a, b in _merge(ranges):
        a, b = max(a, first), min(b, end)
        if b > a:
            out.append((a, b))
    return out


def common_cuts(listeners: Sequence[tuple]) -> list[tuple]:
    """The frames to remove when every sound is listened to at once.

    *listeners*: ``(left, right, silent)`` per sound strip, *silent* its quiet frame
    ranges inside ``left..right``.  A frame goes when at least one of them plays
    there and every one that plays there is quiet: two speakers on two strips keep
    each other's words, and a gap with no sound at all is no silence.
    """
    covered = _merge((left, right) for left, right, _silent in listeners)
    loud = []
    for left, right, silent in listeners:
        loud.extend(subtract([(left, right)], silent))
    return subtract(covered, loud)


def stretch_edges(left: int, right: int, silent: Iterable[tuple], new_left: int, new_right: int) -> tuple:
    """``(left, right, silent)`` of a sound stretched to its picture's span (a frame or two
    longer, as a movie's picture often is): a silence that reaches the sound's start or end
    reaches the picture's too, so no sliver of picture is left without its sound; frames
    added next to sound are loud (kept)."""
    out = [list(r) for r in _merge(silent)]
    if out and new_right > right and out[-1][1] >= right:
        out[-1][1] = new_right
    if out and new_left < left and out[0][0] <= left:
        out[0][0] = new_left
    return min(left, new_left), max(right, new_right), [(a, b) for a, b in out]


class Cuts:
    """Sorted, merged cut ranges with fast lookups (an hour of footage has hundreds)."""

    def __init__(self, cuts: Iterable[tuple]):
        self.ranges = _merge(cuts)
        self.starts = [a for a, _b in self.ranges]
        self.ends = [b for _a, b in self.ranges]
        self.before = [0]           # frames removed before range i
        for a, b in self.ranges:
            self.before.append(self.before[-1] + (b - a))

    def map(self, frame: float) -> float:
        i = bisect.bisect_right(self.starts, frame)         # ranges starting at or before frame
        if i and frame < self.ends[i - 1]:                  # inside range i-1: lands at its start
            return self.starts[i - 1] - self.before[i - 1]
        return frame - self.before[i]

    def touching(self, left: float, right: float) -> list[tuple]:
        """The ranges overlapping ``left..right``, clipped to it."""
        out = []
        i = bisect.bisect_right(self.ends, left)
        while i < len(self.ranges) and self.starts[i] < right:
            a, b = self.ranges[i]
            out.append((max(a, left), min(b, right)))
            i += 1
        return [r for r in out if r[1] > r[0]]


def map_frame(frame: float, cuts: Iterable[tuple]) -> float:
    """Where *frame* lands once *cuts* are closed up (a frame inside a cut: where it began)."""
    return Cuts(cuts).map(frame)


def overlapping(left: float, right: float, cuts: Iterable[tuple]) -> list[tuple]:
    """The parts of *cuts* inside ``left..right``."""
    return Cuts(cuts).touching(left, right)


def _round(value: float) -> int:
    return int(math.floor(value + 0.5))


def plan(strips: Sequence[dict], cuts: Iterable[tuple], ripple: bool = True, fps: float = 24.0,
         readable_s: float = READABLE_S, mapper: Any = None) -> dict[str, Any]:
    """What happens to each strip at the level when *cuts* go.

    *strips*: dicts with ``name``, ``left``, ``right``, ``kind`` (``"media"``: cut in
    pieces; ``"text"``: trimmed; ``"follow"``: an effect that follows its inputs),
    ``locked`` and ``refuse`` (why it can't be cut, ``""`` when it can), and for a
    word strip ``line`` (its caption's name).

    Returns ``refused`` (``[(name, reason)]``: when not empty nothing may change),
    ``cut`` (``{name: [ranges]}`` to remove from media strips and, with *ripple*
    off, from text strips), ``trim`` (``{name: (left, right)}``: a text strip's new
    handles once closed up), ``drop`` (text strips that go) and ``cuts`` (merged).

    *mapper* (Speed Up, #542): an object with ``map(frame)`` that says where a frame lands instead
    of the cuts closing up (a :class:`SpeedMap`); the ranges then are played faster, not removed.
    """
    c = Cuts(cuts)
    where = mapper if mapper is not None else c
    readable = readable_s * fps
    refused, cut, trim, drop = [], {}, {}, []
    lines_dropped = set()
    for s in strips:
        left, right = s["left"], s["right"]
        touched = c.touching(left, right)
        moves = bool(ripple) and where.map(left) != left
        if s.get("locked") and (touched or moves):
            refused.append((s["name"], "is locked" if touched else "is locked and would have to move with the edit"))
            continue
        if touched and s.get("refuse"):
            refused.append((s["name"], s["refuse"]))
            continue
        if not touched or s["kind"] == "follow":
            continue
        if s["kind"] == "text":
            length = right - left
            short = min(readable, length / 2.0)
            if ripple:
                new = (_round(where.map(left)), _round(where.map(right)))
                kept = new[1] - new[0]
                if kept <= 0 or kept < short:
                    drop.append(s["name"])
                    lines_dropped.add(s["name"])
                else:
                    trim[s["name"]] = new
            else:
                pieces = subtract([(left, right)], touched)
                keep = [p for p in pieces if p[1] - p[0] >= short]
                if not keep and pieces:
                    keep = [max(pieces, key=lambda p: p[1] - p[0])]
                if not keep:
                    drop.append(s["name"])
                    lines_dropped.add(s["name"])
                else:
                    cut[s["name"]] = subtract([(left, right)], keep)
            continue
        cut[s["name"]] = touched
    for s in strips:        # the word strips of a caption that goes, go with it
        if s.get("line") in lines_dropped and s["name"] not in lines_dropped:
            drop.append(s["name"])
            trim.pop(s["name"], None)
            cut.pop(s["name"], None)
    return {"refused": refused, "cut": cut, "trim": trim, "drop": drop, "cuts": list(c.ranges)}


def piece_names(original: str, count: int, taken: Iterable[str]) -> list[str]:
    """Names for *count* pieces of *original*, in time order: the first keeps it, the
    others are ``original.001``, ``.002`` ... skipping the names in *taken*."""
    if count <= 0:
        return []
    used = set(taken)
    out = [original]
    used.add(original)
    n = 0
    while len(out) < count:
        n += 1
        name = "{:s}.{:03d}".format(original, n)
        if name not in used:
            out.append(name)
            used.add(name)
    return out


def follow_end(frame_end: int, end_before: int, end_after: int, frame_start: int = 1) -> int | None:
    """The new scene (or preview) end, or ``None`` to leave it: it follows when it was
    at or past the last frame of the content before (*end_before*, *end_after*: the
    content's end, exclusive, before and after the edit)."""
    if end_after <= end_before and frame_end >= end_before - 1:
        return max(int(frame_start), int(end_after) - 1)
    return None


# -- Speed Up (#542) -----------------------------------------------------------------------

SPEED_FACTORS = (2, 4, 8)


def sped_length(length: int, factor: float) -> int:
    """How many frames a piece of *length* frames takes once played *factor* times faster:
    rounded to the nearest frame (halves up, like Blender's retiming), never less than one, and
    never more than before."""
    length = int(length)
    if length <= 1 or factor <= 1.0:
        return max(0, length)
    return max(1, min(length, int((length / float(factor)) + 0.5)))


class SpeedMap:
    """Where the frames of the edit land once each of *ranges* (``(start, end)``, end exclusive) plays
    in *lengths* frames (``lengths[i]`` for ``ranges[i]``) and the rest closes up behind it.

    Inside a range a frame lands in proportion; at its start it stays; after it, it moves left by what
    the ranges before it saved.  Same shape as :meth:`Cuts.map`, so ``wand.sequencer._ripple``
    takes either."""

    def __init__(self, ranges: Iterable[tuple], lengths: Iterable[int]):
        rows = sorted((int(a), int(b), int(n)) for (a, b), n in zip(ranges, lengths) if b > a)
        self.ranges = [(a, b) for a, b, _n in rows]
        self.lengths = [n for _a, _b, n in rows]
        self.starts = [a for a, _b in self.ranges]
        self.ends = [b for _a, b in self.ranges]
        self.before = [0]           # frames saved before range i
        for (a, b), n in zip(self.ranges, self.lengths):
            self.before.append(self.before[-1] + (b - a - n))

    @property
    def saved(self) -> int:
        """Frames saved in all."""
        return self.before[-1]

    def map(self, frame: float) -> float:
        i = bisect.bisect_right(self.starts, frame)         # ranges starting at or before frame
        if i and frame < self.ends[i - 1]:                  # inside range i-1: in proportion
            a, b = self.ranges[i - 1]
            return a - self.before[i - 1] + (frame - a) * self.lengths[i - 1] / float(b - a)
        return frame - self.before[i]

    def touching(self, left: float, right: float) -> list[tuple]:
        """The ranges overlapping ``left..right``, clipped to it."""
        return Cuts(self.ranges).touching(left, right)
