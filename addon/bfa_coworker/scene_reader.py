# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Scene reader (Highlights H1, #611): the words of a whole edit as numbered sentences.  The part that
needs no Blender.

Edit by Transcript reads one media file at a time.  Highlights (and Suggest Edits after it) need the
whole scene: every speaking strip, every media file, merged in the order it plays now.

* **Input** (:func:`read_scene`): *sources*, one per media file the edit plays:
  ``{"key", "path", "words": [transcript words] | None, "pieces": [strips]}``.  A piece is
  ``{"name", "left", "right", "content_start", "offset", "channel"}`` (frames as in
  :func:`transcript_edit.timeline_words`) with an optional ``"speaker"`` and an optional ``"skip"``
  (``"muted"``, ``"retimed"``, ``"meta"``: the Blender side saw it and the reader does not read it).
  A source whose ``words`` is ``None`` was never transcribed.
* **Words** are placed where they play now (``transcript_edit.timeline_words``).  A clip used twice
  gives its words twice, each use with its own frames.
* **The same speech twice at once** (two microphones, camera sound plus recorder): the words of two
  different strips are cut into phrases; two phrases that play over the same frames *and* hold the
  same words are the same speech.  Phrases are settled best first (higher mean word confidence);
  a phrase gives up only the words a better one already holds, so a reply on another microphone or
  a strip cut shorter loses nothing it alone has.  The result lists what was dropped.
* **Sentences** are the lines ``transcript_edit.render`` makes; one longer than :data:`MAX_SENTENCE_S`
  is split at its longest pause.  Each carries the facts the code can count (seconds, words per
  second, fillers, pause before it, question, number, speaker change) and where its words come from
  (media key and word position in that file's transcript: what a stored pick keeps, since a cut
  renumbers every later sentence).
* **The report** says what was left out: muted strips, retimed strips and strips inside metas (and
  their share of the edit), clips without a transcript, speech dropped as a duplicate.
* **Windows** (:func:`windows`): overlapping runs of sentences that fit the engine's context, for
  one request each.

