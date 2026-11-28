# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Highlights H3 (#613): find and rank.  The two model passes, and the checks around them, that turn the
numbered sentences of the scene reader (#611) into a short list of picks.  No Blender needed.

The model never sees or returns a time.  It names sentence numbers; the code turns them into frames.

1. **Find** (per window of sentences, :func:`scene_reader.windows`): the model names up to three
   ranges (first sentence, last sentence, a short label).
2. **Check** (:func:`check_candidate`): the numbers exist inside the window, the range is whole
   sentences, its length is within the chosen length, a range a little too long is trimmed back to
   the last sentence that fits (only if that still ends on a full stop).  Overlapping candidates of
   neighbouring windows are merged (:func:`merge_candidates`).
3. **Rank** (:func:`rank`): the text of the candidates, in groups that fit the engine's context; the
   model scores each 0 to 3 on the criteria and writes a title, a hook line and a reason.  The
   *value density* score is counted in code (:func:`density`).  The total is summed in code
   (:func:`total`) from the weights of the kind of video, so the order can be explained and tuned.

The criteria, the weights per kind of video and both prompts live in one editable chapter,
``skills/video_highlights.md`` (:func:`load_chapter`), written in our own words.

The model is reached through an *ask* function: ``ask(messages, schema, **kw) -> ask_json.Result``
(see :func:`ask_json.make_asker`).  A window that cannot be read (:class:`ask_json.AskFailed` after
its one retry) is skipped and counted.  :class:`ask_json.Unavailable` is not caught: there is no
engine to ask, and the caller decides (hand the job to an External Agent, tell the artist).

Importing this module needs no ``bpy``.
"""

from __future__ import annotations

__all__ = (
    "CHAPTER_NAME",
    "KINDS",
    "ChapterError",
    "check_candidate",
    "density",
    "estimate_requests",
    "find_highlights",
    "find_schema",
    "load_chapter",
    "merge_candidates",
    "parse_chapter",
    "rank",
    "rank_schema",
    "total",
)

import math
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import ask_json as aj
from . import scene_reader as sr

CHAPTER_NAME = "video_highlights.md"
KINDS = ("podcast", "tutorial", "talk")          # "auto" decides one of these
DEFAULT_KIND = "podcast"
MAX_SECONDS = 60.0
MAX_CANDIDATES_PER_WINDOW = 3
OVER_FACTOR = 1.25            # a range up to this much over the length is trimmed back, longer is refused
OVERLAP_MERGE = 0.5           # two candidates sharing this much of the shorter one are the same piece
LABEL_WORDS = 8
TITLE_CHARS = 60
HOOK_CHARS = 110
REASON_CHARS = 220
CANDIDATE_WORDS = 220         # the text of a candidate shown to the ranker (head and tail when longer)
CANDIDATE_HEAD = 140
CANDIDATE_TAIL = 70
MAX_RANK_GROUP = 20
ANSWER_TOKENS_PER_CANDIDATE = 110
OPENING_WORDS = 120           # what the ranker reads of the start of the video when the kind is Auto
MAX_RANK_ROUNDS = 3
FIND_MAX_TOKENS = 400
SCORE_MAX = 3
_FULL_STOP = (".", "!", "?")
_CLOSERS = "\"')]}"


class ChapterError(Exception):
    """The skill chapter is missing a part or has a broken one; the message says which."""


# -- the chapter -----------------------------------------------------------------------------------

def _chapter_path() -> Path:
    return Path(__file__).resolve().parent / "skills" / CHAPTER_NAME


def parse_chapter(text: str) -> dict[str, Any]:
    """The chapter as ``{"criteria": {key: question}, "weights": {kind: {key: int}}, "find_prompt",
    "rank_prompt"}``.  Raises :class:`ChapterError` naming what is wrong."""
    criteria: dict[str, str] = {}
    weights: dict[str, dict[str, int]] = {}
    bodies: dict[str, list[str]] = {}
    h2 = h3 = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("### "):
            h3 = line[4:].strip().lower()
            if h2 == "weights":
                weights.setdefault(h3, {})
            continue
        if line.startswith("## "):
            h2, h3 = line[3:].strip().lower(), ""
            continue
        if line.startswith("# "):
            h2 = h3 = ""
            continue
        item = re.match(r"^\s*[-*]\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$", line)
        if h2 == "criteria" and item:
            criteria[item.group(1).lower()] = item.group(2)
        elif h2 == "weights" and h3 and item:
            try:
                value = int(item.group(2))
            except ValueError as err:
                raise ChapterError("weight '{:s}' of '{:s}' is not a whole number: {:s}".format(
                    item.group(1), h3, item.group(2))) from err
            if not 0 <= value <= 9:
                raise ChapterError("weight '{:s}' of '{:s}' must be 0 to 9, not {:d}".format(item.group(1), h3, value))
            weights[h3][item.group(1).lower()] = value
        elif h2 in ("find prompt", "rank prompt"):
            bodies.setdefault(h2, []).append(line)
    if not criteria:
        raise ChapterError("the chapter has no '## Criteria' list")
    if not [k for k in criteria if k != "density"]:
        raise ChapterError("the chapter has no criterion for the model to score")
    if not weights:
        raise ChapterError("the chapter has no '## Weights' section")
    for kind, table in weights.items():
        missing = [k for k in criteria if k not in table]
        if missing:
            raise ChapterError("weights of '{:s}' miss: {:s}".format(kind, ", ".join(missing)))
        if not any(table[k] for k in criteria):
            raise ChapterError("weights of '{:s}' are all zero".format(kind))
    if DEFAULT_KIND not in weights:
        raise ChapterError("the chapter has no weights for '{:s}'".format(DEFAULT_KIND))
    prompts = {}
    for name in ("find prompt", "rank prompt"):
        body = "\n".join(bodies.get(name, [])).strip()
        if not body:
            raise ChapterError("the chapter has no '## {:s}' text".format(name.title()))
        prompts[name] = body
    if "{criteria}" not in prompts["rank prompt"]:
        raise ChapterError("the Rank prompt must contain {criteria}")
    return {"criteria": criteria, "weights": weights,
            "find_prompt": prompts["find prompt"], "rank_prompt": prompts["rank prompt"]}


def load_chapter(path: str | Path | None = None) -> dict[str, Any]:
    """Read and check the chapter (the packaged one by default)."""
    p = Path(path) if path else _chapter_path()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as err:
        raise ChapterError("cannot read {:s}: {:s}".format(str(p), str(err))) from err
    return parse_chapter(text)


def _model_criteria(chapter: dict) -> list[str]:
    return [k for k in chapter["criteria"] if k != "density"]


def _fill(prompt: str, **values: Any) -> str:
    for key, value in values.items():
        prompt = prompt.replace("{" + key + "}", str(value))
    return prompt


# -- the two answers -------------------------------------------------------------------------------

def find_schema() -> dict[str, Any]:
    """What the Find request answers: ``{"candidates": [{"first", "last", "label"}]}``.  Extra fields
    are ignored; more than three candidates are cut in code (a retry would cost more)."""
    return {"type": "object", "required": ["candidates"], "properties": {
        "candidates": {"type": "array", "items": {
            "type": "object", "required": ["first", "last"], "properties": {
                "first": {"type": "integer", "minimum": 1},
                "last": {"type": "integer", "minimum": 1},
                "label": {"type": "string"}}}}}}


def rank_schema(chapter: dict, letters: Sequence[str]) -> dict[str, Any]:
    """What the Rank request answers, per candidate letter (see the module's text)."""
    scores = {"type": "object", "required": _model_criteria(chapter),
              "properties": {k: {"type": "integer", "minimum": 0, "maximum": SCORE_MAX} for k in _model_criteria(chapter)}}
    return {"type": "object", "required": ["picks"], "properties": {
        "picks": {"type": "array", "items": {
            "type": "object", "required": ["id", "scores"], "properties": {
                "id": {"type": "string", "enum": list(letters)},
                "title": {"type": "string"}, "hook": {"type": "string"}, "reason": {"type": "string"},
                "scores": scores}}},
        "kind": {"type": "string", "enum": list(KINDS)}}}


# -- checking a candidate --------------------------------------------------------------------------

def _clean(text: Any, limit: int) -> str:
    s = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(text or ""))).strip()
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0] if " " in s[:limit] else s[:limit]
    return cut.rstrip(" ,;:-") + "..."


