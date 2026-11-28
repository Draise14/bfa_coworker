# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Highlights H6 (#616): what the agent's tools need that does not need Blender.

* :func:`check_proposals`: the picks an agent read out of the sentences itself (``read_edit_sentences``,
  then ``propose_highlights``) go through **the same checks** as the model's picks in the search: the
  numbers exist, the range is whole sentences, its length is within the chosen one, a range a little too
  long is trimmed back to a full stop, no two picks share frames.  The scores the agent gives are whole
  numbers 0 to 3 on the criteria of the chapter; the value density is counted here, never taken from the
  agent; the total is added up from the weights of the kind of video.  A refused range is listed with
  the reason, so the agent can fix it.
* :func:`parse_steps`, :func:`resolve_pick`: the arguments of ``make_short``.
* :func:`prepared_message`: what the panel's button sends an External Agent (or puts on the clipboard for an
  External Harness), a message that uses these tools.
* :func:`rows`: the picks as the tools answer them.

Importing this module needs no ``bpy``.
"""

from __future__ import annotations

__all__ = (
    "MAX_PROPOSALS",
    "REFUSED_TEXT",
    "STEP_NAMES",
    "check_proposals",
    "parse_steps",
    "prepared_message",
    "resolve_pick",
    "rows",
)

import math
from typing import Any, Sequence

from . import highlight_picks as hp
from . import highlights as hl

MAX_PROPOSALS = 20
STEP_NAMES = ("silences", "vertical", "captions", "hook")
REFUSED_TEXT = {
    "not_a_range": "needs whole numbers `first` and `last`",
    "outside_window": "outside the sentences shown",
    "no_such_sentence": "no sentence has that number (read the edit again: numbers change after a cut)",
    "backwards": "`first` is after `last`",
    "too_long": "longer than the length allowed, and it cannot be cut back to a full stop",
    "too_short": "too short to stand alone as a short",
    "overlaps": "shares frames with another pick",
    "too_many": "more than {:d} picks in one call".format(MAX_PROPOSALS),
}

_KIND_TEXT = {
    "auto": "Decide from the first minutes what kind of video it is.",
    "podcast": "It is a podcast or interview: an exchange between speakers or a story works best.",
    "tutorial": "It is a tutorial: one tip with its result works best.",
    "talk": "It is a talk or lecture: a claim and its proof works best.",
}


def _scores(raw: Any, criteria: Sequence[str]) -> dict[str, int]:
    """The agent's scores that are whole numbers 0 to 3 on a criterion of the chapter (the rest is dropped)."""
    out: dict[str, int] = {}
    if not isinstance(raw, dict):
        return out
    for key in criteria:
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        out[key] = max(0, min(hl.SCORE_MAX, int(round(value))))
    return out