Importing this module needs no ``bpy``.
"""

from __future__ import annotations

__all__ = (
    "MAX_SENTENCE_S",
    "MAX_WINDOW_WORDS",
    "OVERLAP_S",
    "estimate_tokens",
    "format_sentence",
    "format_sentences",
    "read_scene",
    "version",
    "window_budget",
    "windows",
)

import difflib
import hashlib
import json
import math
import re
from typing import Any, Iterable, Sequence

from . import transcript_edit as te

MAX_SENTENCE_S = 20.0       # a sentence longer than this is split at its longest pause
RUN_GAP_S = 0.3             # a pause this long ends a phrase (for telling the same speech on two strips)
MAX_RUN_S = 12.0            # a phrase longer than this is split at its longest pause
SAME_SPEECH = 0.5           # two phrases overlap in time by this share of the shorter one and share this share of its words: the same speech
MAX_DUPLICATE_LINES = 5      # the report's messages name this many duplicate pairs, then count the rest
MATCH_S = 1.0               # a word matches a word of the other strip when they start this close
NEUTRAL_P = 0.5             # the confidence of a word the transcript gave none for
MAX_WINDOW_WORDS = 2400     # about 16 minutes of speech
OVERLAP_S = 60.0            # windows share this much speech
TOKENS_PER_WORD = 1.4       # a word in the model's tokens (one hour: 9 000 words, about 12 000 tokens)
TOKENS_PER_SENTENCE = 14    # "S214 [12.3s 2.1w/s]" in front of each sentence
INSTRUCTION_TOKENS = 1500   # the request's own instructions
ANSWER_TOKENS = 1000        # room kept for the answer
FIT = 0.7                   # of what is left, so a miscount does not overflow the context
MIN_WINDOW_WORDS = 150      # a context too small for this cannot be used
DEFAULT_CONTEXT = 32768

_NUMBER = re.compile(r"\d")
_QUESTION_END = ("?", "？")


def _fps(fps: float) -> float:
    try:
        out = float(fps)
    except (TypeError, ValueError):
        return 24.0
    return out if out > 0 else 24.0


# -- placing the words ---------------------------------------------------------------------------

def _place(source: dict, pieces: list[dict], fps: float, frame_start: int) -> list[dict[str, Any]]:
    """The words of *source* on the timeline for its *pieces*: one occurrence per word per use."""
    words = source.get("words") or []
    by_name = {p["name"]: p for p in pieces}
    out: list[dict[str, Any]] = []
    for o in te.timeline_words(words, pieces, fps, frame_start):
        piece = by_name[o["piece"]]
        w = words[o["i"]]
        p = w.get("p") if isinstance(w, dict) else None
        out.append({
            "text": te._text(w),                              # pylint: disable=protected-access
            "frame": int(o["frame"]),
            "end_frame": max(float(o["frame"]), min(float(o["end_frame"]), float(piece["right"]))),
            "media": source.get("key", ""), "i": int(o["i"]), "piece": piece["name"],
            "speaker": str(piece.get("speaker") or ""),
            "p": float(p) if isinstance(p, (int, float)) and not isinstance(p, bool) else None,
        })
    return out


def _runs(occs: Sequence[dict], fps: float) -> list[list[dict]]:
    """*occs* (one strip's words, in order) cut into phrases at pauses of :data:`RUN_GAP_S`."""
    runs: list[list[dict]] = []
    for o in occs:
        if runs and (o["frame"] - runs[-1][-1]["end_frame"]) / fps < RUN_GAP_S:
            runs[-1].append(o)
        else:
            runs.append([o])
    out: list[list[dict]] = []
    todo = list(reversed(runs))
    while todo:
        run = todo.pop()
        dur = (run[-1]["end_frame"] - run[0]["frame"]) / fps
        if dur <= MAX_RUN_S or len(run) < 2:
            out.append(run)
            continue
        cut = max(range(1, len(run)), key=lambda k: (run[k]["frame"] - run[k - 1]["end_frame"], -k))
        todo.append(run[cut:])
        todo.append(run[:cut])
    return out


def _score(run: Sequence[dict]) -> float:
    return sum(NEUTRAL_P if o["p"] is None else o["p"] for o in run) / max(1, len(run))


def _token(o: dict) -> str:
    return te.norm(o["text"]) or str(o["text"]).lower()


def _same_speech(run: list[dict], other: list[dict], fps: float) -> list[int]:
    """The positions in *run* whose word *other* (a phrase of another strip) holds too: the words
    that match in order and start within :data:`MATCH_S` of each other.  Empty unless that is at
    least :data:`SAME_SPEECH` of the shorter phrase's words (a phrase that merely overlaps in time,
    another person's reply, shares no words)."""
    sm = difflib.SequenceMatcher(None, [_token(o) for o in run], [_token(o) for o in other], autojunk=False)
    hit: list[int] = []
    for i, j, size in sm.get_matching_blocks():
        for k in range(size):
            if abs(run[i + k]["frame"] - other[j + k]["frame"]) / fps <= MATCH_S:
                hit.append(i + k)
    need = max(1, math.ceil(SAME_SPEECH * min(len(run), len(other))))
    return hit if len(hit) >= need else []


def _dedupe(per_piece: dict[str, list[dict]], rank: dict[str, int], fps: float) -> tuple[dict[str, list[dict]], list[dict]]:
    """Drop the words that another strip holds too (the same speech on two microphones).  Returns
    the words left per strip and the list of what was dropped.

    Phrases are settled best first (higher mean word confidence, then the strip order), all at
    once, so a phrase that has won never loses later.  Each phrase gives up only the words a
    better phrase of another strip, playing over the same frames, already has; the rest of it
    stays (a reply on another microphone, a strip cut shorter than the phrase)."""
    runs: list[dict[str, Any]] = []
    for name, occs in per_piece.items():
        for run in _runs(occs, fps):
            runs.append({"piece": name, "a": float(run[0]["frame"]), "b": float(run[-1]["end_frame"]),
                         "occs": run, "score": _score(run)})
    runs.sort(key=lambda r: (-round(r["score"], 9), rank[r["piece"]], r["a"]))
    bucket = max(1.0, 30.0 * fps)
    settled: dict[int, list[dict]] = {}
    drops: dict[tuple[str, str], dict] = {}
    for r in runs:
        lo, hi = int((r["a"] - MATCH_S * fps) // bucket), int((r["b"] + MATCH_S * fps) // bucket)
        seen: list[dict] = []
        for k in range(lo, hi + 1):
            for s in settled.get(k, ()):
                if s["piece"] != r["piece"] and s["occs"] and not any(s is x for x in seen):
                    seen.append(s)
        seen.sort(key=lambda s: (s["a"], rank[s["piece"]]))
        for s in seen:
            if not r["occs"]:
                break
            overlap = min(s["b"], r["b"]) - max(s["a"], r["a"])
            shorter = max(1.0, min(s["b"] - s["a"], r["b"] - r["a"]))
            if overlap <= 0 or overlap < SAME_SPEECH * shorter:
                continue
            hit = _same_speech(r["occs"], s["occs"], fps)
            if not hit:
                continue
            gone = [r["occs"][i] for i in hit]
            r["occs"] = [o for i, o in enumerate(r["occs"]) if i not in set(hit)]
            rec = drops.setdefault((s["piece"], r["piece"]), {
                "kept": s["piece"], "dropped": r["piece"], "kept_media": s["occs"][0]["media"],
                "dropped_media": gone[0]["media"], "words": 0,
                "first_frame": int(gone[0]["frame"]), "last_frame": int(gone[-1]["end_frame"])})
            rec["words"] += len(gone)
            rec["first_frame"] = min(rec["first_frame"], int(gone[0]["frame"]))
            rec["last_frame"] = max(rec["last_frame"], int(gone[-1]["end_frame"]))
        if r["occs"]:
            r["a"], r["b"] = float(r["occs"][0]["frame"]), float(r["occs"][-1]["end_frame"])
            for k in range(int(r["a"] // bucket), int(r["b"] // bucket) + 1):
                settled.setdefault(k, []).append(r)
    kept_words: dict[str, list[dict]] = {name: [] for name in per_piece}
    for r in runs:
        kept_words[r["piece"]].extend(r["occs"])
    for occs in kept_words.values():
        occs.sort(key=lambda o: (o["frame"], o["i"]))
    return kept_words, sorted(drops.values(), key=lambda d: (d["first_frame"], d["kept"], d["dropped"]))


# -- sentences -----------------------------------------------------------------------------------

def _split_long(occs: list[dict], fps: float) -> list[list[dict]]:
    """*occs* (one sentence) in parts of at most :data:`MAX_SENTENCE_S`, cut at the longest pause."""
    out: list[list[dict]] = []
    todo = [occs]
    while todo:
        part = todo.pop()
        dur = (max(o["end_frame"] for o in part) - part[0]["frame"]) / fps
        if dur <= MAX_SENTENCE_S or len(part) < 2:
            out.append(part)
            continue
        cut = max(range(1, len(part)), key=lambda k: (part[k]["frame"] - part[k - 1]["end_frame"], -k))
        todo.append(part[cut:])
        todo.append(part[:cut])
    out.sort(key=lambda p: p[0]["frame"])
    return out


def _sentences(merged: list[dict], fps: float, frame_start: int) -> list[dict[str, Any]]:
    """Sentences per voice (one media file and speaker), then all of them in playing order: two
    people who talk over each other are never woven into one sentence."""
    if not merged:
        return []
    streams: dict[tuple[str, str], list[dict]] = {}
    for o in merged:
        streams.setdefault((o["media"], o["speaker"]), []).append(o)
    parts: list[list[dict]] = []
    for voice in streams.values():
        flat = [{"w": o["text"], "start": o["frame"] / fps, "end": o["end_frame"] / fps} for o in voice]
        occs = [{"i": k, "frame": o["frame"], "end_frame": o["end_frame"], "t": (o["frame"] - frame_start) / fps}
                for k, o in enumerate(voice)]
        for line in te.render(occs, flat, None, fps)["lines"]:
            parts.extend(_split_long([voice[k] for k in line["words"]], fps))
    parts.sort(key=lambda part: (part[0]["frame"], part[0]["media"], part[0]["i"]))
    out: list[dict[str, Any]] = []
    prev: dict | None = None
    for n, part in enumerate(parts, 1):
        first, last = part[0], max(part, key=lambda o: o["end_frame"])
        start = int(first["frame"])
        end = max(start + 1, int(round(last["end_frame"])))
        text = " ".join(o["text"] for o in part)
        seconds = max(0.1, (end - start) / fps)
        speaker = first["speaker"]
        fillers = sum(1 for o in part if te.norm(o["text"]) in te.FILLERS)
        out.append({
            "n": n, "text": text, "start": start, "end": end,
            "t": round((start - frame_start) / fps, 2), "seconds": round(seconds, 2),
            "words": len(part), "wps": round(len(part) / seconds, 2), "fillers": fillers,
            "pause_before": round(max(0.0, (start - prev["end"]) / fps), 2) if prev else 0.0,
            "question": text.rstrip(te._CLOSERS).endswith(_QUESTION_END),   # pylint: disable=protected-access
            "number": bool(_NUMBER.search(text)),
            "speaker": speaker,
            "speaker_change": bool(prev and speaker and prev["speaker"] and speaker != prev["speaker"]),
            "first": {"media": first["media"], "i": first["i"]},
            "last": {"media": last["media"], "i": last["i"]},
            "media": sorted({o["media"] for o in part}),
        })
        prev = out[-1]
    return out


# -- the scene -----------------------------------------------------------------------------------

def _frames(piece: dict) -> int:
    return max(0, int(piece["right"]) - int(piece["left"]))


def read_scene(sources: Iterable[dict], fps: float, frame_start: int = 1) -> dict[str, Any]:
    """The edit as numbered sentences: ``{"sentences", "report"}`` (see the module's text)."""
    fps = _fps(fps)
    sources = list(sources)
    skipped = {k: {"strips": 0, "frames": 0, "names": []} for k in ("muted", "retimed", "meta", "no_transcript")}
    spoken_frames = 0
    unsupported = 0
    active: list[tuple[dict, list[dict]]] = []
    for src in sources:
        use: list[dict] = []
        for piece in src.get("pieces") or []:
            reason = piece.get("skip") or ""
            if reason not in skipped and reason:
                reason = ""
            if reason == "muted":
                bucket = skipped["muted"]
            else:
                spoken_frames += _frames(piece)
                if reason:
                    unsupported += _frames(piece)
                    bucket = skipped[reason]
                elif src.get("words") is None:
                    bucket = skipped["no_transcript"]
                else:
                    use.append(piece)
                    continue
            bucket["strips"] += 1
            bucket["frames"] += _frames(piece)
            bucket["names"].append(piece["name"])
        if use:
            active.append((src, use))
    rank: dict[str, int] = {}
    ordered = sorted((p for _s, ps in active for p in ps), key=lambda p: (p["left"], p.get("channel", 0), p["name"]))
    for k, p in enumerate(ordered):
        rank[p["name"]] = k
    per_piece: dict[str, list[dict]] = {p["name"]: [] for p in ordered}
    for src, use in active:
        for o in _place(src, use, fps, frame_start):
            per_piece[o["piece"]].append(o)
    for occs in per_piece.values():
        occs.sort(key=lambda o: (o["frame"], o["i"]))
    per_piece, dropped = _dedupe(per_piece, rank, fps)
    merged = sorted((o for occs in per_piece.values() for o in occs),
                    key=lambda o: (o["frame"], rank[o["piece"]], o["i"]))
    sentences = _sentences(merged, fps, frame_start)
    speech_s = sum(s["seconds"] for s in sentences)
    report = {
        "sentences": len(sentences), "words": len(merged), "speech_seconds": round(speech_s, 1),
        "skipped": {k: v for k, v in skipped.items() if v["strips"]},
        "unsupported_share": round(unsupported / spoken_frames, 3) if spoken_frames else 0.0,
        "unsupported_seconds": round(unsupported / fps, 1),
        "duplicates": dropped,
    }
    report["messages"] = _messages(report, fps)
    return {"sentences": sentences, "report": report}


def _plural(n: int, one: str, many: str = "") -> str:
    return "{:d} {:s}".format(n, one if n == 1 else (many or one + "s"))


def _messages(report: dict, fps: float) -> list[str]:
    out: list[str] = []
    sk = report["skipped"]
    for key, what in (("retimed", "changed in speed"), ("meta", "inside meta strips")):
        if key in sk:
            out.append("Skipped {:s} {:s}: {:.0f} s of sound, in this version.".format(
                _plural(sk[key]["strips"], "strip"), what, sk[key]["frames"] / fps))
    if "retimed" in sk or "meta" in sk:
        out.append("Together that is {:.0f}% of the edit's sound.".format(100.0 * report["unsupported_share"]))
    if "muted" in sk:
        out.append("Skipped {:s} (muted).".format(_plural(sk["muted"]["strips"], "strip")))
    if "no_transcript" in sk:
        out.append("{:s} without a transcript ({:.0f} s): transcribe them first.".format(
            _plural(sk["no_transcript"]["strips"], "strip"), sk["no_transcript"]["frames"] / fps))
    for d in report["duplicates"][:MAX_DUPLICATE_LINES]:
        out.append("The same speech was on “{:s}” and “{:s}”: kept “{:s}” (higher word confidence), "
                   "left out {:s}.".format(d["kept"], d["dropped"], d["kept"], _plural(d["words"], "word")))
    more = len(report["duplicates"]) - MAX_DUPLICATE_LINES
    if more > 0:
        out.append("... and the same on {:s} more.".format(_plural(more, "other pair")))
    if not report["sentences"]:
        out.append("No speech to read.")
    return out


# -- showing the sentences -------------------------------------------------------------------------

def format_sentence(s: dict) -> str:
    """``S12 [0:42.1 5.2s 2.9w/s] text``, with short hints after the numbers (``pause 1.4s``,
    ``?``, ``#`` for a number, ``fillers 2``, ``speaker Ana``)."""
    hints = ["{:.1f}s".format(s["seconds"]), "{:.1f}w/s".format(s["wps"])]
    if s["pause_before"] >= 0.5:
        hints.append("pause {:.1f}s".format(s["pause_before"]))
    if s["question"]:
        hints.append("?")
    if s["number"]:
        hints.append("#")
    if s["fillers"]:
        hints.append("fillers {:d}".format(s["fillers"]))
    if s["speaker"] and s["speaker_change"]:
        hints.append("speaker " + s["speaker"])
    return "S{:d} [{:s} {:s}] {:s}".format(s["n"], te.format_mark(s["t"]).strip("[]"), " ".join(hints), s["text"])


def format_sentences(sentences: Iterable[dict]) -> str:
    return "\n".join(format_sentence(s) for s in sentences)


def version(sentences: Sequence[dict]) -> str:
    """A short hash of the sentences, so a later call can tell the edit changed."""
    body = json.dumps([(s["n"], s["text"], s["start"], s["end"]) for s in sentences])
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]


# -- windows ---------------------------------------------------------------------------------------

def estimate_tokens(words: int, sentences: int) -> int:
    """What *words* in *sentences* cost in a request, with their numbers and hints."""
    return int(words * TOKENS_PER_WORD + sentences * TOKENS_PER_SENTENCE) + 1


def window_budget(context: int | None) -> int:
    """The tokens of sentences one request may hold on a context of *context* tokens (the
    default 32K when unknown): the instructions and the room for the answer come off first."""
    ctx = int(context) if context else DEFAULT_CONTEXT
    return max(0, int((ctx - INSTRUCTION_TOKENS - ANSWER_TOKENS) * FIT))


def windows(sentences: Sequence[dict], context: int | None = None, max_words: int = MAX_WINDOW_WORDS,
            overlap_s: float = OVERLAP_S) -> list[dict[str, Any]]:
    """The sentences in overlapping windows that each fit one request on *context* tokens:
    ``[{"index", "first", "last", "words", "sentences", "tokens", "t0", "t1"}]`` (*first* / *last*:
    sentence numbers, ``n``).  A window holds at most *max_words* words and the token budget of the
    context; the next one starts *overlap_s* of speech before the end of the one before (always at
    least one sentence further on).  A context too small for :data:`MIN_WINDOW_WORDS` gives no window."""
    budget = window_budget(context)
    if budget < estimate_tokens(MIN_WINDOW_WORDS, MIN_WINDOW_WORDS // 15) or not sentences:
        return []
    out: list[dict[str, Any]] = []
    start, n = 0, len(sentences)
    while start < n:
        words = 0
        end = start
        while end < n:
            s = sentences[end]
            if end > start and (words + s["words"] > max_words
                                or estimate_tokens(words + s["words"], end - start + 1) > budget):
                break
            words += s["words"]
            end += 1
        out.append({"index": len(out) + 1, "first": sentences[start]["n"], "last": sentences[end - 1]["n"],
                    "words": words, "sentences": end - start, "tokens": estimate_tokens(words, end - start),
                    "t0": sentences[start]["t"], "t1": round(sentences[end - 1]["t"] + sentences[end - 1]["seconds"], 2)})
        if end >= n:
            break
        limit = sentences[end - 1]["t"] + sentences[end - 1]["seconds"] - overlap_s
        nxt = end
        while nxt - 1 > start and sentences[nxt - 1]["t"] >= limit:
            nxt -= 1
        start = max(start + 1, nxt)
    return out
