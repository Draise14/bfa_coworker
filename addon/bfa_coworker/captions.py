# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Auto Captions (Tier 4, Video 2, #376): the parts that need no Blender.

* **Words -> captions**: :func:`build_captions` groups timed words (the transcript
  format ``bfa_coworker.transcript/1``) into caption lines -- at most *max_chars* per
  line and *max_lines* lines, at most *max_duration* seconds, at least
  *min_duration*; it breaks on pauses and sentence ends (and on a comma once the
  caption is well filled), keeps numbers and titles with the word after them, and
  avoids a lone short word where it can.
* **Styles**: :data:`STYLES` (Clean, Bold Pop, Word-by-word highlight, Boxed) and
  :func:`resolve_style`, which turns one into text-strip properties for an output
  size, inside the safe area of a wide (16:9) or vertical (9:16) frame.
* **Frames and channels**: :func:`to_frames` (seconds in the media -> timeline
  frames, clipped to what the strip shows), :func:`highlight_windows`,
  :func:`pick_channel`, :func:`owned_by` (re-running replaces a source's captions).
* **Every selected strip** (#538): :func:`captions_for_strips` maps one file's transcript to
  captions for each strip that shows it (its source offset, trims and place on the timeline;
  captions never cross a strip's edge nor overlap a neighbour's); :func:`plan_strips` (a movie
  heard through its sound strip, muted and retimed strips skipped), :func:`replace_plan` and
  :func:`caption_signature` (a re-run keeps captions edited by hand), :func:`strips_report`.
* **Another frame** (Make Vertical, #497): :func:`relayout` lays a caption out again for
  a new output size by the same rules (splitting it in time where its words no longer fit
  the style's lines), :func:`hand_edits` tells a caption moved or resized by hand.
* **Animation and position** (#542): :data:`ANIMATIONS` (None, Fade, Slide Up, Typewriter,
  Pop) on any style, as a description of keyframes (:func:`animation_tracks`: which property,
  which values at which frames, clamped to the caption's own time) that ``captions_ops``
  applies; :data:`POSITIONS` (Bottom, Top, Middle) and a margin (:func:`placement`).
* **Speakers** (#542): :func:`plan_speakers` gives each speaker (one sound file each) its own
  channel name, and optionally style, position and a name label (:func:`speaker_name`).
* **SRT**: :func:`format_srt` and the tolerant :func:`parse_srt`.
* **Translate**: :func:`batch_text` / :func:`parse_batch_reply` (many captions in one
  question, written back one strip each).

``captions_ops`` builds the text strips and the operators on top of this.
"""

from __future__ import annotations

__all__ = (
    "ANIMATED_PATHS",
    "ANIMATIONS",
    "ANIM_STYLE",
    "CHANNEL_NAME",
    "CaptionsError",
    "HIGHLIGHT_CHANNEL_NAME",
    "LANGUAGE_RE",
    "MAX_MARGIN",
    "NOT_TRANSCRIBED",
    "NO_TRANSCRIPTION",
    "POSITIONS",
    "SCHEMA",
    "SKIP_MUTED",
    "SKIP_NO_SPEECH",
    "SKIP_NO_TRANSCRIPT",
    "SKIP_RETIMED",
    "SKIP_SILENT",
    "SKIP_TEXT",
    "STYLES",
    "TAG",
    "TRANSLATE_PREFIX",
    "animation_key",
    "animation_tracks",
    "animation_window",
    "batch_text",
    "build_captions",
    "caption_signature",
    "captions_for_strip",
    "captions_for_strips",
    "channel_names",
    "check_look_params",
    "check_speakers",
    "check_tool_params",
    "default_margin",
    "default_max_chars",
    "even_words",
    "format_srt",
    "frame_kind",
    "hand_edits",
    "highlight_left",
    "highlight_windows",
    "label_chars",
    "layout_lines",
    "media_frame",
    "normalise_words",
    "one_line",
    "owned_by",
    "parse_batch_reply",
    "parse_srt",
    "partner_outcome",
    "pick_channel",
    "placement",
    "plan_speakers",
    "plan_strips",
    "pop_middle",
    "position_key",
    "relayout",
    "relayout_limits",
    "relayout_report",
    "replace_plan",
    "resolve_style",
    "reveal_crops",
    "safe_area",
    "separate",
    "speaker_name",
    "srt_time",
    "strip_tags",
    "strips_report",
    "style_key",
    "to_frames",
    "transcript_words",
    "with_label",
    "without_label",
    "words_in_strip",
)

import html
import math
import re
import zlib
from typing import Any, Callable, Iterable, Sequence

SCHEMA = "bfa_coworker.transcript/1"
TAG = "bfacw_caption"                   # the custom property on every caption strip
CHANNEL_NAME = "Captions"
HIGHLIGHT_CHANNEL_NAME = "Captions Highlight"
TRANSLATE_PREFIX = "captions:"          # Translate's path for many caption strips at once
MAX_CHANNEL = 128
NO_TRANSCRIPTION = ("Captions from speech need local transcription, which isn't in this build yet. "
                    "Pick a transcript file (.json) in the dialog, or use Import SRT.")
NOT_TRANSCRIBED = ("{:s} hasn't been transcribed yet: ask Coworker to transcribe it (transcribe_strip), "
                   "then run Auto Captions again, or pick a transcript file (.json).")
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:[_-][a-z0-9]{2,8})?$")

# Timing defaults (seconds).
PAUSE = 0.5             # a gap this long between words starts a new caption
MAX_DURATION = 5.0
MIN_DURATION = 0.8
LINGER = 0.3            # a caption stays up over a gap shorter than this (no flicker)
ORPHAN_CHARS = 7        # a lone word this short is joined to a neighbour when it can be


class CaptionsError(Exception):
    """A plain message for the artist (or the agent)."""


# -- words -------------------------------------------------------------------------

_SENTENCE_END = (".", "!", "?", "…", "。", "！", "？")
_CLAUSE_END = (",", ";", ":", "—", "–", "，", "、")
_CLOSERS = "\"')]}”’»"
_OPENERS = "\"'([{“‘«¿¡"
_ABBREVIATIONS = frozenset({
    "mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "vs.", "etc.", "e.g.", "i.e.", "no.", "jr.", "sr.",
    "inc.", "ltd.", "approx.", "fig.", "vol.", "mt.", "ft.", "sgt.", "capt.", "gen.", "sra.",
})
_TITLES = frozenset({"mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "mt.", "sgt.", "capt.", "gen.", "sra.", "sr."})


def _core(word: str) -> str:
    return word.rstrip(_CLOSERS)


def _is_closing_punct(word: str) -> bool:
    return bool(word) and all(not ch.isalnum() for ch in word) and word[0] not in _OPENERS


def _is_opening_punct(word: str) -> bool:
    return bool(word) and all(ch in _OPENERS for ch in word)


def is_sentence_end(word: str) -> bool:
    """Ends a sentence: ``.``/``!``/``?`` -- not a title or abbreviation (``Dr.``, ``e.g.``)
    and not initials (``U.S.``)."""
    core = _core(word)
    if not core.endswith(_SENTENCE_END):
        return False
    low = core.lower()
    if low in _ABBREVIATIONS:
        return False
    if re.fullmatch(r"(?:[A-Za-z]\.){1,}", core) and len(core) <= 6:
        return False
    return True


def is_clause_end(word: str) -> bool:
    return _core(word).endswith(_CLAUSE_END)


def _is_number(word: str) -> bool:
    return any(ch.isdigit() for ch in word)


def glued(prev: str, nxt: str) -> bool:
    """*prev* and *nxt* stay on the same caption: a number and what it counts (``3 pm``,
    ``10 percent``), a title and the name (``Dr. Smith``), ``$`` and the amount."""
    if is_sentence_end(prev) or is_clause_end(prev):
        return prev.lower() in _TITLES
    if prev.lower() in _TITLES:
        return True
    if _is_number(prev) and not is_clause_end(prev):
        return True
    if prev in ("$", "€", "£", "#", "No", "no", "nr"):
        return True
    return False


def normalise_words(words: Iterable[Any]) -> list[dict[str, Any]]:
    """Clean timed words: empty or broken entries dropped, sorted by time, a punctuation
    token joined to its word, overlapping times clipped (a word ends where the next starts)."""
    clean: list[dict[str, Any]] = []
    for item in words or ():
        try:
            text = str(item.get("w", item.get("word", ""))).strip()
            start = float(item["start"])
            end = float(item.get("end", start))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if not text or math.isnan(start) or math.isinf(start):
            continue
        if math.isnan(end) or math.isinf(end):
            end = start
        start = max(0.0, start)
        clean.append({"w": " ".join(text.split()), "start": start, "end": max(start, end)})
    clean.sort(key=lambda w: (w["start"], w["end"]))
    merged: list[dict[str, Any]] = []
    opener = ""
    for w in clean:
        if _is_opening_punct(w["w"]):
            opener += w["w"]
            continue
        if merged and _is_closing_punct(w["w"]) and not opener:
            merged[-1]["w"] += w["w"]
            merged[-1]["end"] = max(merged[-1]["end"], w["end"])
            continue
        if opener:
            w = dict(w, w=opener + w["w"])
            opener = ""
        merged.append(dict(w))
    for a, b in zip(merged, merged[1:]):
        if a["end"] > b["start"]:
            a["end"] = max(a["start"], b["start"])
    return merged


def transcript_words(transcript: Any) -> list[dict[str, Any]]:
    """The words of a transcript dict (``bfa_coworker.transcript/1``), or of a bare list."""
    if isinstance(transcript, list):
        return list(transcript)
    if not isinstance(transcript, dict):
        raise CaptionsError("That isn't a transcript (expected a JSON object with \"words\").")
    schema = transcript.get("schema")
    if schema is not None and schema != SCHEMA:
        raise CaptionsError("Unknown transcript format {!r} (expected {:s}).".format(schema, SCHEMA))
    words = transcript.get("words")
    if not isinstance(words, list):
        raise CaptionsError("The transcript has no \"words\" list.")
    return words


# -- lines -------------------------------------------------------------------------

def _join(words: Sequence[str]) -> str:
    return " ".join(words)


def layout_lines(words: Sequence[str], max_chars: int, max_lines: int, force: bool = False) -> list[str] | None:
    """*words* laid out in at most *max_lines* lines of at most *max_chars*; None when they
    don't fit.  Two lines are balanced (a slightly shorter top line is preferred), breaking
    after a comma when it costs little, never between glued words.  With *force* it always
    answers: a word longer than a line gets a line of its own, and there may be more lines."""
    words = [w for w in words if w]
    if not words:
        return [] if force else None
    max_chars = max(1, int(max_chars))
    max_lines = max(1, int(max_lines))
    one = _join(words)
    if len(one) <= max_chars:
        return [one]
    if max_lines >= 2 and len(words) >= 2:
        best: tuple[float, int] | None = None
        for k in range(1, len(words)):
            top, bottom = _join(words[:k]), _join(words[k:])
            if len(top) > max_chars or len(bottom) > max_chars:
                continue
            score = abs(len(top) - len(bottom)) + (0.5 if len(top) > len(bottom) else 0.0)
            if glued(words[k - 1], words[k]):
                score += max_chars
            if is_clause_end(words[k - 1]) or is_sentence_end(words[k - 1]):
                score -= 4
            elif _core(words[k - 1]).lower() in _WEAK_END:
                score += 4
            if best is None or score < best[0]:
                best = (score, k)
        if best is not None:
            k = best[1]
            return [_join(words[:k]), _join(words[k:])]
    if not force:
        return None
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        if cur and len(_join(cur + [w])) > max_chars:
            lines.append(_join(cur))
            cur = []
        cur.append(w)
    if cur:
        lines.append(_join(cur))
    return lines


def _fits(group: Sequence[dict], max_chars: int, max_lines: int) -> bool:
    if len(group) == 1:
        return True                     # one word always "fits": it can't be split
    return layout_lines([w["w"] for w in group], max_chars, max_lines) is not None


def _caption(group: list[dict], max_chars: int, max_lines: int) -> dict[str, Any]:
    lines = layout_lines([w["w"] for w in group], max_chars, max_lines, force=True) or []
    return {"text": "\n".join(lines), "lines": lines, "start": group[0]["start"], "end": group[-1]["end"],
            "words": [dict(w) for w in group]}


# Words a caption shouldn't end on (it reads as cut off).
_WEAK_END = frozenset({"the", "a", "an", "of", "to", "and", "or", "but", "in", "on", "at", "for", "with",
                       "from", "by", "my", "your", "our", "their", "this", "that", "is", "are", "was"})


def build_captions(words: Iterable[Any], max_chars: int = 36, max_lines: int = 2,
                   max_duration: float = MAX_DURATION, min_duration: float = MIN_DURATION,
                   pause: float = PAUSE) -> list[dict[str, Any]]:
    """Timed words -> caption lines ``{"text", "lines", "start", "end", "words"}`` in seconds.

    The words are cut into stretches at every pause of *pause* seconds or more and every
    sentence end; a stretch too long for one caption (*max_lines* x *max_chars*, at most
    *max_duration*) is split where it reads best: captions of even length, after a comma
    rather than before, never between glued words (``3 pm``, ``Dr. Smith``), not ending on
    "the"/"of"..., and no lone short word when another split works."""
    ws = normalise_words(words)
    if not ws:
        return []
    max_chars = max(4, int(max_chars))
    max_lines = 1 if int(max_lines) <= 1 else 2
    stretches: list[list[dict]] = [[ws[0]]]
    for prev, w in zip(ws, ws[1:]):
        if w["start"] - prev["end"] >= pause or is_sentence_end(prev["w"]):
            stretches.append([])
        stretches[-1].append(w)
    caps: list[dict[str, Any]] = []
    for stretch in stretches:
        for group in _split(stretch, max_chars, max_lines, max_duration):
            caps.append(_caption(group, max_chars, max_lines))
    _time(caps, min_duration)
    return caps


def _split(seg: list[dict], max_chars: int, max_lines: int, max_duration: float) -> list[list[dict]]:
    """*seg* in captions, by the cheapest split (dynamic programming over the word gaps)."""
    texts = [w["w"] for w in seg]
    n = len(seg)
    total = len(_join(texts))
    capacity = max_chars * max_lines
    duration = seg[-1]["end"] - seg[0]["start"]
    count = max(1, math.ceil(total / capacity), math.ceil(duration / max_duration) if max_duration > 0 else 1)
    target = total / count
    best = [math.inf] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            size = j - i
            if size > 1:
                if len(_join(texts[i:j])) > capacity or not _fits(seg[i:j], max_chars, max_lines):
                    break               # longer groups only get longer
                if seg[j - 1]["end"] - seg[i]["start"] > max_duration:
                    break
            if best[i] == math.inf:
                continue
            length = len(_join(texts[i:j]))
            cost = best[i] + 10.0 + 10.0 * ((length - target) / max_chars) ** 2
            if size == 1 and n > 1 and len(texts[i]) <= ORPHAN_CHARS:
                cost += 30.0            # a lone short word
            if j < n:                   # the caption ends between texts[j-1] and texts[j]
                if glued(texts[j - 1], texts[j]):
                    cost += 50.0
                if is_clause_end(texts[j - 1]):
                    cost -= 7.0
                elif _core(texts[j - 1]).lower() in _WEAK_END:
                    cost += 8.0
            if cost < best[j]:
                best[j], back[j] = cost, i
    groups: list[list[dict]] = []
    j = n
    while j > 0:
        i = back[j]
        groups.append(seg[i:j])
        j = i
    groups.reverse()
    return groups


def _time(caps: list[dict], min_duration: float) -> None:
    """Each caption up at least *min_duration* (not into the next one), over short gaps."""
    for i, c in enumerate(caps):
        nxt = caps[i + 1]["start"] if i + 1 < len(caps) else None
        end = max(c["end"], c["start"] + min_duration)
        if nxt is not None:
            if nxt - end < LINGER:
                end = nxt
            end = min(end, nxt)
        c["end"] = max(end, c["start"] + 0.01)


def even_words(text: str, start: float, end: float) -> list[dict[str, Any]]:
    """Words of *text* spread over *start*..*end* by their length (an SRT cue has no word times)."""
    words = text.split()
    if not words:
        return []
    total = sum(len(w) + 1 for w in words)
    span = max(0.0, end - start)
    out, t = [], start
    for w in words:
        d = span * (len(w) + 1) / total
        out.append({"w": w, "start": t, "end": t + d})
        t += d
    out[-1]["end"] = end
    return out


# -- frames and channels ------------------------------------------------------------

def to_frames(caps: Sequence[dict], fps: float, content_start: float = 0.0,
              left: float | None = None, right: float | None = None) -> list[dict[str, Any]]:
    """Captions with ``frame_start`` / ``frame_end`` (end exclusive) on the timeline: media
    second *t* is frame ``content_start + t * fps``; only what the strip shows (*left* <=
    frame < *right*) is kept, clipped, at least one frame each and never overlapping.
    Word times get frames too (``"frame"`` per word)."""
    out: list[dict[str, Any]] = []
    prev_end = -math.inf
    for c in caps:
        fs = int(round(content_start + c["start"] * fps))
        fe = int(round(content_start + c["end"] * fps))
        if left is not None:
            if fe <= left:
                continue
            fs = max(fs, int(left))
        if right is not None:
            if fs >= right:
                continue
            fe = min(fe, int(right))
        fs = max(fs, prev_end) if prev_end != -math.inf else fs
        if fe <= fs:
            fe = fs + 1
        if right is not None and fs >= right:
            continue
        d = dict(c)
        d["frame_start"], d["frame_end"] = int(fs), int(fe)
        d["words"] = [dict(w, frame=int(round(content_start + w["start"] * fps))) for w in c.get("words", ())]
        out.append(d)
        prev_end = fe
    return out


def highlight_windows(cap: dict) -> list[tuple[int, int, int]]:
    """``(word index, frame_start, frame_end)`` per word of a framed caption: from the word's
    start to the next word's (the first from the caption's start, the last to its end),
    inside the caption; zero-length windows are left out."""
    fs, fe = cap["frame_start"], cap["frame_end"]
    words = cap.get("words") or []
    out = []
    for i, w in enumerate(words):
        a = fs if i == 0 else min(max(w.get("frame", fs), fs), fe)
        b = fe if i + 1 == len(words) else min(max(words[i + 1].get("frame", fe), fs), fe)
        if out and a < out[-1][2]:
            a = out[-1][2]
        if b > a:
            out.append((i, a, b))
    return out


def highlight_left(x_center: float, line_width: float, prefix_width: float, frame_width: float) -> float:
    """The left edge (0-1 of the frame) of a word inside a centred line: the line starts at
    ``x_center - line/2``, the word after its prefix (the words before it and a space)."""
    if frame_width <= 0:
        return x_center
    return x_center - line_width / 2.0 / frame_width + prefix_width / frame_width


def pick_channel(occupied: dict[int, list[tuple[float, float]]], names: dict[int, str], above: int,
                 span: tuple[float, float] | Sequence[tuple[float, float]], need: int = 1,
                 max_channel: int = MAX_CHANNEL, name: str = CHANNEL_NAME,
                 skip: Iterable[int] = ()) -> int | None:
    """The channel for the captions: a channel already named *name* (*Captions*) above *above*
    if it (and the *need* - 1 above it) is free over *span*, else the first such free channel
    above *above*; None when there's no room.  *occupied* = channel -> (start, end) of its strips.
    *span* may also be a list of ``(start, end)``: each caption's own frames (#538), so a caption
    kept between them doesn't push the new ones onto another channel.  *skip*: channels not to
    use (another speaker's, #542)."""
    spans = list(span) if span and isinstance(span[0], (tuple, list)) else [span]
    skip = set(skip)

    def free(ch: int) -> bool:
        for c in range(ch, ch + need):
            if c > max_channel or c in skip:
                return False
            for a, b in occupied.get(c, ()):
                if any(a < s1 and s0 < b for s0, s1 in spans):
                    return False
        return True

    named = sorted(c for c, n in names.items() if n == name and c > above)
    for c in named:
        if free(c):
            return c
    for c in range(max(1, above + 1), max_channel + 1):
        if free(c):
            return c
    return None


def owned_by(tag: Any, sources: Iterable[str]) -> bool:
    """The caption strip with *tag* was made from one of *sources* (so a re-run replaces it)."""
    if not isinstance(tag, dict):
        return False
    return tag.get("source") in set(sources)


# -- every selected strip (#538) ------------------------------------------------------

# Why a selected strip got no captions (``strips_skipped[i]["reason"]``).
SKIP_NO_SPEECH = "no speech"
SKIP_RETIMED = "retimed"
SKIP_NO_TRANSCRIPT = "no transcript"
SKIP_MUTED = "muted"
SKIP_SILENT = "silent"          # the transcript has no words at all (#558)
SKIP_TEXT = {
    SKIP_NO_SPEECH: "no speech in the part it shows",
    SKIP_SILENT: "no speech in the clip",
    SKIP_RETIMED: "retimed: its speed changed, so captions would be out of sync (reset its retiming to caption it)",
    SKIP_NO_TRANSCRIPT: "no transcript",
    SKIP_MUTED: "muted: none of it is heard",
}


def plan_strips(items: Sequence[dict]) -> tuple[list[dict], dict[str, str], dict[str, list[str]]]:
    """Which of the selected media strips *items* get their own captions.

    *items*: ``{"name", "type": "SOUND"|"MOVIE", "key" (the media file), "left", "right",
    "muted", "retimed"}``.  A movie strip is heard through a selected sound strip of the same
    file over its time (its own sound, connected or not): it takes that strip's outcome and is
    not captioned twice.  A movie with no such sound strip is captioned itself.  Muted and
    retimed strips are skipped.

    Returns ``(todo, skipped, partners)``: the strips to caption (sounds first, then in time
    order), ``{name: reason}`` for those skipped here, ``{movie name: [sound names]}``."""
    sounds = [it for it in items if it["type"] == "SOUND"]
    partners: dict[str, list[str]] = {}
    todo: list[dict] = []
    skipped: dict[str, str] = {}
    for it in items:
        if it["type"] != "SOUND":
            heard = [s["name"] for s in sounds if s["key"] == it["key"]
                     and s["left"] < it["right"] and it["left"] < s["right"]]
            if heard:
                partners[it["name"]] = heard
                continue
        if it.get("muted"):
            skipped[it["name"]] = SKIP_MUTED
        elif it.get("retimed"):
            skipped[it["name"]] = SKIP_RETIMED
        else:
            todo.append(it)
    todo.sort(key=lambda it: (it["type"] != "SOUND", it["left"], it["name"]))
    return todo, skipped, partners


def partner_outcome(outcomes: Sequence[str]) -> str:
    """A movie's outcome from its sound strips' (``"captioned"`` or a skip reason): captioned
    when any of them is, else the first reason."""
    if any(o == "captioned" for o in outcomes):
        return "captioned"
    return next(iter(outcomes), SKIP_NO_SPEECH)


def media_frame(t: float, content_start: float, offset: float, fps: float) -> float:
    """The timeline frame where media second *t* of a strip plays: its content starts at
    *content_start*, a sound strip's *offset* (``sound_offset``, seconds) slips it."""
    return content_start + (t - offset) * fps


def words_in_strip(words: Iterable[Any], content_start: float, left: float, right: float,
                   offset: float, fps: float) -> list[dict[str, Any]]:
    """The words a strip plays: those whose start falls on a frame it shows (*left* <= frame <
    *right*, rounded as the captions are).  One word belongs to one piece of a cut clip."""
    out = []
    for w in normalise_words(words):
        f = int(round(media_frame(w["start"], content_start, offset, fps)))
        if int(left) <= f < int(right):
            out.append(w)
    return out


def captions_for_strip(words: Iterable[Any], strip: dict, fps: float, max_chars: int = 36,
                       max_lines: int = 2) -> list[dict[str, Any]]:
    """The captions of one strip (``{"name", "content_start", "left", "right", "offset"}``) on
    the timeline: its own words grouped into captions (never across its edges), framed and
    clipped to what it shows; each caption has ``"strip"``."""
    offset = float(strip.get("offset") or 0.0)
    cs = float(strip["content_start"])
    mine = words_in_strip(words, cs, strip["left"], strip["right"], offset, fps)
    caps = build_captions(mine, max_chars, max_lines)
    framed = to_frames(caps, fps, cs - offset * fps, strip["left"], strip["right"])
    for c in framed:
        c["strip"] = strip["name"]
    return framed


def separate(framed: Iterable[dict]) -> tuple[list[dict], int]:
    """Captions of several strips in time order, none overlapping another: a caption ends where
    the next one starts; one that would start on the same frame as the one before moves after
    it (and is left out when no frame is left).  ``(captions, how many were left out)``."""
    out: list[dict] = []
    dropped = 0
    for c in sorted(framed, key=lambda c: (c["frame_start"], c["frame_end"])):
        c = dict(c)
        if out and c["frame_start"] < out[-1]["frame_end"]:
            prev = out[-1]
            if c["frame_start"] > prev["frame_start"]:
                prev["frame_end"] = c["frame_start"]
            else:
                c["frame_start"] = prev["frame_end"]
                if c["frame_end"] <= c["frame_start"]:
                    dropped += 1
                    continue
        out.append(c)
    return out, dropped


def captions_for_strips(words: Iterable[Any], strips: Sequence[dict], fps: float, max_chars: int = 36,
                        max_lines: int = 2) -> dict[str, Any]:
    """A transcript (one media file's timed words, media seconds) -> timeline captions for each
    of *strips* that shows that file: ``{"name", "content_start", "left", "right", "offset"}``
    (frames; *offset* in seconds).  Each strip gets the words inside the part of the media it
    shows, grouped into captions that stay inside it; captions of neighbouring strips don't
    overlap.

    Returns ``{"captions": [framed captions with "strip"], "captioned": [names],
    "no_speech": [names], "left_out": int}``."""
    words = list(words or ())
    framed: list[dict] = []
    captioned, silent = [], []
    for strip in strips:
        mine = captions_for_strip(words, strip, fps, max_chars, max_lines)
        (captioned if mine else silent).append(strip["name"])
        framed.extend(mine)
    kept, dropped = separate(framed)
    return {"captions": kept, "captioned": captioned, "no_speech": silent, "left_out": dropped}


def caption_signature(text: str, length: int, channel: int, location: Sequence[float]) -> str:
    """What a caption looked like when Coworker made it: its text, length (frames), channel and
    place.  Not its time on the timeline: Remove Silences' ripple slides captions along with
    the clip, which is not a hand edit."""
    loc = tuple(round(float(v), 4) for v in tuple(location)[:2])
    raw = "{:s}\x00{:d}\x00{:d}\x00{!r}".format(str(text), int(length), int(channel), loc)
    return "{:08x}".format(zlib.crc32(raw.encode("utf-8")))


def replace_plan(lines: Sequence[dict], targets: Sequence[tuple] | None, present: Sequence[tuple] = (),
                 replace_edited: bool = False) -> tuple[set[str], set[str]]:
    """Which earlier caption lines a run replaces, and which hand-edited ones it keeps.

    *lines*: ``{"name", "key", "left", "right", "edited"}`` (the earlier captions of the sources
    being captioned).  *targets*: ``(key, left, right)`` of the strips captioned now (None:
    every line of those sources goes, as for Import SRT); *present*: ``(key, left, right)`` of
    every strip of those sources in the scene.  A line goes when it lies over a strip captioned
    now, or over no strip of its source any more (left behind); a line over another strip of the
    same file that isn't captioned now stays.  An *edited* line is kept unless
    *replace_edited*.  Returns ``(names to remove, edited names kept)``."""
    doomed: set[str] = set()
    kept: set[str] = set()

    def over(line: dict, spans: Sequence[tuple]) -> bool:
        return any(k == line["key"] and a < line["right"] and line["left"] < b for k, a, b in spans)

    for line in lines:
        if targets is not None and not over(line, targets) and over(line, present):
            continue
        if line.get("edited") and not replace_edited:
            kept.add(line["name"])
        else:
            doomed.add(line["name"])
    return doomed, kept


def strips_report(captioned: Sequence[str], skipped: Sequence[dict], kept_edited: int = 0) -> str:
    """``"for 6 strips; skipped 1, no speech in the part it shows: “Music”; kept 1 caption
    edited by hand (...)"`` -- the part of the report about the strips (#538), skipped strips
    grouped by reason (:data:`SKIP_TEXT`)."""
    n = len(captioned)
    parts = ["for {:d} strip{:s}".format(n, "" if n == 1 else "s")]
    by_reason: dict[str, list[str]] = {}
    for s in skipped:
        by_reason.setdefault(s["reason"], []).append(s["strip"])
    for reason, names in by_reason.items():
        shown = ", ".join("“{:s}”".format(x) for x in names[:4])
        if len(names) > 4:
            shown += " and {:d} more".format(len(names) - 4)
        parts.append("skipped {:d}, {:s}: {:s}".format(len(names), SKIP_TEXT.get(reason, reason), shown))
    if kept_edited:
        parts.append("kept {:d} caption{:s} edited by hand (Replace Edited Captions replaces {:s})".format(
            kept_edited, "" if kept_edited == 1 else "s", "it" if kept_edited == 1 else "them"))
    return "; ".join(parts)


# -- styles --------------------------------------------------------------------------

# Text-strip properties per style.  ``size`` = font size as a fraction of the output height
# for a wide frame, ``size_vertical`` for a vertical one; colours RGBA.  The font is
# Blender's bundled Inter (datafiles/fonts), the same in Blender and Bforartists.
STYLES: dict[str, dict[str, Any]] = {
    "CLEAN": {
        "label": "Clean", "description": "White text with a soft outline and shadow, up to two lines",
        "size": 0.050, "size_vertical": 0.031, "color": (1.0, 1.0, 1.0, 1.0), "use_bold": False,
        "use_outline": True, "outline_color": (0.0, 0.0, 0.0, 0.85), "outline_width": 0.04,
        "use_shadow": True, "shadow_color": (0.0, 0.0, 0.0, 0.6), "shadow_offset": 0.03, "shadow_blur": 0.15,
        "use_box": False, "uppercase": False, "max_lines": 2, "pop": False, "highlight": None,
    },
    "BOLD_POP": {
        "label": "Bold Pop", "description": "Big bold capitals with a thick outline, one line, popping in",
        "size": 0.075, "size_vertical": 0.045, "color": (1.0, 0.93, 0.25, 1.0), "use_bold": True,
        "use_outline": True, "outline_color": (0.0, 0.0, 0.0, 1.0), "outline_width": 0.12,
        "use_shadow": True, "shadow_color": (0.0, 0.0, 0.0, 0.5), "shadow_offset": 0.04, "shadow_blur": 0.1,
        "use_box": False, "uppercase": True, "max_lines": 1, "pop": True, "highlight": None,
    },
    "WORD_HIGHLIGHT": {
        "label": "Word-by-word highlight", "description": "One line; the word being spoken lights up",
        "size": 0.062, "size_vertical": 0.038, "color": (1.0, 1.0, 1.0, 1.0), "use_bold": False,
        "use_outline": True, "outline_color": (0.0, 0.0, 0.0, 1.0), "outline_width": 0.1,
        "use_shadow": True, "shadow_color": (0.0, 0.0, 0.0, 0.5), "shadow_offset": 0.03, "shadow_blur": 0.1,
        "use_box": False, "uppercase": False, "max_lines": 1, "pop": False,
        "highlight": (1.0, 0.82, 0.1, 1.0),
    },
    "BOXED": {
        "label": "Boxed", "description": "White text on a dark rounded box, up to two lines",
        "size": 0.046, "size_vertical": 0.029, "color": (1.0, 1.0, 1.0, 1.0), "use_bold": False,
        "use_outline": False, "outline_color": (0.0, 0.0, 0.0, 1.0), "outline_width": 0.05,
        "use_shadow": False, "shadow_color": (0.0, 0.0, 0.0, 0.5), "shadow_offset": 0.03, "shadow_blur": 0.1,
        "use_box": True, "box_color": (0.0, 0.0, 0.0, 0.66), "box_margin": 0.012, "box_roundness": 0.3,
        "uppercase": False, "max_lines": 2, "pop": False, "highlight": None,
    },
}
FONT_FILE = "Inter.woff2"
_STYLE_ALIASES = {"BOLD": "BOLD_POP", "POP": "BOLD_POP", "WORD": "WORD_HIGHLIGHT", "WORD_BY_WORD": "WORD_HIGHLIGHT",
                  "HIGHLIGHT": "WORD_HIGHLIGHT", "WORD_BY_WORD_HIGHLIGHT": "WORD_HIGHLIGHT", "KARAOKE": "WORD_HIGHLIGHT",
                  "BOX": "BOXED", "DEFAULT": "CLEAN", "": "CLEAN"}

# Safe areas: the caption's bottom edge (fraction of the height from the bottom), its widest
# text (fraction of the width, centred) and the top of the area (bottom third).  Vertical
# frames keep the bottom ~22 % free: the platforms put their buttons and text there.
_SAFE = {
    "WIDE": {"bottom": 0.09, "width": 0.80, "top": 1.0 / 3.0},
    "VERTICAL": {"bottom": 0.24, "width": 0.76, "top": 1.0 / 3.0 + 0.02},
}
LINE_HEIGHT = 1.25      # line pitch as a multiple of the font size (an estimate, for the checks)


def style_key(name: Any) -> str | None:
    """``"Bold Pop"``, ``"bold-pop"``, ``"BOLD_POP"``... -> ``"BOLD_POP"``; None if unknown."""
    key = re.sub(r"[^A-Z0-9]+", "_", str(name or "").upper()).strip("_")
    key = _STYLE_ALIASES.get(key, key)
    return key if key in STYLES else None


def frame_kind(res_x: float, res_y: float) -> str:
    """``"VERTICAL"`` for a portrait frame (9:16 and the like), else ``"WIDE"``."""
    return "VERTICAL" if res_y > res_x * 1.1 else "WIDE"


def safe_area(kind: str) -> dict[str, float]:
    return dict(_SAFE.get(kind, _SAFE["WIDE"]))


def _font_px(style: dict, res_x: float, res_y: float) -> int:
    kind = frame_kind(res_x, res_y)
    frac = style["size_vertical"] if kind == "VERTICAL" else style["size"]
    return max(8, int(round(frac * res_y)))


def default_max_chars(name: str, res_x: float, res_y: float) -> int:
    """Characters per line that fit the safe width at the style's size (10-42)."""
    style = STYLES[style_key(name) or "CLEAN"]
    px = _font_px(style, res_x, res_y)
    ratio = 0.64 if (style["use_bold"] or style["uppercase"]) else 0.54
    chars = int(safe_area(frame_kind(res_x, res_y))["width"] * res_x / (px * ratio))
    return max(10, min(42, chars))


# -- position (#542) ---------------------------------------------------------------------

# Where a caption sits: ``anchor_y`` of the text strip and how its height is measured.
POSITIONS: dict[str, dict[str, str]] = {
    "BOTTOM": {"label": "Bottom", "description": "Near the bottom of the frame (above the margin)"},
    "TOP": {"label": "Top", "description": "Near the top of the frame (below the margin), e.g. off the UI a "
                                           "tutorial shows at the bottom"},
    "MIDDLE": {"label": "Middle", "description": "In the middle of the frame"},
}
# The margin when none is given (0): the safe area's own.  At the top a vertical frame keeps
# about 12 % free (the platforms' top bar).
_TOP_MARGIN = {"WIDE": 0.09, "VERTICAL": 0.12}
MAX_MARGIN = 0.45
_POSITION_ALIASES = {"": "BOTTOM", "DEFAULT": "BOTTOM", "LOWER": "BOTTOM", "DOWN": "BOTTOM", "UPPER": "TOP",
                     "UP": "TOP", "CENTER": "MIDDLE", "CENTRE": "MIDDLE", "MID": "MIDDLE"}


def position_key(name: Any) -> str | None:
    """``"top"``, ``"Bottom"``, ``"centre"``... -> ``"TOP"`` / ``"BOTTOM"`` / ``"MIDDLE"``; None if unknown."""
    key = re.sub(r"[^A-Z]+", "_", str(name or "").upper()).strip("_")
    key = _POSITION_ALIASES.get(key, key)
    return key if key in POSITIONS else None


def default_margin(position: str, kind: str) -> float:
    """The margin (fraction of the frame's height) a position uses when none is given."""
    if position == "TOP":
        return _TOP_MARGIN.get(kind, _TOP_MARGIN["WIDE"])
    if position == "MIDDLE":
        return 0.0
    return safe_area(kind)["bottom"]


def placement(position: str, margin: float, res_x: float, res_y: float) -> tuple[str, tuple[float, float]]:
    """``(anchor_y, location)`` of a caption at *position* with *margin* (fraction of the frame's
    height from that edge; 0: the safe area's) in a *res_x* x *res_y* frame.  Bottom: the
    caption's bottom edge on the margin; Top: its top edge; Middle: centred (no margin)."""
    key = position_key(position) or "BOTTOM"
    kind = frame_kind(res_x, res_y)
    m = float(margin or 0.0)
    m = default_margin(key, kind) if m <= 0.0 else min(m, MAX_MARGIN)
    if key == "TOP":
        return "TOP", (0.5, round(1.0 - m, 6))
    if key == "MIDDLE":
        return "CENTER", (0.5, 0.5)
    return "BOTTOM", (0.5, round(m, 6))


def resolve_style(name: str, res_x: float, res_y: float, position: str = "BOTTOM",
                  margin: float = 0.0) -> dict[str, Any]:
    """The text-strip properties for style *name* at *res_x* x *res_y* (``"props"``), and how
    captions are made with it (``"uppercase"``, ``"max_lines"``, ``"pop"``, ``"highlight"``,
    ``"font_file"``, ``"kind"``); placed at *position* with *margin* (:func:`placement`)."""
    key = style_key(name)
    if key is None:
        raise CaptionsError("Unknown caption style {!r}: pick one of {:s}.".format(
            name, ", ".join(s["label"] for s in STYLES.values())))
    s = STYLES[key]
    kind = frame_kind(res_x, res_y)
    safe = safe_area(kind)
    pos = position_key(position) or "BOTTOM"
    anchor_y, location = placement(pos, margin, res_x, res_y)
    props = {
        "font_size": _font_px(s, res_x, res_y), "color": tuple(s["color"]), "use_bold": s["use_bold"],
        "use_italic": False, "use_outline": s["use_outline"], "outline_color": tuple(s["outline_color"]),
        "outline_width": s["outline_width"], "use_shadow": s["use_shadow"], "shadow_color": tuple(s["shadow_color"]),
        "shadow_offset": s["shadow_offset"], "shadow_blur": s["shadow_blur"], "use_box": s["use_box"],
        "anchor_x": "CENTER", "anchor_y": anchor_y, "alignment_x": "CENTER",
        "location": location, "wrap_width": safe["width"],
    }
    if s["use_box"]:
        props.update(box_color=tuple(s["box_color"]), box_margin=s["box_margin"], box_roundness=s["box_roundness"])
    return {"key": key, "label": s["label"], "kind": kind, "props": props, "uppercase": s["uppercase"],
            "max_lines": s["max_lines"], "pop": s["pop"], "highlight": s["highlight"], "font_file": FONT_FILE,
            "position": pos, "margin": max(0.0, min(float(margin or 0.0), MAX_MARGIN))}


# -- animation presets (#542) -----------------------------------------------------------------

ANIM_STYLE = "STYLE"        # the style's own: Bold Pop pops its font size, the others don't move
ANIMATIONS: dict[str, dict[str, str]] = {
    ANIM_STYLE: {"label": "Style's", "description": "The style's own: Bold Pop pops in, the others don't move"},
    "NONE": {"label": "None", "description": "No animation: each caption is just there"},
    "FADE": {"label": "Fade", "description": "Each caption fades in and out"},
    "SLIDE_UP": {"label": "Slide Up", "description": "Each caption rises into place as it fades in, and fades out"},
    "TYPEWRITER": {"label": "Typewriter", "description": "Each caption is typed out letter by letter (one line "
                                                         "per caption)"},
    "POP": {"label": "Pop", "description": "Each caption pops in: it grows into place with a small overshoot"},
}
_ANIM_ALIASES = {"": ANIM_STYLE, "DEFAULT": ANIM_STYLE, "STYLES": ANIM_STYLE, "OFF": "NONE", "NO": "NONE",
                 "STATIC": "NONE", "FADE_IN": "FADE", "FADE_IN_OUT": "FADE", "SLIDE": "SLIDE_UP", "SLIDEUP": "SLIDE_UP",
                 "TYPE": "TYPEWRITER", "TYPE_WRITER": "TYPEWRITER", "TYPING": "TYPEWRITER", "SCALE": "POP",
                 "POP_IN": "POP"}
# The text-strip properties the presets key (relative to the strip).  Never ``location``, the
# text, the length or the channel: those are the caption's signature (#552), so an animation
# Coworker sets never reads as an edit by hand.
ANIMATED_PATHS = ("blend_alpha", "transform.offset_y", "transform.scale_x", "transform.scale_y", "crop.max_x",
                  "font_size")
# Timing (seconds) and amounts.
FADE_IN_S = 0.25
FADE_OUT_S = 0.2
SLIDE_DISTANCE = 0.035      # of the frame's height
POP_FROM = 0.6              # Pop: the scale it starts at, its overshoot, when the overshoot is
POP_OVER = 1.08
POP_OVER_AT = 0.6
TYPE_CPS = 30.0             # Typewriter: characters a second ...
TYPE_MAX_S = 1.0            # ... and at most this long
STYLE_POP_FROM = 0.78       # Bold Pop's own font-size pop (as before #542): 78 % to full in 3 frames
STYLE_POP_FRAMES = 3
MIN_ANIMATED = 4            # a caption shorter than this many frames isn't animated


def animation_key(name: Any) -> str | None:
    """``"slide up"``, ``"Fade"``, ``"style"``, ``None``... -> an :data:`ANIMATIONS` key; None if unknown."""
    key = re.sub(r"[^A-Z]+", "_", str(name or "").upper()).strip("_")
    key = _ANIM_ALIASES.get(key, key)
    return key if key in ANIMATIONS else None


def one_line(animation: Any) -> bool:
    """The animation lays each caption out on one line (Typewriter reveals one line)."""
    return animation_key(animation) == "TYPEWRITER"


def _windows(length: int, fps: float) -> tuple[int, int]:
    """Frames for the way in and the way out of a caption *length* frames long: about
    :data:`FADE_IN_S` / :data:`FADE_OUT_S`, at most a third / a quarter of the caption; 0 when
    that leaves fewer than 2 frames (no animation that way)."""
    fps = max(1.0, float(fps))
    n_in = min(int(round(FADE_IN_S * fps)), length // 3)
    n_out = min(int(round(FADE_OUT_S * fps)), length // 4)
    return (n_in if n_in >= 2 else 0), (n_out if n_out >= 2 else 0)


def reveal_crops(text: str, measure: Callable[[str, float], float], font_px: float, res_x: float,
                 x_center: float = 0.5) -> list[tuple[int, int]]:
    """Typewriter: ``(characters shown, crop from the right in pixels)`` for a line of *text*
    centred at *x_center* (0-1 of the frame width), one entry per visible character (spaces
    join the next one); the last one shows it all (crop 0)."""
    text = str(text or "")
    if not text.strip() or res_x <= 0:
        return []
    width = float(measure(text, font_px))
    left = float(x_center) * res_x - width / 2.0
    out: list[tuple[int, int]] = []
    for k in range(1, len(text) + 1):
        if text[k - 1].isspace():
            continue
        right = left + float(measure(text[:k], font_px))
        out.append((k, max(0, int(math.floor(res_x - right)))))
    if out:
        out[-1] = (len(text), 0)
    return out


def animation_tracks(animation: Any, style: str, frame_start: int, frame_end: int, fps: float,
                     res_x: float, res_y: float, font_size: float = 0.0, lines: int = 1,
                     anchor_y: str = "BOTTOM", location: Sequence[float] = (0.5, 0.09),
                     reveal: Sequence[tuple[int, int]] | None = None) -> list[dict[str, Any]]:
    """The keyframes of animation *animation* for a caption in style *style* shown from
    *frame_start* to *frame_end* (end exclusive) -- a description, applied by ``captions_ops``.

    Each track: ``{"path"`` (relative to the text strip, one of :data:`ANIMATED_PATHS`),
    ``"keys": [(frame, value)]``, ``"interpolation"`` ("BEZIER" or "CONSTANT"), ``"steady"``
    (the value once the caption is up)``}``.  Keys lie on the caption's own frames, except the last key of a way out,
    on *frame_end* (the first frame after it).  A caption too short for an animation gets none
    (:data:`MIN_ANIMATED`); the way in takes at most a third of it, the way out a quarter.
    *font_size*, *lines*, *anchor_y* and *location*: the caption's layout (Pop grows around its
    middle, :func:`pop_middle`; Bold Pop's own pop scales its font size).  *reveal*: :func:`reveal_crops` of its
    line, for Typewriter (none: no typewriter)."""
    key = animation_key(animation) or ANIM_STYLE
    fs, fe = int(frame_start), int(frame_end)
    length = fe - fs
    if key == ANIM_STYLE:
        st = STYLES.get(style_key(style) or "", {})
        if not st.get("pop") or length < STYLE_POP_FRAMES + 2 or font_size <= 0:
            return []
        return [{"path": "font_size", "interpolation": "BEZIER", "steady": float(font_size),
                 "keys": [(fs, float(font_size) * STYLE_POP_FROM), (fs + STYLE_POP_FRAMES, float(font_size))]}]
    if key == "NONE" or length < MIN_ANIMATED:
        return []
    n_in, n_out = _windows(length, fps)
    tracks: list[dict[str, Any]] = []

    def fade(with_in: bool = True) -> None:
        keys: list[tuple[int, float]] = []
        if with_in and n_in:
            keys += [(fs, 0.0), (fs + n_in, 1.0)]
        if n_out:
            keys += [(fe - n_out, 1.0), (fe, 0.0)]
        if keys:
            tracks.append({"path": "blend_alpha", "interpolation": "BEZIER", "steady": 1.0, "keys": keys})

    if key == "FADE":
        fade()
    elif key == "SLIDE_UP":
        fade()
        if n_in:
            rise = round(SLIDE_DISTANCE * float(res_y), 2)
            tracks.append({"path": "transform.offset_y", "interpolation": "BEZIER", "steady": 0.0,
                           "keys": [(fs, -rise), (fs + n_in, 0.0)]})
    elif key == "POP" and n_in:
        over = max(fs + 1, min(fs + n_in - 1, fs + int(round(n_in * POP_OVER_AT))))
        keys = [(fs, POP_FROM), (over, POP_OVER), (fs + n_in, 1.0)]
        for axis in ("transform.scale_x", "transform.scale_y"):
            tracks.append({"path": axis, "interpolation": "BEZIER", "steady": 1.0, "keys": list(keys)})
        # A strip scales around the frame's centre (its transform origin, 0.5 / 0.5): an offset
        # that follows the scale keeps the caption's own middle where it is.  (The origin can't
        # be set to the caption instead: on a text strip it is measured on the text's box, live
        # check #542.)  Captions are centred across, so only the height moves.
        middle = pop_middle(lines, font_size, res_y, anchor_y, location)
        shift = (middle - 0.5) * float(res_y)
        tracks.append({"path": "transform.offset_y", "interpolation": "BEZIER", "steady": 0.0,
                       "keys": [(f, round(shift * (1.0 - v), 3)) for f, v in keys]})
    elif key == "TYPEWRITER" and reveal:
        steps = list(reveal)
        span = min(len(steps) / TYPE_CPS * max(1.0, float(fps)), TYPE_MAX_S * max(1.0, float(fps)), length * 0.5)
        keys: list[tuple[int, float]] = []
        n = len(steps)
        for i, (_chars, crop) in enumerate(steps):
            f = fs + int(math.floor(span * i / n))
            if keys and keys[-1][0] == f:
                keys[-1] = (f, float(crop))         # several characters on one frame: the last of them
            else:
                keys.append((f, float(crop)))
        if len(keys) >= 2:
            tracks.append({"path": "crop.max_x", "interpolation": "CONSTANT", "steady": 0.0, "keys": keys})
        fade(with_in=False)
    return tracks


def animation_paths(animation: Any, style: str) -> tuple[str, ...]:
    """The properties (:data:`ANIMATED_PATHS`) animation *animation* keys on a caption in
    *style*, for a caption long enough to have all of it; empty when it keys none (None, or
    the style's own on a style that doesn't pop)."""
    tracks = animation_tracks(animation, style, 0, 600, 30.0, 1920, 1080, 50.0, 1, "BOTTOM", (0.5, 0.09),
                              [(1, 10), (2, 0)])
    return tuple(t["path"] for t in tracks)


def pop_middle(lines: int, font_size: float, res_y: float, anchor_y: str, location: Sequence[float]) -> float:
    """The middle of a caption (0-1 of the frame's height, from the bottom): *lines* lines of
    *font_size* px from its anchor at *location*."""
    height = max(1.0, float(lines)) * float(font_size) * LINE_HEIGHT / max(1.0, float(res_y))
    y = float(location[1])
    middle = y + height / 2.0 if anchor_y == "BOTTOM" else (y - height / 2.0 if anchor_y == "TOP" else y)
    return min(1.0, max(0.0, middle))


def animation_window(tracks: Sequence[dict]) -> tuple[int, int] | None:
    """The first and last keyed frame of *tracks* (None: no keys)."""
    frames = [f for t in tracks for f, _v in t["keys"]]
    return (min(frames), max(frames)) if frames else None


# -- speakers (#542) ---------------------------------------------------------------------------

_DUP_SUFFIX = re.compile(r"\.\d{3,}$")
_MEDIA_EXT = re.compile(r"\.(?:wav|mp3|flac|ogg|oga|aac|m4a|opus|aif|aiff|wma|mp4|mov|mkv|avi|webm|m4v|mpg|mpeg)$",
                        re.IGNORECASE)


def speaker_name(strip_name: Any) -> str:
    """A speaker's name from their strip's: ``"ana_mic.wav.001"`` -> ``"Ana mic"`` (no
    ``.001`` copy number, no file extension, ``_`` and ``-`` as spaces, a capital first)."""
    name = _DUP_SUFFIX.sub("", str(strip_name or "").strip())
    name = _MEDIA_EXT.sub("", name)
    name = " ".join(re.sub(r"[_\-]+", " ", name).split())
    if not name:
        return "Speaker"
    return name[0].upper() + name[1:]


CHANNEL_NAME_BYTES = 63         # Blender's limit for a channel's name, in UTF-8 bytes
SPEAKER_NAME_CHARS = 40


def _fit_bytes(name: str, limit: int = CHANNEL_NAME_BYTES) -> str:
    """*name* cut so its UTF-8 encoding fits *limit* bytes (never inside a character)."""
    return name.encode("utf-8")[:limit].decode("utf-8", "ignore")


def channel_names(speaker: str | None) -> tuple[str, str]:
    """The names of a speaker's caption channel and its highlight channel (``"Captions: Ana"``);
    the plain ones without a speaker."""
    if not speaker:
        return CHANNEL_NAME, HIGHLIGHT_CHANNEL_NAME
    return (_fit_bytes("{:s}: {:s}".format(CHANNEL_NAME, speaker)),
            _fit_bytes("{:s}: {:s}".format(HIGHLIGHT_CHANNEL_NAME, speaker)))


def plan_speakers(todo: Sequence[dict], style: str, position: str, settings: Sequence[dict] = (),
                  labels: bool = False) -> dict[str, dict[str, Any]]:
    """One speaker per media file of *todo* (the strips to caption, as :func:`plan_strips`
    gives them: ``{"name", "key"}``), in that order: ``{key: {"name", "style", "position",
    "label", "channel", "highlight_channel", "strips"}}``.

    *settings*: per speaker ``{"strip": a strip of theirs, "name", "style", "position"}``
    (each optional; empty: the run's *style* / *position*, the name from the strip,
    :func:`speaker_name`).  *labels*: each caption starts with ``"Name:"``."""
    by_strip = {str(s.get("strip") or ""): s for s in settings or () if s.get("strip")}
    out: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    for it in todo:
        key = it["key"]
        if key in out:
            out[key]["strips"].append(it["name"])
            continue
        mine = next((by_strip[n] for n in [it["name"]] + [x["name"] for x in todo if x["key"] == key]
                     if n in by_strip), {})
        name = " ".join(str(mine.get("name") or "").split())[:SPEAKER_NAME_CHARS] or speaker_name(it["name"])
        base, n = name, 2
        while name.lower() in used:             # two files of "Ana": Ana, Ana 2
            name = "{:s} {:d}".format(base, n)
            n += 1
        used.add(name.lower())
        # An unset style / position is the run's ("" would read as Clean / Bottom).
        st = (style_key(mine["style"]) if mine.get("style") else None) or style_key(style) or "CLEAN"
        pos = (position_key(mine["position"]) if mine.get("position") else None) or position_key(position) or "BOTTOM"
        chan, hl = channel_names(name)
        out[key] = {"name": name, "style": st, "position": pos, "label": (name + ":") if labels else "",
                    "channel": chan, "highlight_channel": hl, "strips": [it["name"]]}
    return out


def label_chars(chars: int, label: str) -> int:
    """Characters per line left for the words when every caption starts with *label* (at least 10)."""
    return max(10, int(chars) - (len(label) + 1 if label else 0))


def with_label(text: str, label: str) -> str:
    """*text* with the speaker's *label* in front (on its first line)."""
    return "{:s} {:s}".format(label, text) if label else text


def without_label(text: str, label: str) -> str:
    """*text* without the speaker's *label* in front (as :func:`with_label` put it, any case)."""
    if label and text[:len(label)].lower() == label.lower():
        return text[len(label):].lstrip(" ")
    return text


# -- SRT ----------------------------------------------------------------------------

def srt_time(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "{:02d}:{:02d}:{:02d},{:03d}".format(h, m, s, ms)


def format_srt(cues: Iterable[tuple[float, float, str]]) -> str:
    """SubRip text: numbered cues ``start --> end``, the text, a blank line."""
    out = []
    n = 0
    for start, end, text in cues:
        lines = [ln.strip() for ln in str(text).replace("\r", "").split("\n") if ln.strip()]
        if not lines:
            continue
        n += 1
        end = max(end, start + 0.001)
        out.append("{:d}\n{:s} --> {:s}\n{:s}\n".format(n, srt_time(start), srt_time(end), "\n".join(lines)))
    return "\n".join(out)


_TIME = r"(\d{1,3}):(\d{1,2}):(\d{1,2})(?:[,.](\d{1,3}))?"
_TIMING = re.compile(r"^\s*" + _TIME + r"\s*-+>\s*" + _TIME + r"(?:\s.*)?$")


def _seconds(h: str, m: str, s: str, frac: str | None) -> float:
    ms = int((frac or "0").ljust(3, "0")[:3])
    return int(h) * 3600 + int(m) * 60 + int(s) + ms / 1000.0


def strip_tags(text: str) -> str:
    """Without ``<i>``/``<b>``/``<u>``/``<font ...>`` tags, ``{\\an8}``-style codes and entities."""
    text = re.sub(r"</?\s*[a-zA-Z][^>]*>", "", text)
    text = re.sub(r"\{\\[^}]*\}", "", text)
    return html.unescape(text)


def parse_srt(text: str) -> tuple[list[dict[str, Any]], int]:
    """SubRip text -> ``([{"start", "end", "text"}], skipped)``.  Tolerant: a BOM, CRLF or CR
    line ends, cues of several lines, missing cue numbers or blank lines, ``.`` for ``,``,
    tags stripped.  A cue with a broken timing line, an end before its start or no text is
    skipped and counted."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[dict[str, Any]] = []
    skipped = 0
    for block in re.split(r"\n[ \t]*\n", text):
        lines = [ln.rstrip() for ln in block.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            continue
        marks = [i for i, ln in enumerate(lines) if "-->" in ln]
        if not marks:
            if not (len(lines) == 1 and lines[0].strip().isdigit()):
                skipped += 1
            continue
        for k, i in enumerate(marks):
            stop = marks[k + 1] if k + 1 < len(marks) else len(lines)
            body = lines[i + 1:stop]
            if k + 1 < len(marks) and body and body[-1].strip().isdigit():
                body = body[:-1]        # the next cue's number (no blank line between them)
            m = _TIMING.match(lines[i])
            if not m:
                skipped += 1
                continue
            start, end = _seconds(*m.groups()[0:4]), _seconds(*m.groups()[4:8])
            body_text = "\n".join(ln for ln in (strip_tags(b).strip() for b in body) if ln)
            if end <= start or not body_text:
                skipped += 1
                continue
            cues.append({"start": start, "end": end, "text": body_text})
    cues.sort(key=lambda c: c["start"])
    return cues, skipped


# -- Translate -------------------------------------------------------------------------

def batch_text(texts: Sequence[str]) -> str:
    """Captions as one numbered text for a single translation question: ``[1] ...`` per
    line (a caption's own line breaks become spaces; they're laid out again after)."""
    return "\n".join("[{:d}] {:s}".format(i + 1, " ".join(str(t).split())) for i, t in enumerate(texts))


def parse_batch_reply(reply: str, count: int) -> dict[int, str]:
    """``{number: text}`` out of a reply to :func:`batch_text` (``[1] ...``, ``1. ...`` or
    ``1) ...``); numbers outside 1..*count* are ignored."""
    out: dict[int, str] = {}
    for line in str(reply).replace("\r", "").split("\n"):
        m = re.match(r"^\s*(?:\[(\d+)\]|(\d+)[.):])\s*(.*?)\s*$", line)
        if not m:
            continue
        n = int(m.group(1) or m.group(2))
        if 1 <= n <= count and m.group(3):
            out[n] = m.group(3)
    return out


# -- the tool ----------------------------------------------------------------------

def check_tool_params(strips: Any, style: Any, language: Any) -> tuple[list[str], str, str | None]:
    """``add_captions``' arguments checked: ``(strip names, style key, language or None)``;
    raises :class:`CaptionsError` with what's wrong."""
    if strips is None:
        names: list[str] = []
    elif isinstance(strips, str):
        names = [strips] if strips.strip() else []
    else:
        try:
            names = [str(s) for s in strips if str(s).strip()]
        except TypeError:
            raise CaptionsError("strips must be a list of strip names") from None
    key = style_key(style)
    if key is None:
        raise CaptionsError("Unknown style {!r}: use one of {:s}.".format(
            style, ", ".join("{:s} ({:s})".format(k, s["label"]) for k, s in STYLES.items())))
    lang = str(language or "").strip().lower().replace("-", "_")
    if lang in ("", "auto", "detect"):
        return names, key, None
    if not LANGUAGE_RE.match(lang):
        raise CaptionsError("language should be a code like \"en\" or \"es\" (or \"auto\"), not {!r}".format(language))
    return names, key, lang


def check_look_params(animation: Any, position: Any, margin: Any) -> tuple[str, str, float]:
    """``add_captions``' look (#542) checked: ``(animation key, position key, margin as a
    fraction of the frame's height)``.  *margin* is in percent (0 or none: the safe area's);
    raises :class:`CaptionsError` with what's wrong."""
    anim = animation_key(animation)
    if anim is None:
        raise CaptionsError("Unknown animation {!r}: use one of {:s}.".format(
            animation, ", ".join("{:s} ({:s})".format(k, a["label"]) for k, a in ANIMATIONS.items())))
    pos = position_key(position)
    if pos is None:
        raise CaptionsError("Unknown position {!r}: use BOTTOM, TOP or MIDDLE.".format(position))
    try:
        pct = float(margin or 0.0)
    except (TypeError, ValueError):
        raise CaptionsError("margin is a percentage of the frame's height (0-45), not {!r}".format(margin)) from None
    if math.isnan(pct) or pct < 0 or pct > MAX_MARGIN * 100:
        raise CaptionsError("margin is a percentage of the frame's height, 0-45 (0: the safe area's), not {!r}".format(
            margin))
    return anim, pos, pct / 100.0


def check_speakers(speakers: Any, settings: Any) -> tuple[bool, list[dict[str, str]]]:
    """``add_captions``' *speakers* (one channel per speaker) and *speaker_settings* (a list of
    ``{"strip", "name", "style", "position"}``) checked: ``(on, settings)``.  Settings turn
    speakers on; a style or position must be known."""
    out: list[dict[str, str]] = []
    if settings:
        if isinstance(settings, dict):
            settings = [settings]
        if not isinstance(settings, (list, tuple)):
            raise CaptionsError("speaker_settings is a list of {\"strip\", \"name\", \"style\", \"position\"}")
        for item in settings:
            if not isinstance(item, dict) or not str(item.get("strip") or "").strip():
                raise CaptionsError("each of speaker_settings needs the \"strip\" of that speaker")
            row = {"strip": str(item["strip"]).strip()}
            if str(item.get("name") or "").strip():
                row["name"] = " ".join(str(item["name"]).split())[:SPEAKER_NAME_CHARS]
            if item.get("style"):
                if style_key(item["style"]) is None:
                    raise CaptionsError("Unknown style {!r} for {:s}: use one of {:s}.".format(
                        item["style"], row["strip"], ", ".join(STYLES)))
                row["style"] = style_key(item["style"]) or "CLEAN"
            if item.get("position"):
                if position_key(item["position"]) is None:
                    raise CaptionsError("Unknown position {!r} for {:s}: use BOTTOM, TOP or MIDDLE.".format(
                        item["position"], row["strip"]))
                row["position"] = position_key(item["position"]) or "BOTTOM"
            out.append(row)
    return bool(speakers) or bool(out), out


def measure_fallback(text: str, font_px: float) -> float:
    """A rough text width when the font can't be measured (only for the safe-area checks)."""
    return len(text) * font_px * 0.54


def text_box(lines: Sequence[str], font_px: float, measure: Callable[[str, float], float] = measure_fallback) -> tuple[float, float]:
    """``(width, height)`` in pixels of *lines* at *font_px* (height from :data:`LINE_HEIGHT`)."""
    width = max((measure(ln, font_px) for ln in lines), default=0.0)
    return width, len(lines) * font_px * LINE_HEIGHT


# -- a caption laid out again for another frame (Make Vertical, #497) ---------------------

def relayout_limits(name: str, chars: int, lines: int, old_res: Sequence[float] | None,
                    new_res: Sequence[float]) -> tuple[int, int]:
    """Characters per line and lines for a caption moving from *old_res* to *new_res*: the
    style's own number for the new frame when the caption used the style's number for the old
    one (or none is known), else the caption's own number, at most what fits the new frame."""
    key = style_key(name) or "CLEAN"
    style = STYLES[key]
    fit = default_max_chars(key, *new_res)
    chars = int(chars or 0)
    if chars <= 0 or old_res is None or chars == default_max_chars(key, *old_res):
        new_chars = fit
    else:
        new_chars = min(chars, fit)
    if style["highlight"]:
        new_lines = 1
    else:
        new_lines = 1 if int(lines or style["max_lines"]) <= 1 else 2
    return new_chars, new_lines


def hand_edits(look: dict[str, Any], name: str, res_x: float, res_y: float, position: str = "BOTTOM",
               margin: float = 0.0) -> list[str]:
    """What of a caption line's layout differs from what style *name* gives at *res_x* x *res_y*
    (the frame it was made for) at *position* / *margin* (#542); empty when it is as the style
    made it.

    *look*: ``location``, ``font_size`` (the steady size, not a pop key's), ``wrap_width``,
    ``anchor_x`` / ``anchor_y`` and, if the strip has one, ``transform``
    (``offset_x, offset_y, scale_x, scale_y, rotation``; the steady values, not an animation's
    keys).  Text, colours and timing are not layout: a caption whose words were changed by hand
    is still laid out again."""
    key = style_key(name)
    if key is None:
        return ["style"]
    props = resolve_style(key, res_x, res_y, position, margin)["props"]
    out = []
    loc = tuple(look.get("location") or (0.0, 0.0))
    if len(loc) < 2 or abs(loc[0] - props["location"][0]) > 1e-3 or abs(loc[1] - props["location"][1]) > 1e-3:
        out.append("position")
    if abs(float(look.get("font_size") or 0.0) - props["font_size"]) > 0.6:
        out.append("size")
    wrap = 1.0 if STYLES[key]["highlight"] else props["wrap_width"]
    if look.get("wrap_width") is not None and abs(float(look["wrap_width"]) - wrap) > 1e-3:
        out.append("wrap width")
    if (look.get("anchor_x", "CENTER"), look.get("anchor_y", "BOTTOM")) != (props["anchor_x"], props["anchor_y"]):
        out.append("anchor")
    tr = look.get("transform")
    if tr is not None:
        ox, oy, sx, sy, rot = (float(v) for v in tr)
        if abs(ox) > 1e-3 or abs(oy) > 1e-3 or abs(sx - 1.0) > 1e-4 or abs(sy - 1.0) > 1e-4 or abs(rot) > 1e-4:
            out.append("transform")
    return out


def relayout(text: str, name: str, res_x: float, res_y: float, frame_start: int, frame_end: int,
             chars: int = 0, lines: int = 0, old_res: Sequence[float] | None = None,
             word_frames: Sequence[float] | None = None, position: str = "BOTTOM", margin: float = 0.0,
             label: str = "") -> dict[str, Any]:
    """A caption (its *text* as shown, from *frame_start* to *frame_end*, end exclusive) laid
    out again in style *name* for a *res_x* x *res_y* frame, by the same rules Auto Captions
    uses there: the style's size and place (:func:`resolve_style`, at the caption's own
    *position* / *margin*, #542), its characters per line (:func:`relayout_limits`) and lines.
    A speaker's *label* (``"Ana:"``) stays in front of each piece and isn't a word.

    Words that don't fit the style's lines in the new frame are split into several captions
    inside the same time, where :func:`build_captions` would split them; each word keeps its
    time (*word_frames*: each word's first frame, when known; else the time is shared out by
    word length, as Import SRT does).  The words themselves never change.

    Returns ``{"style": resolve_style(...), "chars", "lines", "pieces": [{"text", "lines",
    "frame_start", "frame_end", "words": [{"w", "frame"}]}]}`` (one piece when it fits)."""
    st = resolve_style(name, res_x, res_y, position, margin)
    chars, lines = relayout_limits(st["key"], chars, lines, old_res, (res_x, res_y))
    frame_start, frame_end = int(frame_start), int(max(frame_end, frame_start + 1))
    text = without_label(" ".join(str(text or "").split()), label) if label else text
    words = str(text or "").split()
    if word_frames is not None and len(word_frames) == len(words) and words:
        starts, prev = [], float(frame_start)
        for i, f in enumerate(word_frames):
            f = float(frame_start) if i == 0 else min(max(float(f), prev), float(frame_end))
            starts.append(f)
            prev = f
    else:
        starts = [w["start"] for w in even_words(" ".join(words), frame_start, frame_end)]
    timed = [{"w": w, "start": s, "end": starts[i + 1] if i + 1 < len(words) else float(frame_end)}
             for i, (w, s) in enumerate(zip(words, starts))]

    fit = label_chars(chars, label) if label else chars     # the label takes room on the first line

    def piece(group: list[dict], fs: int, fe: int) -> dict[str, Any]:
        ln = layout_lines([w["w"] for w in group], fit, lines, force=True) or []
        if label and ln:
            ln[0] = with_label(ln[0], label)
            if st["uppercase"]:
                ln[0] = ln[0].upper()           # the label in the style's case, as _place writes it
        return {"text": "\n".join(ln), "lines": ln, "frame_start": fs, "frame_end": fe,
                "words": [{"w": w["w"], "frame": int(round(w["start"]))} for w in group], "label": label}

    whole = [piece(timed, frame_start, frame_end)]
    if len(words) < 2 or layout_lines(words, fit, lines) is not None:
        return {"style": st, "chars": chars, "lines": lines, "pieces": whole}
    # build_captions' split, without its clean-up of the words (they are kept exactly as shown):
    # at sentence ends first, then where a stretch reads best.
    stretches: list[list[dict]] = [[timed[0]]]
    for prev, w in zip(timed, timed[1:]):
        if is_sentence_end(prev["w"]):
            stretches.append([])
        stretches[-1].append(w)
    groups = [g for seg in stretches for g in _split(seg, fit, lines, math.inf)]
    bounds = [frame_start] + [int(round(g[0]["start"])) for g in groups[1:]] + [frame_end]
    if any(b >= a_next for b, a_next in zip(bounds, bounds[1:])):
        return {"style": st, "chars": chars, "lines": lines, "pieces": whole}     # no frame for a piece
    pieces = [piece(g, bounds[i], bounds[i + 1]) for i, g in enumerate(groups)]
    return {"style": st, "chars": chars, "lines": lines, "pieces": pieces}


def relayout_report(laid_out: int, left: int, split: int = 0) -> str:
    """``"27 captions laid out for vertical (8 split to fit), 1 left as edited"``."""
    text = "{:d} caption{:s} laid out for vertical".format(laid_out, "" if laid_out == 1 else "s")
    if split:
        text += " ({:d} split to fit)".format(split)
    return text + ", {:d} left as edited".format(left)
