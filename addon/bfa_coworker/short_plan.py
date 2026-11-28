# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Highlights H5 (#615), the part that needs no Blender: how a pick becomes a short.

A short is **a copy of the scene whose frame range is the pick**, nothing deleted.  This module decides
the things around that copy:

* the name, ``Short 01: <title>`` (:func:`short_name`, :func:`next_number`), cut to Blender's 63 bytes;
* the tag on the copy, saying which scene and which words it came from (:func:`tag`);
* where the borders go: in the pause before the first word and after the last, not on a word
  (:func:`neighbours`, :func:`borders`);
* the polish chain's settings (:data:`SILENCES`, :func:`layout_for`), the hook title's frames and text
  (:func:`hook_frames`, :func:`hook_text`);
* the output file next to the .blend, named after the short (:func:`file_stem`, :func:`output_path`);
* one report line per step (:func:`step_line`).

Importing this module needs no ``bpy``.
"""

from __future__ import annotations

__all__ = (
    "HOOK_SECONDS",
    "ID_BYTES",
    "LEAD_SECONDS",
    "SILENCES",
    "TAIL_SECONDS",
    "TAG_FROM",
    "TAG_WORDS",
    "borders",
    "file_stem",
    "fit_name",
    "hook_frames",
    "hook_text",
    "layout_for",
    "neighbours",
    "next_number",
    "output_path",
    "short_name",
    "step_line",
    "tag",
)

import json
import os
import re
from typing import Any, Sequence

TAG_FROM = "coworker_short_of"          # on a short: the scene it was made from
TAG_WORDS = "coworker_short_words"      # on a short: JSON, the pick's words and title
ID_BYTES = 63                           # Blender's longest ID name
LEAD_SECONDS = 0.35                     # at most this much room before the first word
TAIL_SECONDS = 0.5                      # and after the last (a pause is never filled up: half of it at most)
HOOK_SECONDS = 3.0
HOOK_CHARS = 80
# Remove Silences for a short: tighter than for a long edit (threshold dB, shortest silence s, padding s, shortest keep s).
SILENCES = (-40.0, 0.3, 0.1, 0.2)
_NUMBERED = re.compile(r"^Short (\d+): ")


def fit_name(text: str, limit: int = ID_BYTES) -> str:
    """*text* cut so it is at most *limit* bytes in UTF-8 (never in the middle of a character)."""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", errors="ignore").rstrip()


def next_number(names: Sequence[str]) -> int:
    """The next free ``Short NN`` number among scene *names*."""
    used = [int(m.group(1)) for m in (_NUMBERED.match(n) for n in names) if m]
    return max(used, default=0) + 1


def short_name(number: int, title: str) -> str:
    """``Short 01: <title>``, at most 63 bytes."""
    clean = re.sub(r"\s+", " ", str(title or "")).strip() or "Untitled"
    return fit_name("Short {:02d}: {:s}".format(max(1, int(number)), clean))


def tag(scene_name: str, pick: dict[str, Any]) -> str:
    """The text kept on the short: where it came from (the pick's words are its identity, as in the picks list)."""
    return json.dumps({"scene": scene_name, "title": pick.get("title", ""), "first_word": pick.get("first_word"),
                       "last_word": pick.get("last_word"), "id": pick.get("id", "")},
                      separators=(",", ":"), ensure_ascii=True)


def neighbours(sentences: Sequence[dict], start: int, end: int) -> tuple[int | None, int | None]:
    """``(prev_end, next_start)``: where the sentence before the pick ends and the one after it starts
    (``None`` when there is none), from *sentences* as they play now."""
    before = [s["end"] for s in sentences if s["end"] <= start]
    after = [s["start"] for s in sentences if s["start"] >= end]
    return (max(before) if before else None), (min(after) if after else None)


def borders(start: int, end: int, prev_end: int | None, next_start: int | None, fps: float,
            lo: int, hi: int) -> tuple[int, int]:
    """``(first, last)`` frames of the short's range for a pick of the frames ``start..end`` (end exclusive).

    The borders go into the pauses: up to :data:`LEAD_SECONDS` before the first word and :data:`TAIL_SECONDS`
    after the last, never more than half of the pause on that side (so the neighbour's word is not touched),
    and never outside ``lo..hi`` (the edit).  *last* is the last frame that plays (Blender's range is inclusive)."""
    fps = fps if fps > 0 else 24.0
    lead, tail = int(round(LEAD_SECONDS * fps)), int(round(TAIL_SECONDS * fps))
    room_before = lead if prev_end is None else max(0, (start - prev_end) // 2)
    room_after = tail if next_start is None else max(0, (next_start - end) // 2)
    first = max(int(lo), int(start) - min(lead, room_before))
    last = min(int(hi), int(end) - 1 + min(tail, room_after))
    return first, max(first, last)


def layout_for(kind: str) -> str:
    """The Make Vertical mode a kind of video suggests: a tutorial shows a screen, so the whole shot stays
    visible (Blur Fill); a person talking is cropped to the middle.  Reframe (#606) changes it afterwards."""
    return "BLUR_FILL" if kind == "tutorial" else "CROP"


def hook_frames(fps: float, first: int, last: int) -> tuple[int, int]:
    """``(first, end)`` of the hook title: the first three seconds of the range, or all of a shorter one
    (end exclusive)."""
    n = int(round(HOOK_SECONDS * (fps if fps > 0 else 24.0)))
    return int(first), int(min(last + 1, first + n))


def hook_text(pick: dict[str, Any]) -> str:
    """The line over the first seconds: the pick's hook, else its title, cut to :data:`HOOK_CHARS`."""
    text = re.sub(r"\s+", " ", str(pick.get("hook") or pick.get("title") or "")).strip()
    if len(text) > HOOK_CHARS:
        text = text[:HOOK_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:-") + "..."
    return text


def file_stem(name: str) -> str:
    """A file name made from the short's name: letters, digits, space, dash and underscore only."""
    stem = re.sub(r"[^\w \-]", "", str(name), flags=re.UNICODE).replace(":", "")
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    return stem[:80].strip(" .") or "Short"


def output_path(blend_dir: str, name: str) -> str:
    """The file the short renders to: next to the .blend (*blend_dir*), named after the short, ``.mp4``.
    Blender writes ``<path>`` as it is when it ends in the extension."""
    return os.path.join(blend_dir, file_stem(name) + ".mp4")


def step_line(step: str, state: str, text: str = "") -> str:
    """One report line: ``Remove Silences: done, 4 silences (0:03) removed``; *state* is ``done``, ``skipped``
    (off, or nothing to do) or ``failed``."""
    return "{:s}: {:s}{:s}".format(step, state, ", " + text if text else "")
