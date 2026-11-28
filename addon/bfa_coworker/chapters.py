# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Make Chapters (#542): a chapter list from the edit's transcript, for the description of a video and for
markers on the timeline.  The part that needs no Blender.

The edit is read as numbered sentences by the scene reader (#611).  The model sees them and answers with
*sentence numbers and titles*, never a time (:func:`chapter_schema`); the code then checks the answer
(:func:`check`) and turns the numbers into frames:

1. every number exists in the part the model was shown, and numbers go up (an out-of-order or repeated one is
   dropped and counted);
2. the first chapter starts at the start of the edit (0:00, what video sites need): a chapter that starts in the
   first seconds is moved there, else an "Introduction" is put in front;
3. no chapter is shorter than the minimum (10 s: what YouTube asks); a chapter too close to the one before is
   dropped, and a short last one is dropped, so the one before runs to the end;
4. with a wanted number (*about* N), the chapters closest together are dropped until there are no more than
   one and a half times N.

The result is :func:`youtube_text` (``0:00 Title`` per line, the first line ``0:00``) and the markers' names
(:func:`marker_name`, ``CH1: Title``): Coworker's own chapter markers are recognised by what the scene stores
(:func:`marker_plan`) and by the ``CH`` prefix, so they can be made again or removed without touching the artist's
markers.

The model is reached through an *ask* function (``ask_json.make_asker``); a part of the edit that cannot be read
(:class:`ask_json.AskFailed` after its one retry) is skipped and counted, an :class:`ask_json.Unavailable` goes up.

Importing this module needs no ``bpy``.
"""

from __future__ import annotations

__all__ = (
    "MARKER_PREFIX",
    "MIN_SECONDS",
    "PROP",
    "chapter_schema",
    "check",
    "dumps",
    "find_chapters",
    "format_time",
    "loads",
    "marker_name",
    "marker_plan",
    "youtube_text",
)

import json
import math
import re
from typing import Any, Callable, Mapping, Sequence

from . import ask_json as aj
from . import scene_reader as sr

PROP = "bfacw_chapters"           # the scene's custom property: what Make Chapters made (names, text, list)
MARKER_PREFIX = "CH"
MIN_SECONDS = 10.0
TITLE_CHARS = 60
MARKER_CHARS = 56                 # a marker's name is at most 63 characters
OVER_FACTOR = 1.5                 # more than this many times the wanted number: the closest ones go
ANSWER_TOKENS = 700
MIN_FOR_SITES = 3                 # YouTube needs at least three chapters
VERSION = 1

_PROMPT = (
    "You are helping to split a video into chapters for its description. Below is the transcript of the video "
    "(or of one part of it). Each line starts with a sentence number such as S214, then the time and a few hints "
    "in brackets, then the sentence.\n\n"
    "Name the sentences where a new topic or step begins. Answer with the number of that sentence and a title of "
    "two to six words that says what the chapter is about, in the words of the video, in the language of the "
    "transcript, with no numbering and no quotation marks.\n"
    "Use only the numbers shown, in increasing order, and never give times. {how_many}"
    "A chapter is at least {min_seconds} seconds long. Do not make a chapter of a single remark or an aside. "
    "{position}"
)


# -- the answer -------------------------------------------------------------------------------------

def chapter_schema(limit: int = 40) -> dict[str, Any]:
    """What the request answers: ``{"chapters": [{"sentence", "title"}]}``.  Extra fields are ignored."""
    return {"type": "object", "required": ["chapters"], "properties": {
        "chapters": {"type": "array", "maxItems": max(1, int(limit)), "items": {
            "type": "object", "required": ["sentence", "title"], "properties": {
                "sentence": {"type": "integer", "minimum": 1},
                "title": {"type": "string"}}}}}}


def _title(text: Any, number: int) -> str:
    s = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(text or ""))).strip()
    s = s.strip("\"'“”‘’`*#").strip()
    s = re.sub(r"^(?:chapter\s*)?\d+\s*[.):\-–]\s*", "", s, flags=re.I).rstrip(" .:;,-")
    if len(s) > TITLE_CHARS:
        s = (s[:TITLE_CHARS].rsplit(" ", 1)[0] if " " in s[:TITLE_CHARS] else s[:TITLE_CHARS]).rstrip(" ,;:-") + "..."
    return s or "Chapter {:d}".format(number)


def check(raw: Any, by_n: Mapping[int, dict], first_n: int, frame_start: int, end_frame: int, fps: float,
          min_seconds: float = MIN_SECONDS, window: tuple[int, int] | None = None,
          stats: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """The chapters of one answer *raw* (the model's list), checked: ``[{"sentence", "title", "frame"}]``.

    *by_n* the sentences by number, *first_n* the first sentence of the edit, *frame_start* the edit's first
    frame (0:00), *end_frame* its end (exclusive), *window* the numbers the model was shown.  What fails a check
    is dropped and counted in *stats* (``not_a_chapter``, ``outside_window``, ``no_such_sentence``,
    ``not_ascending``).  Does not enforce the start, the minimum length or the number: that is :func:`settle`."""
    stats = stats if stats is not None else {}

    def drop(why: str) -> None:
        stats[why] = stats.get(why, 0) + 1
    out: list[dict[str, Any]] = []
    last = 0
    for item in raw if isinstance(raw, list) else []:
        try:
            n = int(item["sentence"])
        except (KeyError, TypeError, ValueError):
            drop("not_a_chapter")
            continue
        if window is not None and not window[0] <= n <= window[1]:
            drop("outside_window")
        elif n not in by_n:
            drop("no_such_sentence")
        elif n <= last:
            drop("not_ascending")
        else:
            last = n
            out.append({"sentence": n, "title": _title(item.get("title"), len(out) + 1), "frame": int(by_n[n]["start"])})
    return out


def settle(chapters: Sequence[dict], by_n: Mapping[int, dict], first_n: int, frame_start: int, end_frame: int,
           fps: float, min_seconds: float = MIN_SECONDS, count: int = 0,
           stats: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """The chapter list of an edit from the checked chapters of all its parts (see the module's text):
    sorted and without repeats, the first at the start, none shorter than *min_seconds*, no more than one and a
    half times *count* (0: no limit).  Titles that repeat are left (a site accepts them)."""
    stats = stats if stats is not None else {}
    min_frames = max(1, int(round(float(min_seconds) * float(fps))))
    rows = sorted(({"sentence": c["sentence"], "title": c["title"], "frame": int(c["frame"])} for c in chapters),
                  key=lambda c: c["sentence"])
    uniq: list[dict[str, Any]] = []
    for c in rows:
        if uniq and c["sentence"] == uniq[-1]["sentence"]:
            continue                                    # the same sentence in two windows
        uniq.append(c)
    out: list[dict[str, Any]] = []
    for c in uniq:
        if out and c["frame"] - out[-1]["frame"] < min_frames:
            stats["too_close"] = stats.get("too_close", 0) + 1
            continue
        out.append(c)
    # the first chapter is 0:00
    if not out or out[0]["frame"] - frame_start >= min_frames:
        out.insert(0, {"sentence": first_n, "title": "Introduction", "frame": int(frame_start)})
        stats["intro_added"] = 1
    elif out[0]["frame"] != frame_start:
        out[0] = dict(out[0], frame=int(frame_start))
        stats["first_moved"] = 1
    if len(out) > 1 and end_frame - out[-1]["frame"] < min_frames:
        out.pop()
        stats["tail_dropped"] = stats.get("tail_dropped", 0) + 1
    if count and len(out) > max(1, int(math.ceil(count * OVER_FACTOR))):
        limit = max(1, int(math.ceil(count * OVER_FACTOR)))
        while len(out) > limit:
            gaps = [(out[i]["frame"] - out[i - 1]["frame"], i) for i in range(1, len(out))]
            _gap, i = min(gaps)
            out.pop(i)
            stats["capped"] = stats.get("capped", 0) + 1
    return out


# -- the texts --------------------------------------------------------------------------------------

def format_time(seconds: float, long: bool = False) -> str:
    """``0:00``, ``12:05`` (``1:02:30`` from an hour on, or with *long*): whole seconds, rounded down, as video sites read them."""
    whole = max(0, int(seconds + 1e-6))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours or long:
        return "{:d}:{:02d}:{:02d}".format(hours, minutes, secs)
    return "{:d}:{:02d}".format(minutes, secs)


def youtube_text(chapters: Sequence[dict], frame_start: int, fps: float) -> str:
    """The list in the form video sites accept, one chapter a line: ``0:00 Title`` (the first line is ``0:00``)."""
    fps = float(fps) if fps else 24.0
    long = any((c["frame"] - frame_start) / fps >= 3600.0 for c in chapters)
    return "\n".join("{:s} {:s}".format(format_time((c["frame"] - frame_start) / fps, long), c["title"])
                     for c in chapters)


def marker_name(index: int, title: str) -> str:
    """The name of chapter *index*'s marker (1 based): ``CH1: Title``."""
    head = "{:s}{:d}: ".format(MARKER_PREFIX, index)
    return head + title[:max(1, MARKER_CHARS - len(head))].rstrip()


def marker_plan(store: Mapping[str, Any], existing: Sequence[tuple[str, int]], chapters: Sequence[dict]
                ) -> tuple[list[tuple[str, int]], list[tuple[str, int]], list[str]]:
    """``(remove, add, names)``: what to do so the scene's markers show *chapters* and only ours change.
    *existing* are the scene's markers as ``(name, frame)``.  *remove* are the ones this scene's store made earlier
    (by name: a marker the artist made is never listed, even when they gave it a ``CH1: x`` name the store did not
    make); *add* the markers to make; *names* the store's new list of names."""
    mine = set(store.get("markers") or ())
    remove = [(n, f) for n, f in existing if n in mine]
    add = [(marker_name(i, c["title"]), int(c["frame"])) for i, c in enumerate(chapters, 1)]
    return remove, add, [n for n, _f in add]


# -- the store on the scene -------------------------------------------------------------------------

def empty_store() -> dict[str, Any]:
    return {"v": VERSION, "chapters": [], "markers": [], "text": ""}


def dumps(store: Mapping[str, Any]) -> str:
    return json.dumps(store, ensure_ascii=False, separators=(",", ":"))


def loads(raw: Any) -> dict[str, Any]:
    """The store from the scene's property; anything wrong gives an empty one (never an error)."""
    try:
        data = json.loads(str(raw or ""))
    except (TypeError, ValueError):
        return empty_store()
    if not isinstance(data, dict) or data.get("v") != VERSION:
        return empty_store()
    out = empty_store()
    chapters = data.get("chapters")
    if isinstance(chapters, list):
        out["chapters"] = [{"sentence": int(c["sentence"]), "title": str(c["title"])[:80], "frame": int(c["frame"])}
                           for c in chapters if isinstance(c, dict) and {"sentence", "title", "frame"} <= set(c)][:200]
    markers = data.get("markers")
    if isinstance(markers, list):
        out["markers"] = [str(m)[:80] for m in markers if isinstance(m, str)][:200]
    out["text"] = str(data.get("text") or "")[:80]
    return out


# -- the run ----------------------------------------------------------------------------------------

def _messages(sentences: Sequence[dict], count: int, share: float, index: int, parts: int,
              min_seconds: float, first: bool) -> list[dict[str, str]]:
    if count:
        mine = max(1, int(round(count * share)))
        how_many = "Give about {:d} chapter{:s} for this part. ".format(mine, "" if mine == 1 else "s")
    else:
        how_many = "Give as many chapters as the topics need, usually one every few minutes, never one just to have one. "
    if parts > 1:
        position = "This is part {:d} of {:d} of the video; only give chapters that begin in this part. ".format(index, parts)
    else:
        position = ""
    if first:
        position += "The video starts at the first sentence shown: it opens the first chapter. "
    prompt = _PROMPT.format(how_many=how_many, min_seconds=int(round(min_seconds)), position=position).strip()
    return [{"role": "system", "content": prompt},
            {"role": "user", "content": "Transcript:\n" + sr.format_sentences(sentences)}]


def find_chapters(sentences: Sequence[dict], fps: float, frame_start: int, end_frame: int,
                  ask: Callable[..., Any], *, count: int = 0, min_seconds: float = MIN_SECONDS,
                  context: int | None = None, should_stop: Callable[[], bool] | None = None,
                  progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """The chapters of an edit.  *sentences* are those of :func:`scene_reader.read_scene`, *ask* the model, *count*
    about how many chapters (0: as many as the topics need), *context* the engine's context size in tokens.

    Returns ``{"chapters": [{"sentence", "title", "frame"}], "report": {...}}``; ``report`` counts what happened
    (parts, parts that could not be read, answers dropped and why, requests) and has ``messages`` for the artist.
    Raises :class:`ask_json.Unavailable`."""
    progress = progress or (lambda _text: None)
    stop = should_stop or (lambda: False)
    fps = sr._fps(fps)           # pylint: disable=protected-access
    by_n = {s["n"]: s for s in sentences}
    stats: dict[str, int] = {}
    report: dict[str, Any] = {"sentences": len(sentences), "requests": 0, "retries": 0, "parts": 0,
                              "parts_failed": 0, "stopped": False}
    messages: list[str] = []
    if not sentences:
        messages.append("No speech to make chapters from.")
        report["messages"] = messages
        return {"chapters": [], "report": report}
    first_n = sentences[0]["n"]
    wins = sr.windows(sentences, context)
    if not wins:
        messages.append("The engine's context is too small for this ({:d} tokens).".format(
            int(context) if context else sr.DEFAULT_CONTEXT))
        report["messages"] = messages
        return {"chapters": [], "report": report}
    report["parts"] = len(wins)
    total_s = sum(s["seconds"] for s in sentences) or 1.0
    found: list[dict[str, Any]] = []
    for w in wins:
        if stop():
            report["stopped"] = True
            break
        progress("Reading part {:d} of {:d}".format(w["index"], len(wins)))
        part = [by_n[n] for n in range(w["first"], w["last"] + 1) if n in by_n]
        share = sum(s["seconds"] for s in part) / total_s
        limit = max(3, int(math.ceil(max(1.0, (w["t1"] - w["t0"]) / 60.0) * 1.5)) + 2)
        report["requests"] += 1
        try:
            result = ask(_messages(part, count, share, w["index"], len(wins), min_seconds, w["index"] == 1),
                         chapter_schema(limit), name="chapters", max_tokens=ANSWER_TOKENS)
        except aj.AskFailed:
            report["requests"] += 1
            report["parts_failed"] += 1
            continue
        if getattr(result, "attempts", 1) > 1:
            report["requests"] += 1
            report["retries"] += 1
        found.extend(check(result.value.get("chapters"), by_n, first_n, frame_start, end_frame, fps, min_seconds,
                           (w["first"], w["last"]), stats))
    chapters = settle(found, by_n, first_n, frame_start, end_frame, fps, min_seconds, count, stats) if found else []
    if not found and not report["parts_failed"] and not report["stopped"]:
        messages.append("The model found no change of topic to make a chapter of.")
    report["dropped"] = {k: v for k, v in stats.items() if k not in ("intro_added", "first_moved")}
    if report["parts_failed"]:
        messages.append("{:d} of {:d} parts could not be read: their chapters are missing.".format(
            report["parts_failed"], len(wins)))
    if report["stopped"]:
        messages.append("Stopped: the list is what was found so far.")
    if stats.get("intro_added"):
        messages.append("The model's first chapter did not start the video: an Introduction was put in front.")
    if chapters and len(chapters) < MIN_FOR_SITES:
        messages.append("Video sites need at least {:d} chapters; this edit got {:d}.".format(MIN_FOR_SITES, len(chapters)))
    report["messages"] = messages
    return {"chapters": chapters, "report": report}