def _words(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split()[:limit])


def _ends_on_full_stop(sentence: dict) -> bool:
    return str(sentence["text"]).rstrip().rstrip(_CLOSERS).endswith(_FULL_STOP)


def _make(by_n: dict[int, dict], first: int, last: int, fps: float, label: str = "") -> dict[str, Any]:
    a, b = by_n[first], by_n[last]
    return {"first": first, "last": last, "start": int(a["start"]), "end": int(b["end"]),
            "seconds": round((b["end"] - a["start"]) / fps, 2), "label": label}


def check_candidate(raw: Any, by_n: dict[int, dict], fps: float, min_s: float, max_s: float,
                    window: tuple[int, int] | None = None) -> tuple[dict[str, Any] | None, str]:
    """``(candidate, "")`` or ``(None, reason)``.  *raw* is one answer of the model
    (``{"first", "last", "label"}``); *by_n* the sentences by their number; *window* the numbers the
    model was shown (an answer outside it was invented)."""
    if not isinstance(raw, dict):
        return None, "not_a_range"
    try:
        first, last = int(raw["first"]), int(raw["last"])
    except (KeyError, TypeError, ValueError):
        return None, "not_a_range"
    if window is not None and not (window[0] <= first and last <= window[1]):
        return None, "outside_window"
    if first not in by_n or last not in by_n:
        return None, "no_such_sentence"
    if first > last:
        return None, "backwards"
    label = _words(_clean(raw.get("label"), 120), LABEL_WORDS)
    cand = _make(by_n, first, last, fps, label)
    if cand["seconds"] > max_s:
        if cand["seconds"] > max_s * OVER_FACTOR:
            return None, "too_long"
        end = last
        while end > first and (by_n[end]["end"] - by_n[first]["start"]) / fps > max_s:
            end -= 1
        if (by_n[end]["end"] - by_n[first]["start"]) / fps > max_s or not _ends_on_full_stop(by_n[end]):
            return None, "too_long"
        cand = _make(by_n, first, end, fps, label)
        cand["trimmed"] = True
    if cand["seconds"] < min_s:
        return None, "too_short"
    return cand, ""


