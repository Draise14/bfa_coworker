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
"""

from __future__ import annotations

__all__ = (
    "Cuts",
    "READABLE_S",
    "common_cuts",
    "follow_end",
    "map_frame",
    "overlapping",
    "piece_names",
    "plan",
    "subtract",
)

import bisect
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


def plan(strips: Sequence[dict], cuts: Iterable[tuple], ripple: bool = True, fps: float = 24.0,
         readable_s: float = READABLE_S) -> dict[str, Any]:
    """What happens to each strip at the level when *cuts* go.

    *strips*: dicts with ``name``, ``left``, ``right``, ``kind`` (``"media"``: cut in
    pieces; ``"text"``: trimmed; ``"follow"``: an effect that follows its inputs),
    ``locked`` and ``refuse`` (why it can't be cut, ``""`` when it can), and for a
    word strip ``line`` (its caption's name).

    Returns ``refused`` (``[(name, reason)]``: when not empty nothing may change),
    ``cut`` (``{name: [ranges]}`` to remove from media strips and, with *ripple*
    off, from text strips), ``trim`` (``{name: (left, right)}``: a text strip's new
    handles once closed up), ``drop`` (text strips that go) and ``cuts`` (merged).
    """
    c = Cuts(cuts)
    readable = readable_s * fps
    refused, cut, trim, drop = [], {}, {}, []
    lines_dropped = set()
    for s in strips:
        left, right = s["left"], s["right"]
        touched = c.touching(left, right)
        moves = bool(ripple) and c.map(left) != left
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
                new = (c.map(left), c.map(right))
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