def check_proposals(raw: Any, sentences: Sequence[dict], fps: float, chapter: dict, *, kind: str = "auto",
                    max_seconds: float = hl.MAX_SECONDS,
                    taken: Sequence[tuple[int, int]] = ()) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(picks, refused)`` for the *raw* proposals (a list of ``{"first", "last", "title", "hook", "reason",
    "scores"}``; sentence numbers of *sentences*).  *picks* are in the form ``find_highlights`` returns (best first),
    ready for :func:`highlight_picks.from_found`; each refused one is ``{"index", "first", "last", "why", "message"}``.
    *taken*: frame ranges already in the list (a proposal sharing frames with one is refused)."""
    refused: list[dict[str, Any]] = []
    if not isinstance(raw, (list, tuple)):
        return [], [{"index": 0, "first": None, "last": None, "why": "not_a_range",
                     "message": "`picks` must be a list of {first, last, title, hook, reason, scores}"}]
    fps = fps if fps and fps > 0 else 24.0
    max_s = float(max_seconds) if max_seconds and max_seconds > 0 else hl.MAX_SECONDS
    max_s = min(max_s, hl.MAX_SECONDS)
    min_s = min(15.0, max_s / 2.0)
    kind_used = kind if kind in hl.KINDS else hl.DEFAULT_KIND
    weights = chapter["weights"].get(kind_used) or chapter["weights"][hl.DEFAULT_KIND]
    criteria = hl._model_criteria(chapter)      # pylint: disable=protected-access
    by_n = {s["n"]: s for s in sentences}
    made: list[dict[str, Any]] = []
    spans = [(int(a), int(b)) for a, b in taken]

    def refuse(i: int, item: Any, why: str) -> None:
        first = item.get("first") if isinstance(item, dict) else None
        last = item.get("last") if isinstance(item, dict) else None
        refused.append({"index": i, "first": first, "last": last, "why": why, "message": REFUSED_TEXT.get(why, why)})

    for i, item in enumerate(raw):
        if i >= MAX_PROPOSALS:
            refused.append({"index": i, "first": None, "last": None, "why": "too_many",
                            "message": "{:d} more picks past the first {:d} were left out: {:s}".format(
                                len(raw) - MAX_PROPOSALS, MAX_PROPOSALS, REFUSED_TEXT["too_many"])})
            break
        title = item.get("title") if isinstance(item, dict) else ""
        cand, why = hl.check_candidate({"first": item.get("first"), "last": item.get("last"), "label": title}
                                       if isinstance(item, dict) else item, by_n, fps, min_s, max_s)
        if cand is None:
            refuse(i, item, why)
            continue
        if any(min(cand["end"], b) > max(cand["start"], a) for a, b in spans):
            refuse(i, item, "overlaps")
            continue
        run = [s for s in sentences if cand["first"] <= s["n"] <= cand["last"]]
        cand["density"] = hl.density(run)
        scores = _scores(item.get("scores"), criteria)
        answer = {"title": item.get("title"), "hook": item.get("hook"), "reason": item.get("reason"),
                  "scores": scores}
        pick = hl._pick(chapter, cand, answer, weights, kind_used)      # pylint: disable=protected-access
        pick["unscored"] = not scores
        if not scores:
            pick["total"] = 0.0
            pick["reason"] = pick["reason"] or "Not scored by the agent."
        a, b = by_n[pick["first"]], by_n[pick["last"]]
        pick["first_word"], pick["last_word"] = dict(a["first"]), dict(b["last"])
        pick["t"] = a["t"]
        spans.append((pick["start"], pick["end"]))
        made.append(pick)
    made.sort(key=lambda p: (p["unscored"], -p["total"], p["start"]))
    return made, refused


def parse_steps(steps: Any) -> tuple[dict[str, bool] | None, str]:
    """``(steps, "")`` or ``(None, why)``.  *steps* is ``None`` (every step), a list of the names to run
    (``[]``: none, only the scene copy with its range), or ``{name: bool}``; names are :data:`STEP_NAMES`."""
    if steps is None:
        return {k: True for k in STEP_NAMES}, ""
    if isinstance(steps, str):
        steps = [part for part in steps.replace(",", " ").split() if part]
    if isinstance(steps, dict):
        unknown = [str(k) for k in steps if k not in STEP_NAMES]
        if unknown:
            return None, "Unknown step {:s}. The steps are {:s}.".format(", ".join(unknown), ", ".join(STEP_NAMES))
        return {k: bool(steps.get(k, False)) for k in STEP_NAMES}, ""
    if isinstance(steps, (list, tuple)):
        names = [str(x).strip().lower() for x in steps]
        unknown = [n for n in names if n not in STEP_NAMES]
        if unknown:
            return None, "Unknown step {:s}. The steps are {:s}.".format(", ".join(unknown), ", ".join(STEP_NAMES))
        return {k: k in names for k in STEP_NAMES}, ""
    return None, "`steps` is a list of step names ({:s}), or left out for all of them.".format(", ".join(STEP_NAMES))


def resolve_pick(picks: Sequence[dict[str, Any]], ref: Any) -> tuple[dict[str, Any] | None, str]:
    """The stored pick *ref* names: its id, else its place in the list (1 is the first), else its title
    (one match, any case).  ``(None, why)`` when there is none or it is not clear."""
    if not picks:
        return None, "There are no picks yet: call suggest_highlights (or propose_highlights) first."
    text = str(ref if ref is not None else "").strip()
    if not text:
        return None, "Say which pick: its `id` from the list ({:s}).".format(", ".join(p["id"] for p in picks[:6]))
    for p in picks:
        if p["id"] == text:
            return p, ""
    if text.isdigit() and 1 <= int(text) <= len(picks):
        return picks[int(text) - 1], ""
    same = [p for p in picks if p["title"].strip().lower() == text.lower()]
    if len(same) == 1:
        return same[0], ""
    return None, "No pick '{:s}'. The picks are {:s}.".format(
        text[:40], "; ".join("{:s} ({:s})".format(p["id"], p["title"][:30]) for p in picks[:6]))


def rows(store: dict[str, Any], fps: float, frame_start: int = 1) -> list[dict[str, Any]]:
    """The picks as the tools answer them: place, id, title, hook, reason, how long, where, score, state."""
    out = []
    for n, p in enumerate(store["picks"], 1):
        out.append({"n": n, "id": p["id"], "title": p["title"], "hook": p["hook"], "reason": p["reason"],
                    "seconds": round(float(p["seconds"]), 1), "at": hp.clock(p["start"] - frame_start, fps),
                    "total": p["total"], "scores": dict(p["scores"]), "kind": p["kind"], "state": p["state"]})
    return out


def prepared_message(kind: str, seconds: float, count: int) -> str:
    """The message the panel's Find button hands an External Agent: it uses the chat tools, which is the
    search with the agent's own model."""
    return (
        "Find up to {count} pieces of this edit that would stand alone as a short of at most {seconds:.0f} seconds. "
        "Read the speech with read_edit_sentences (every page), choose ranges that start and end on whole "
        "sentences and make sense without the rest of the video, and send them with propose_highlights "
        "(the first and last sentence number, a title, a hook line, one sentence of reason, and scores 0 to 3 "
        "for hook, alone, complete, one_idea, energy and tease). {kind} "
        "Then tell me what you found and why. Do not build a short yet: I will press Make Short on the one I like."
    ).format(count=int(count), seconds=float(seconds), kind=_KIND_TEXT.get(kind, _KIND_TEXT["auto"]))
