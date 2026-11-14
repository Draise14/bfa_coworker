# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Finding the silences in a sound strip, and where to cut (Tier 4m, Wand 0, #331).

Free of ``bpy``: the operator (``wand/sequencer.py``) reads the sound into a
loudness envelope -- one RMS value per short window, in dB below full scale --
and everything after that is here:

1. :func:`find_silences`: the quiet stretches of the envelope, in seconds, with
   a threshold, a minimum silence length, padding kept around the speech and a
   minimum length for what is kept between two cuts.
2. :func:`to_frames`: seconds of the sound to timeline frames, from the strip's
   content start, its sound offset and the scene's frame rate; clamped to the
   strip's handles and merged.
3. :func:`ripple_shifts`: how far each strip moves left once the cuts are
   closed up.
4. :func:`is_partner`: which picture strip belongs with a sound strip, so both
   are cut together and stay in sync.
5. :func:`summary`: the preview line, e.g. ``"12 silences, 0:41 removed"``.
"""

from __future__ import annotations

__all__ = (
    "VISUAL_TYPES",
    "db",
    "find_silences",
    "format_duration",
    "is_partner",
    "merge_ranges",
    "removed_frames",
    "ripple_shifts",
    "sound_window",
    "summary",
    "to_frames",
    "window_rms",
)

import math
import os
from typing import Iterable, Sequence

_Range = tuple  # (start, end), end exclusive
_EPS = 1e-9
_FLOOR_DB = -200.0
# Strip types that can be the picture of a sound strip.
VISUAL_TYPES = frozenset({"MOVIE", "IMAGE", "MOVIECLIP", "SCENE", "COLOR"})


def db(rms: float) -> float:
    """*rms* (full scale = 1.0) in dB below full scale; silence is -200 dB."""
    return 20.0 * math.log10(rms) if rms > 1e-10 else _FLOOR_DB


def window_rms(samples: Sequence[float], window: int) -> list[float]:
    """RMS of each whole *window* of *samples* (a leftover shorter window is dropped).

    The operator does the same with numpy; this is the fallback and the reference."""
    out = []
    for start in range(0, len(samples) - window + 1, window):
        chunk = samples[start:start + window]
        out.append(math.sqrt(sum(x * x for x in chunk) / window))
    return out


def merge_ranges(ranges: Iterable[_Range], gap: float = 0.0) -> list[tuple]:
    """Sorted, with overlapping ranges (or ones at most *gap* apart) joined; empty ones dropped."""
    out: list[list] = []
    for start, end in sorted(r for r in ranges if r[1] > r[0]):
        if out and start <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(s, e) for s, e in out]


def find_silences(env_db: Sequence[float], window_s: float, threshold_db: float, min_silence_s: float,
                  padding_s: float = 0.0, min_keep_s: float = 0.0, total_s: float | None = None) -> list[tuple]:
    """The ranges to remove, in seconds from the start of the envelope.

    *env_db* holds one loudness value per *window_s* seconds.  A window quieter
    than *threshold_db* is silent; a run of silent windows at least
    *min_silence_s* long is a silence.  *padding_s* of each silence is kept next
    to the sound around it (not at the very start or end, where nothing is heard
    before or after), so words aren't clipped; a silence shorter than twice the
    padding goes.  What's left between two silences, or before the first and
    after the last, is removed too when it's shorter than *min_keep_s* (a click,
    a breath).  The result is sorted and doesn't overlap; ``[(0, total)]`` means
    all of it is quiet.
    """
    total = len(env_db) * window_s if total_s is None else total_s
    if total <= 0 or not env_db:
        return []
    runs, start = [], None
    for i, value in enumerate(list(env_db) + [float("inf")]):
        if value < threshold_db:
            if start is None:
                start = i
        elif start is not None:
            # A run to the last window reaches the end (a partial last window isn't in env_db).
            runs.append((start * window_s, total if i == len(env_db) else min(i * window_s, total)))
            start = None
    cuts = []
    for start_s, end_s in runs:
        if end_s - start_s + _EPS < min_silence_s:
            continue
        if start_s > _EPS:
            start_s += padding_s
        if end_s < total - _EPS:
            end_s -= padding_s
        if end_s - start_s > _EPS:
            cuts.append((start_s, end_s))
    if not cuts or min_keep_s <= 0:
        return cuts
    # Keep only what's long enough between the cuts: a short keep joins its neighbours.
    out = [list(cuts[0])]
    if out[0][0] < min_keep_s - _EPS:
        out[0][0] = 0.0
    for start_s, end_s in cuts[1:]:
        if start_s - out[-1][1] < min_keep_s - _EPS:
            out[-1][1] = end_s
        else:
            out.append([start_s, end_s])
    if total - out[-1][1] < min_keep_s - _EPS:
        out[-1][1] = total
    return [(s, e) for s, e in out]


def sound_window(left: float, right: float, content_start: float, sound_offset: float, fps: float) -> tuple:
    """The seconds of the sound a strip plays between its handles *left* and *right*.

    The sound's time *t* plays at timeline frame ``content_start + (t - sound_offset) * fps``;
    the start is never before the sound's own start.
    """
    start = (left - content_start) / fps + sound_offset
    end = (right - content_start) / fps + sound_offset
    return max(0.0, start), max(0.0, end)


def to_frames(ranges_s: Iterable[_Range], fps: float, content_start: float, sound_offset: float,
              left: int, right: int) -> list[tuple]:
    """Sound-time ranges (seconds) as timeline frame ranges ``(first, end)``.

    Rounded to the nearest frame, clamped to the strip's handles *left*..*right*,
    merged; ranges shorter than a frame drop out."""
    out = []
    for start_s, end_s in ranges_s:
        first = int(round(content_start + (start_s - sound_offset) * fps))
        end = int(round(content_start + (end_s - sound_offset) * fps))
        first, end = max(first, int(left)), min(end, int(right))
        if end > first:
            out.append((first, end))
    return merge_ranges(out)


def removed_frames(ranges: Iterable[_Range]) -> int:
    return int(sum(end - start for start, end in ranges))


def ripple_shifts(starts: Sequence[float], removed: Sequence[_Range]) -> list[int]:
    """How far left each strip starting at ``starts[i]`` moves when the *removed*
    frame ranges are closed up: the removed time before its start.  A strip that
    starts inside a removed range lands where that range began; one that spans a
    removed range moves only for the ones before it."""
    removed = merge_ranges(removed)
    out = []
    for start in starts:
        out.append(int(sum(min(e, start) - s for s, e in removed if s < start)))
    return out


def _stem(name: str) -> str:
    return name.split(".", 1)[0].strip().lower()


def _same_file(a: str, b: str) -> bool:
    return bool(a) and bool(b) and os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def is_partner(sound: dict, other: dict) -> bool:
    """True when *other* is the picture that goes with the sound strip *sound*.

    Both are dicts with ``name``, ``type``, ``channel``, ``left``, ``right``,
    ``content_start``, ``path`` (absolute source file, ``""`` if none) and
    ``connected`` (names of the strips it is connected to).  A partner overlaps
    the sound in time and is connected to it, or is a movie of the same file, or
    sits on the next channel up or down, starts at the same frame and has the
    same name before the first dot ("talk.mp4" and "talk.001").
    """
    if other["name"] == sound["name"] or other["type"] not in VISUAL_TYPES:
        return False
    if other["right"] <= sound["left"] or other["left"] >= sound["right"]:
        return False
    if other["name"] in sound.get("connected", ()) or sound["name"] in other.get("connected", ()):
        return True
    if other["type"] == "MOVIE" and _same_file(other.get("path", ""), sound.get("path", "")):
        return True
    return (abs(other["channel"] - sound["channel"]) == 1
            and abs(other["content_start"] - sound["content_start"]) < 1.0
            and _stem(other["name"]) == _stem(sound["name"]) != "")


def format_duration(seconds: float) -> str:
    """``0:02.4`` under ten seconds, ``0:41``, ``12:05``, ``1:02:03``."""
    seconds = max(0.0, seconds)
    if seconds < 10.0:
        return "0:{:04.1f}".format(round(seconds, 1)).replace("0:10.0", "0:10")
    whole = int(round(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "{:d}:{:02d}:{:02d}".format(hours, minutes, secs)
    return "{:d}:{:02d}".format(minutes, secs)


def summary(count: int, frames: int, fps: float) -> str:
    """The preview line: ``"12 silences, 0:41 removed"``."""
    if count <= 0:
        return "No silences found"
    return "{:d} silence{:s}, {:s} removed".format(count, "" if count == 1 else "s",
                                                   format_duration(frames / fps if fps > 0 else 0.0))