def merge_candidates(cands: Sequence[dict], by_n: dict[int, dict], fps: float, max_s: float) -> list[dict[str, Any]]:
    """Candidates that are the same piece (they share :data:`OVERLAP_MERGE` of the shorter one: what
    neighbouring windows find twice) become one: the union when it still fits *max_s*, else the longer.
    In playing order.  Candidates that share only a little stay apart."""
    work = [dict(c, windows=sorted(set(c.get("windows") or []))) for c in cands]
    work.sort(key=lambda c: (c["start"], c["end"]))
    changed = True
    while changed:
        changed = False
        for i in range(len(work)):
            for j in range(i + 1, len(work)):
                a, b = work[i], work[j]
                overlap = min(a["end"], b["end"]) - max(a["start"], b["start"])
                shorter = max(1, min(a["end"] - a["start"], b["end"] - b["start"]))
                if overlap <= 0 or overlap < OVERLAP_MERGE * shorter:
                    continue
                first, last = min(a["first"], b["first"]), max(a["last"], b["last"])
                longer = a if (a["end"] - a["start"]) >= (b["end"] - b["start"]) else b
                if first in by_n and last in by_n and (by_n[last]["end"] - by_n[first]["start"]) / fps <= max_s:
                    merged = _make(by_n, first, last, fps, longer.get("label", ""))
                else:
                    merged = dict(longer)
                merged["windows"] = sorted(set(a["windows"]) | set(b["windows"]))
                work[i] = merged
                del work[j]
                work.sort(key=lambda c: (c["start"], c["end"]))
                changed = True
                break
            if changed:
                break
    return work


# -- value density (counted, never asked) ------------------------------------------------------------

def _band(value: float, cuts: Sequence[float]) -> int:
    """3, 2, 1 or 0: how many of the ascending *cuts* the *value* is above, counted from the top."""
    for score, cut in zip((3, 2, 1), cuts):
        if value <= cut:
            return score
    return 0


