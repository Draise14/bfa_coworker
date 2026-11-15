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
* **SRT**: :func:`format_srt` and the tolerant :func:`parse_srt`.
* **Translate**: :func:`batch_text` / :func:`parse_batch_reply` (many captions in one
  question, written back one strip each).

``captions_ops`` builds the text strips and the operators on top of this.
"""

from __future__ import annotations

__all__ = (
    "CHANNEL_NAME",
    "HIGHLIGHT_CHANNEL_NAME",
    "LANGUAGE_RE",
    "NOT_TRANSCRIBED",
    "NO_TRANSCRIPTION",
    "SCHEMA",
    "SKIP_MUTED",
    "SKIP_NO_SPEECH",
    "SKIP_NO_TRANSCRIPT",
    "SKIP_RETIMED",
    "SKIP_TEXT",
    "STYLES",
    "TAG",
    "TRANSLATE_PREFIX",
    "CaptionsError",
    "batch_text",
    "build_captions",
    "caption_signature",
    "captions_for_strip",
    "captions_for_strips",
    "check_tool_params",
    "default_max_chars",
    "even_words",
    "format_srt",
    "frame_kind",
    "hand_edits",
    "highlight_left",
    "highlight_windows",
    "layout_lines",
    "media_frame",
    "normalise_words",
    "owned_by",
    "parse_batch_reply",
    "parse_srt",
    "partner_outcome",
    "pick_channel",
    "plan_strips",
    "relayout",
    "relayout_limits",
    "relayout_report",
    "replace_plan",
    "resolve_style",
    "safe_area",
    "separate",
    "srt_time",
    "strip_tags",
    "strips_report",
    "style_key",
    "to_frames",
    "transcript_words",
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
                 max_channel: int = MAX_CHANNEL) -> int | None:
    """The channel for the captions: a channel already named *Captions* above *above* if it
    (and the *need* - 1 above it) is free over *span*, else the first such free channel above
    *above*; None when there's no room.  *occupied* = channel -> (start, end) of its strips.
    *span* may also be a list of ``(start, end)``: each caption's own frames (#538), so a caption
    kept between them doesn't push the new ones onto another channel."""
    spans = list(span) if span and isinstance(span[0], (tuple, list)) else [span]

    def free(ch: int) -> bool:
        for c in range(ch, ch + need):
            if c > max_channel:
                return False
            for a, b in occupied.get(c, ()):
                if any(a < s1 and s0 < b for s0, s1 in spans):
                    return False
        return True

    named = sorted(c for c, n in names.items() if n == CHANNEL_NAME and c > above)
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
SKIP_TEXT = {
    SKIP_NO_SPEECH: "no speech in the part it shows",
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


def resolve_style(name: str, res_x: float, res_y: float) -> dict[str, Any]:
    """The text-strip properties for style *name* at *res_x* x *res_y* (``"props"``), and how
    captions are made with it (``"uppercase"``, ``"max_lines"``, ``"pop"``, ``"highlight"``,
    ``"font_file"``, ``"kind"``)."""
    key = style_key(name)
    if key is None:
        raise CaptionsError("Unknown caption style {!r}: pick one of {:s}.".format(
            name, ", ".join(s["label"] for s in STYLES.values())))
    s = STYLES[key]
    kind = frame_kind(res_x, res_y)
    safe = safe_area(kind)
    props = {
        "font_size": _font_px(s, res_x, res_y), "color": tuple(s["color"]), "use_bold": s["use_bold"],
        "use_italic": False, "use_outline": s["use_outline"], "outline_color": tuple(s["outline_color"]),
        "outline_width": s["outline_width"], "use_shadow": s["use_shadow"], "shadow_color": tuple(s["shadow_color"]),
        "shadow_offset": s["shadow_offset"], "shadow_blur": s["shadow_blur"], "use_box": s["use_box"],
        "anchor_x": "CENTER", "anchor_y": "BOTTOM", "alignment_x": "CENTER",
        "location": (0.5, safe["bottom"]), "wrap_width": safe["width"],
    }
    if s["use_box"]:
        props.update(box_color=tuple(s["box_color"]), box_margin=s["box_margin"], box_roundness=s["box_roundness"])
    return {"key": key, "label": s["label"], "kind": kind, "props": props, "uppercase": s["uppercase"],
            "max_lines": s["max_lines"], "pop": s["pop"], "highlight": s["highlight"], "font_file": FONT_FILE}


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


def hand_edits(look: dict[str, Any], name: str, res_x: float, res_y: float) -> list[str]:
    """What of a caption line's layout differs from what style *name* gives at *res_x* x *res_y*
    (the frame it was made for); empty when it is as the style made it.

    *look*: ``location``, ``font_size`` (the steady size, not a pop key's), ``wrap_width``,
    ``anchor_x`` / ``anchor_y`` and, if the strip has one, ``transform``
    (``offset_x, offset_y, scale_x, scale_y, rotation``).  Text, colours and timing are not
    layout: a caption whose words were changed by hand is still laid out again."""
    key = style_key(name)
    if key is None:
        return ["style"]
    props = resolve_style(key, res_x, res_y)["props"]
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
             word_frames: Sequence[float] | None = None) -> dict[str, Any]:
    """A caption (its *text* as shown, from *frame_start* to *frame_end*, end exclusive) laid
    out again in style *name* for a *res_x* x *res_y* frame, by the same rules Auto Captions
    uses there: the style's size and place (:func:`resolve_style`), its characters per line
    (:func:`relayout_limits`) and lines.

    Words that don't fit the style's lines in the new frame are split into several captions
    inside the same time, where :func:`build_captions` would split them; each word keeps its
    time (*word_frames*: each word's first frame, when known; else the time is shared out by
    word length, as Import SRT does).  The words themselves never change.

    Returns ``{"style": resolve_style(...), "chars", "lines", "pieces": [{"text", "lines",
    "frame_start", "frame_end", "words": [{"w", "frame"}]}]}`` (one piece when it fits)."""
    st = resolve_style(name, res_x, res_y)
    chars, lines = relayout_limits(st["key"], chars, lines, old_res, (res_x, res_y))
    frame_start, frame_end = int(frame_start), int(max(frame_end, frame_start + 1))
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

    def piece(group: list[dict], fs: int, fe: int) -> dict[str, Any]:
        ln = layout_lines([w["w"] for w in group], chars, lines, force=True) or []
        return {"text": "\n".join(ln), "lines": ln, "frame_start": fs, "frame_end": fe,
                "words": [{"w": w["w"], "frame": int(round(w["start"]))} for w in group]}

    whole = [piece(timed, frame_start, frame_end)]
    if len(words) < 2 or layout_lines(words, chars, lines) is not None:
        return {"style": st, "chars": chars, "lines": lines, "pieces": whole}
    # build_captions' split, without its clean-up of the words (they are kept exactly as shown):
    # at sentence ends first, then where a stretch reads best.
    stretches: list[list[dict]] = [[timed[0]]]
    for prev, w in zip(timed, timed[1:]):
        if is_sentence_end(prev["w"]):
            stretches.append([])
        stretches[-1].append(w)
    groups = [g for seg in stretches for g in _split(seg, chars, lines, math.inf)]
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