def density(sentences: Sequence[dict]) -> float:
    """Value density 0 to 3 of a run of sentences: few fillers, few pauses, many short quotable lines
    (a sentence of 4 to 16 words, or one with a number).  The mean of the three, to 2 decimals."""
    if not sentences:
        return 0.0
    words = max(1, sum(s["words"] for s in sentences))
    fillers = _band(sum(s["fillers"] for s in sentences) / words, (0.01, 0.03, 0.06))
    seconds = max(0.1, sum(s["seconds"] for s in sentences))
    pauses = sum(s["pause_before"] for s in sentences[1:])
    pause_score = _band(pauses / seconds, (0.05, 0.12, 0.25))
    quotable = sum(1 for s in sentences if 4 <= s["words"] <= 16 or s["number"]) / len(sentences)
    quote_score = 3 if quotable >= 0.5 else 2 if quotable >= 0.3 else 1 if quotable >= 0.15 else 0
    return round((fillers + pause_score + quote_score) / 3.0, 2)


def total(scores: Mapping[str, float], weights: dict[str, int]) -> float:
    """The weighted share of the best possible score, 0 to 100, to one decimal.  A criterion with no
    score counts as 0."""
    wsum = sum(weights.values())
    if wsum <= 0:
        return 0.0
    got = sum(w * float(scores.get(k, 0.0)) for k, w in weights.items())
    return round(100.0 * got / (SCORE_MAX * wsum), 1)


# -- engine sizes ------------------------------------------------------------------------------------

def _candidate_text(sentences: Sequence[dict]) -> str:
    words = " ".join(s["text"] for s in sentences).split()
    if len(words) <= CANDIDATE_WORDS:
        return " ".join(words)
    return " ".join(words[:CANDIDATE_HEAD]) + " [...] " + " ".join(words[-CANDIDATE_TAIL:])


def _candidate_tokens(words: int = CANDIDATE_WORDS) -> int:
    return int(words * sr.TOKENS_PER_WORD) + 60


def rank_group_size(context: int | None) -> int:
    """How many candidates one Rank request holds on *context* tokens (at most
    :data:`MAX_RANK_GROUP`, at least 1): the instructions come off first, and every candidate also
    needs room for its answer."""
    ctx = int(context) if context else sr.DEFAULT_CONTEXT
    room = (ctx - sr.INSTRUCTION_TOKENS - OPENING_WORDS * 2) * sr.FIT
    return int(max(1, min(MAX_RANK_GROUP, room // (_candidate_tokens() + ANSWER_TOKENS_PER_CANDIDATE))))


def estimate_requests(sentences: Sequence[dict], context: int | None = None, count: int = 5,
                      max_candidates: int | None = None) -> dict[str, int]:
    """How many model requests a run needs at most: Find per window, Rank for the most candidates
    (three per window unless *max_candidates* says otherwise).  For the dialog's time estimate."""
    wins = sr.windows(sentences, context)
    cands = max_candidates if max_candidates is not None else MAX_CANDIDATES_PER_WINDOW * len(wins)
    group = rank_group_size(context)
    ranks = 0
    left = cands
    for _ in range(MAX_RANK_ROUNDS):
        if left <= 0:
            break
        groups = max(1, math.ceil(left / group))
        ranks += groups
        if groups == 1:
            break
        left = groups * _keep(count, groups, group)
    return {"find": len(wins), "rank": ranks, "total": len(wins) + ranks}


def _keep(count: int, groups: int, group: int) -> int:
    return max(1, min(group - 1, max(2, math.ceil(2.0 * count / groups))))


# -- the passes --------------------------------------------------------------------------------------

def _find_messages(chapter: dict, window_sentences: Sequence[dict], max_s: float, min_s: float) -> list[dict]:
    prompt = _fill(chapter["find_prompt"], max_seconds=round(max_s), min_seconds=round(min_s))
    return [{"role": "system", "content": prompt},
            {"role": "user", "content": "Transcript part:\n" + sr.format_sentences(window_sentences)}]


def _opening(sentences: Sequence[dict]) -> str:
    out: list[str] = []
    n = 0
    for s in sentences:
        out.append(s["text"])
        n += s["words"]
        if n >= OPENING_WORDS:
            break
    return " ".join(out)


def _rank_messages(chapter: dict, letters: Sequence[str], texts: Sequence[str], kind_known: bool,
                   opening: str) -> list[dict]:
    crit = "\n".join("- {:s}: {:s}".format(k, chapter["criteria"][k]) for k in _model_criteria(chapter))
    if kind_known:
        kind_line = ""
    else:
        kind_line = ("Also say what kind of video this is ({:s}), judging from its opening: {:s}".format(
            ", ".join(KINDS), opening))
    prompt = _fill(chapter["rank_prompt"], criteria=crit, kind_line=kind_line)
    body = "\n\n".join("Clip {:s}:\n{:s}".format(letter, text) for letter, text in zip(letters, texts))
    return [{"role": "system", "content": prompt}, {"role": "user", "content": body}]


def _letter(i: int) -> str:
    return chr(ord("A") + i) if i < 26 else "A{:d}".format(i)


class _Run:
    """The state of one run: the numbers for the report, the stop flag, the progress line."""

    def __init__(self, ask: Callable[..., Any], should_stop: Callable[[], bool] | None,
                 progress: Callable[[str], None] | None) -> None:
        self.ask = ask
        self.should_stop = should_stop or (lambda: False)
        self.progress = progress or (lambda _text: None)
        self.requests = 0
        self.retries = 0
        self.stopped = False

    def stop(self) -> bool:
        if not self.stopped and self.should_stop():
            self.stopped = True
        return self.stopped

    def call(self, messages: list[dict], schema: dict, name: str, max_tokens: int) -> Any:
        """The checked answer, or ``None`` when it could not be read (counted).  An
        :class:`ask_json.Unavailable` goes up."""
        self.requests += 1
        try:
            result = self.ask(messages, schema, name=name, max_tokens=max_tokens)
        except aj.AskFailed:
            self.requests += 1                   # the retry that failed too
            return None
        if getattr(result, "attempts", 1) > 1:
            self.requests += 1
            self.retries += 1
        return result


def _find_pass(run: _Run, chapter: dict, by_n: dict[int, dict], fps: float,
               wins: Sequence[dict], min_s: float, max_s: float, stats: dict) -> list[dict]:
    schema = find_schema()
    found: list[dict] = []
    for w in wins:
        if run.stop():
            break
        run.progress("Reading part {:d} of {:d}".format(w["index"], len(wins)))
        part = [by_n[n] for n in range(w["first"], w["last"] + 1) if n in by_n]
        result = run.call(_find_messages(chapter, part, max_s, min_s), schema, "find", FIND_MAX_TOKENS)
        if result is None:
            stats["windows_failed"] += 1
            continue
        raw = result.value.get("candidates") or []
        for item in raw[:MAX_CANDIDATES_PER_WINDOW]:
            stats["raw"] += 1
            cand, why = check_candidate(item, by_n, fps, min_s, max_s, (w["first"], w["last"]))
            if cand is None:
                stats["rejected"][why] = stats["rejected"].get(why, 0) + 1
                continue
            if cand.get("trimmed"):
                stats["trimmed"] += 1
            cand["windows"] = [w["index"]]
            found.append(cand)
    return found


def _score_group(run: _Run, chapter: dict, group: Sequence[dict], by_n: dict[int, dict], opening: str,
                 kind_known: bool) -> tuple[dict[int, dict] | None, str | None]:
    """The model's answers for one group of candidates, by candidate id, and the kind it named;
    ``(None, None)`` when the request could not be read."""
    letters = [_letter(i) for i in range(len(group))]
    texts = [_candidate_text([by_n[n] for n in range(c["first"], c["last"] + 1) if n in by_n]) for c in group]
    schema = rank_schema(chapter, letters)
    result = run.call(_rank_messages(chapter, letters, texts, kind_known, opening), schema, "rank",
                      200 + ANSWER_TOKENS_PER_CANDIDATE * len(group))
    if result is None:
        return None, None
    answers: dict[int, dict] = {}
    for item in result.value.get("picks") or []:
        key = item.get("id")
        if key in letters and group[letters.index(key)]["id"] not in answers:
            answers[group[letters.index(key)]["id"]] = item
    kind = result.value.get("kind")
    return answers, kind if kind in KINDS else None


def rank(run_or_ask: Any, chapter: dict, cands: Sequence[dict], sentences: Sequence[dict],
         by_n: dict[int, dict], *, kind: str = "auto", count: int = 5, context: int | None = None,
         stats: dict | None = None) -> tuple[list[dict], str]:
    """Rank the candidates: ``(ranked, kind)``, best first, each with ``scores``, ``total``, ``title``,
    ``hook``, ``reason`` and ``unscored``.  Candidates are scored in groups that fit the context; when
    there is more than one group the best of each are scored once more together (scores of different
    requests do not compare), for at most :data:`MAX_RANK_ROUNDS` rounds.  *run_or_ask* is an ask
    function (or the run state of a pipeline)."""
    run = run_or_ask if isinstance(run_or_ask, _Run) else _Run(run_or_ask, None, None)
    stats = stats if stats is not None else {}
    group_size = rank_group_size(context)
    opening = _opening(sentences)
    decided = kind if kind in KINDS else ""
    pool = [dict(c, id=i) for i, c in enumerate(cands)]
    for c in pool:
        c["density"] = density([by_n[n] for n in range(c["first"], c["last"] + 1) if n in by_n])
    known: dict[int, dict] = {}                     # id -> {"answer", "total"} of its latest request
    final = list(pool)
    for _round in range(MAX_RANK_ROUNDS):
        if not pool or run.stop():
            break
        n_groups = max(1, math.ceil(len(pool) / group_size))
        chunks = [sorted(pool[k::n_groups], key=lambda c: c["start"]) for k in range(n_groups)]
        survivors: list[dict] = []
        unprocessed: list[dict] = []
        for ch in chunks:
            if run.stop():
                unprocessed += ch
                continue
            run.progress("Ranking {:d} pieces".format(len(ch)))
            answers, said = _score_group(run, chapter, ch, by_n, opening, bool(decided))
            if answers is None:
                stats["rank_failed"] = stats.get("rank_failed", 0) + 1
                answers = {}
            if not decided and said:
                decided = said
            weights = chapter["weights"].get(decided or said or DEFAULT_KIND) or chapter["weights"][DEFAULT_KIND]
            for c in ch:
                known[c["id"]] = {"answer": answers.get(c["id"]),
                                  "total": _total_of(answers.get(c["id"]), c, weights)}
            ordered = sorted(ch, key=lambda c: (-known[c["id"]]["total"], c["start"]))
            survivors += ordered[:_keep(count, n_groups, group_size)] if n_groups > 1 else ordered
        final = survivors + unprocessed
        if n_groups == 1:
            break
        pool = survivors
    final_kind = decided or DEFAULT_KIND
    weights = chapter["weights"].get(final_kind) or chapter["weights"][DEFAULT_KIND]
    ranked = [_pick(chapter, c, (known.get(c["id"]) or {}).get("answer"), weights, final_kind) for c in final]
    ranked.sort(key=lambda p: (p["unscored"], -p["total"], p["start"]))
    return ranked, final_kind


def _total_of(answer: dict | None, cand: dict, weights: dict[str, int]) -> float:
    scores = dict(((answer or {}).get("scores") or {}), density=cand["density"])
    value = total(scores, weights)
    return value if answer else value - 1000.0           # an unscored piece sorts below every scored one


def _pick(chapter: dict, cand: dict, answer: dict | None, weights: dict[str, int], kind: str) -> dict[str, Any]:
    scores = {k: int(v) for k, v in ((answer or {}).get("scores") or {}).items() if k in chapter["criteria"]}
    scores["density"] = cand["density"]
    label = cand.get("label") or ""
    title = _clean((answer or {}).get("title"), TITLE_CHARS) or _clean(label, TITLE_CHARS)
    return {
        "first": cand["first"], "last": cand["last"], "start": cand["start"], "end": cand["end"],
        "seconds": cand["seconds"], "label": label, "title": title,
        "hook": _clean((answer or {}).get("hook"), HOOK_CHARS),
        "reason": _clean((answer or {}).get("reason"), REASON_CHARS) or ("Not scored by the model." if not answer else ""),
        "scores": scores, "total": total(scores, weights), "kind": kind, "unscored": not answer,
        "windows": list(cand.get("windows") or []), "trimmed": bool(cand.get("trimmed")),
    }


# -- the whole run -----------------------------------------------------------------------------------

def find_highlights(sentences: Sequence[dict], fps: float, ask: Callable[..., Any], *,
                    chapter: dict | None = None, kind: str = "auto", max_seconds: float = MAX_SECONDS,
                    min_seconds: float | None = None, count: int = 5, context: int | None = None,
                    should_stop: Callable[[], bool] | None = None,
                    progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Find and rank the picks of an edit.  *sentences* are those of :func:`scene_reader.read_scene`,
    *ask* the model (see the module's text), *context* the engine's context size in tokens (unknown:
    32K), *max_seconds* the chosen length (15, 30 or 60), *count* how many picks to return (3 to 10).

    Returns ``{"picks": [...], "report": {...}}``.  Each pick: sentence numbers ``first``/``last`` and
    their frames ``start``/``end`` (of the edit as it is now), ``seconds``, ``title``, ``hook``,
    ``reason``, ``scores`` (the criteria and ``density``), ``total`` (0 to 100), ``kind``,
    ``unscored``, plus ``first_word``/``last_word`` (media key and word position in that file's
    transcript: what a stored pick keeps).  No two picks share frames.  The report counts what
    happened (parts that could not be read, candidates refused and why, requests, seconds) and has
    ``messages`` for the artist.  Raises :class:`ChapterError` and :class:`ask_json.Unavailable`."""
    t0 = time.monotonic()
    chapter = chapter or load_chapter()
    fps = sr._fps(fps)           # pylint: disable=protected-access
    max_s = float(max_seconds) if max_seconds and max_seconds > 0 else MAX_SECONDS
    min_s = float(min_seconds) if min_seconds else min(15.0, max_s / 2.0)
    count = max(1, int(count))
    kind = kind if kind in KINDS else "auto"
    stats: dict[str, Any] = {"windows": 0, "windows_failed": 0, "raw": 0, "rejected": {}, "trimmed": 0,
                             "merged": 0, "rank_failed": 0}
    report: dict[str, Any] = {"sentences": len(sentences), "max_seconds": max_s, "min_seconds": min_s,
                              "kind_asked": kind, "context": int(context) if context else sr.DEFAULT_CONTEXT}
    messages: list[str] = []
    by_n = {s["n"]: s for s in sentences}
    speech = sum(s["seconds"] for s in sentences)
    run = _Run(ask, should_stop, progress)

    def finish(picks: list[dict], kind_used: str = "") -> dict[str, Any]:
        report.update(stats)
        report.update({"requests": run.requests, "retries": run.retries, "stopped": run.stopped,
                       "kind": kind_used, "seconds_taken": round(time.monotonic() - t0, 2),
                       "picks": len(picks)})
        if stats["windows_failed"]:
            messages.append("{:d} of {:d} parts could not be read.".format(stats["windows_failed"], stats["windows"]))
        if stats["rank_failed"]:
            messages.append("{:d} ranking request(s) could not be read; those pieces are listed unscored.".format(
                stats["rank_failed"]))
        if run.stopped:
            messages.append("Stopped: the list is what was found so far.")
        report["messages"] = messages
        return {"picks": picks, "report": report}

    if not sentences or speech < min_s:
        messages.append("Not enough speech to pick from.")
        return finish([])
    wins = sr.windows(sentences, context)
    if not wins:
        messages.append("The engine's context is too small for this ({:d} tokens).".format(report["context"]))
        return finish([])
    stats["windows"] = len(wins)
    found = _find_pass(run, chapter, by_n, fps, wins, min_s, max_s, stats)
    cands = merge_candidates(found, by_n, fps, max_s)
    stats["merged"] = len(found) - len(cands)
    if not cands:
        if not run.stopped and not stats["windows_failed"]:
            messages.append("Nothing in this edit stands on its own as a short of this length.")
        return finish([])
    ranked, kind_used = rank(run, chapter, cands, sentences, by_n, kind=kind, count=count, context=context,
                             stats=stats)
    picks: list[dict] = []
    for p in ranked:
        if any(min(p["end"], q["end"]) > max(p["start"], q["start"]) for q in picks):
            continue                                         # no two shorts share the same frames
        a, b = by_n[p["first"]], by_n[p["last"]]
        p["first_word"], p["last_word"] = dict(a["first"]), dict(b["last"])
        p["t"] = a["t"]
        picks.append(p)
        if len(picks) >= count:
            break
    return finish(picks, kind_used)
